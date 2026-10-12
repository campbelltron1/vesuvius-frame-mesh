#!/usr/bin/env python3
"""Resolve the public mesh built in a requested surface volume's CT frame.

The selection is deliberately conservative: a mesh must encode the target
volume id in its directory name and reproduce both the declared and stored
surface-volume canvas.  Ink, CT pixels, labels, and mesh resolution are never
ranking signals.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import urllib.parse
from pathlib import Path
from typing import Sequence

from canvas_provenance import (
    BUCKET_URL,
    PublicS3,
    discover_mesh_dirs,
    rendered_shape,
    tiff_shape,
    volume_id,
)


EXIT_RESOLVED = 0
EXIT_MISSING = 2
EXIT_AMBIGUOUS = 3
EXIT_INVALID_METADATA = 4


def _bucket_key(value: str, base_url: str) -> str:
    """Return a normalized public-bucket key from a key or HTTP URL."""

    parsed = urllib.parse.urlparse(value)
    if parsed.scheme:
        base = urllib.parse.urlparse(base_url)
        same_host = parsed.netloc == base.netloc
        source_bucket = parsed.netloc.split(".s3", 1)[0]
        configured_bucket = base.netloc.split(".s3", 1)[0]
        same_s3_bucket = (
            ".s3" in parsed.netloc
            and ".s3" in base.netloc
            and source_bucket == configured_bucket
        )
        if parsed.scheme not in {"http", "https"} or not (
            same_host or same_s3_bucket
        ):
            raise ValueError(f"URL is outside configured bucket: {value}")
        return urllib.parse.unquote(parsed.path).lstrip("/").rstrip("/")
    return value.strip().lstrip("/").rstrip("/")


def normalize_segment(value: str, base_url: str = BUCKET_URL) -> str:
    key = _bucket_key(value, base_url)
    marker = "/surface-volumes/"
    if marker in key:
        key = key.split(marker, 1)[0]
    if key.endswith("/surface-volumes"):
        key = key.removesuffix("/surface-volumes")
    if key.endswith("/mesh"):
        key = key.removesuffix("/mesh")
    parts = key.split("/")
    if len(parts) != 3 or parts[1] != "segments" or not all(parts):
        raise ValueError(
            "segment must look like '<scroll>/segments/<segment-name>'"
        )
    return key


def normalize_surface_volume(
    value: str, segment: str, base_url: str = BUCKET_URL
) -> tuple[str, str]:
    key = _bucket_key(value, base_url)
    if "/" not in key:
        name = key
        key = f"{segment}/surface-volumes/{name}"
    else:
        expected = f"{segment}/surface-volumes/"
        if not key.startswith(expected):
            raise ValueError(
                "surface volume does not belong to the requested segment"
            )
        name = key.removeprefix(expected)
        if "/" in name:
            raise ValueError("surface volume must identify one Zarr group")
    if not name.endswith(".zarr"):
        raise ValueError("surface volume must end in .zarr")
    return key, name


def _scale_pair(meta: dict) -> tuple[float, float]:
    scale = meta.get("scale")
    if isinstance(scale, (int, float)) and not isinstance(scale, bool):
        scale = [scale, scale]
    if not isinstance(scale, list) or len(scale) != 2:
        raise ValueError("mesh meta.json lacks a two-element scale")
    result: list[float] = []
    for value in scale:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("mesh scale values must be numeric")
        parsed = float(value)
        if not math.isfinite(parsed) or parsed <= 0:
            raise ValueError(f"invalid mesh scale: {scale}")
        result.append(parsed)
    return result[0], result[1]


def _positive_int_pair(value: object, label: str) -> tuple[int, int]:
    if (
        not isinstance(value, list)
        or len(value) != 2
        or any(isinstance(item, bool) or not isinstance(item, int) for item in value)
        or any(item <= 0 for item in value)
    ):
        raise ValueError(f"{label} must be two positive integers")
    return value[0], value[1]


def _positive_shape(value: object) -> list[int]:
    if (
        not isinstance(value, list)
        or len(value) < 2
        or any(isinstance(item, bool) or not isinstance(item, int) for item in value)
        or any(item <= 0 for item in value)
    ):
        raise ValueError("level-0 .zarray shape must contain positive integers")
    return value


def _source_group(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("source_group must be an integer in [0, 63]")
    if value < 0 or value > 63:
        raise ValueError("source_group must be in [0, 63]")
    return value


def _surface_metadata(client: PublicS3, directory: str) -> dict:
    attrs = client.json(f"{directory}/.zattrs")
    array_meta = client.json(f"{directory}/0/.zarray")
    canvas_x, canvas_y = _positive_int_pair(
        attrs.get("canvas_size"), "surface canvas_size"
    )
    declared = (canvas_y, canvas_x)
    shape = _positive_shape(array_meta.get("shape"))
    stored = (shape[-2], shape[-1])
    if declared != stored:
        raise ValueError(
            f"declared canvas {declared} != level-0 array canvas {stored}"
        )
    source_group = _source_group(attrs.get("source_group", 0))
    return {
        "declared_canvas": list(declared),
        "level0_shape": shape,
        "source_group": source_group,
        "ds_scale": math.ldexp(1.0, -source_group),
    }


def resolve_volume_frame_mesh(
    client: PublicS3,
    segment_value: str,
    surface_value: str,
    *,
    mesh_directory_cache: dict[str, list[str]] | None = None,
) -> dict:
    """Return a machine-readable resolution or safe abstention."""

    try:
        segment = normalize_segment(segment_value, client.base_url)
        surface_dir, surface_name = normalize_surface_volume(
            surface_value, segment, client.base_url
        )
        target_id = volume_id(surface_name)
        if target_id is None:
            raise ValueError("surface-volume name has no 14-digit volume id")
        surface = _surface_metadata(client, surface_dir)
    except Exception as exc:
        return {
            "schema_version": 1,
            "status": "invalid_metadata",
            "selection_uses_labels": False,
            "error": f"{type(exc).__name__}: {exc}",
        }

    mesh_root = f"{segment}/mesh/"
    try:
        if mesh_directory_cache is None:
            directories = discover_mesh_dirs(client, segment)
        elif segment in mesh_directory_cache:
            directories = mesh_directory_cache[segment]
        else:
            directories = discover_mesh_dirs(client, segment)
            mesh_directory_cache[segment] = directories
    except Exception as exc:
        return {
            "schema_version": 1,
            "status": "invalid_metadata",
            "selection_uses_labels": False,
            "segment": segment,
            "surface_volume": surface_name,
            "volume_id": target_id,
            "surface": surface,
            "error": f"{type(exc).__name__}: {exc}",
        }

    named = [
        directory
        for directory in directories
        if f"-on-{target_id}-" in directory.removeprefix(mesh_root)
    ]
    candidates: list[dict] = []
    candidate_errors = 0
    target_canvas = tuple(surface["declared_canvas"])
    for directory in sorted(named):
        relative = directory.removeprefix(mesh_root)
        try:
            meta = client.json(f"{directory}/meta.json")
            scale = _scale_pair(meta)
            stored = _positive_int_pair(
                list(tiff_shape(client, f"{directory}/x.tif")),
                "mesh TIFF shape",
            )
            predicted = rendered_shape(stored, scale, surface["ds_scale"])
            candidates.append(
                {
                    "mesh": relative,
                    "mesh_key": directory,
                    "stored_shape": list(stored),
                    "scale": list(scale),
                    "predicted_canvas": list(predicted),
                    "matches_canvas": predicted == target_canvas,
                }
            )
        except Exception as exc:
            candidate_errors += 1
            candidates.append(
                {
                    "mesh": relative,
                    "mesh_key": directory,
                    "error": f"{type(exc).__name__}: {exc}",
                    "matches_canvas": False,
                }
            )

    valid = [row for row in candidates if row["matches_canvas"]]
    if candidate_errors:
        status = "invalid_metadata"
    elif len(valid) == 1:
        status = "resolved"
    elif len(valid) > 1:
        status = "ambiguous"
    else:
        status = "missing"
    result = {
        "schema_version": 1,
        "status": status,
        "selection_uses_labels": False,
        "selection_signals": ["volume_id", "canvas_compatibility"],
        "segment": segment,
        "surface_volume": surface_name,
        "volume_id": target_id,
        "surface": surface,
        "named_candidate_count": len(named),
        "valid_candidate_count": len(valid),
        "candidates": candidates,
        "selected_mesh": valid[0]["mesh"] if status == "resolved" else None,
        "selected_mesh_key": valid[0]["mesh_key"] if status == "resolved" else None,
    }
    if status == "missing":
        result["error"] = "no volume-id candidate reproduces the surface canvas"
    elif status == "ambiguous":
        result["error"] = "multiple volume-id candidates reproduce the surface canvas"
    elif status == "invalid_metadata":
        result["error"] = "one or more volume-id candidates have unreadable metadata"
    return result


def exit_code(status: str) -> int:
    return {
        "resolved": EXIT_RESOLVED,
        "missing": EXIT_MISSING,
        "ambiguous": EXIT_AMBIGUOUS,
        "invalid_metadata": EXIT_INVALID_METADATA,
    }[status]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("segment", help="public segment bucket key or URL")
    parser.add_argument("surface_volume", help="surface-volume name, key, or URL")
    parser.add_argument("--base-url", default=BUCKET_URL)
    parser.add_argument("--cache-dir", type=Path, default=Path("data/canvas_provenance_cache"))
    parser.add_argument("--budget-mib", type=int, default=32)
    parser.add_argument("--output", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    client = PublicS3(
        base_url=args.base_url,
        cache_dir=args.cache_dir,
        budget_bytes=args.budget_mib * 1024 * 1024,
    )
    result = resolve_volume_frame_mesh(client, args.segment, args.surface_volume)
    result["network"] = {
        "downloaded_bytes": client.downloaded_bytes,
        "request_count": client.request_count,
        "cache_hit_bytes": client.cache_hit_bytes,
        "cache_hit_count": client.cache_hit_count,
        "cache_invalid_bytes": client.cache_invalid_bytes,
        "cache_invalid_count": client.cache_invalid_count,
        "budget_bytes": client.budget_bytes,
    }
    payload = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload)
    sys.stdout.write(payload)
    return exit_code(result["status"])


if __name__ == "__main__":
    raise SystemExit(main())
