#!/usr/bin/env python3
"""Independently verify frozen volume-frame mesh decisions from raw evidence.

This module intentionally does not import the producer (`canvas_provenance`) or
the online resolver.  It ignores their decision fields and recomputes the
target volume id, rendered canvas and unique candidate from lower-level JSON.
"""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any, Iterable, Mapping, Sequence


VOLUME_ID_RE = re.compile(r"(?:^|-)volume-(\d{14})(?:-|\.zarr(?:$|/))")


def canonical_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant: {value}")


def _strict_json(payload: bytes, source: str) -> Mapping[str, Any]:
    try:
        value = json.loads(payload, parse_constant=_reject_constant)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"invalid JSON in {source}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"JSON root in {source} must be an object")
    return value


def _positive_int_pair(value: Any, label: str) -> tuple[int, int]:
    if (
        not isinstance(value, list)
        or len(value) != 2
        or any(isinstance(item, bool) or not isinstance(item, int) for item in value)
        or any(item <= 0 for item in value)
    ):
        raise ValueError(f"{label} must be two positive integers")
    return int(value[0]), int(value[1])


def _positive_scale_pair(value: Any) -> tuple[float, float]:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        value = [value, value]
    if not isinstance(value, list) or len(value) != 2:
        raise ValueError("mesh scale must have two elements")
    result: list[float] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            raise ValueError("mesh scale values must be numeric")
        parsed = float(item)
        if not math.isfinite(parsed) or parsed <= 0:
            raise ValueError("mesh scale values must be finite and positive")
        result.append(parsed)
    return result[0], result[1]


def _lround_positive(value: float) -> int:
    if not math.isfinite(value) or value <= 0:
        raise ValueError("rendered dimension is not finite and positive")
    return max(1, math.floor(value + 0.5))


def _rendered_shape(
    stored_shape: tuple[int, int], scale: tuple[float, float], source_group: int
) -> tuple[int, int]:
    if isinstance(source_group, bool) or not isinstance(source_group, int):
        raise ValueError("source_group must be a non-negative integer")
    if source_group < 0 or source_group > 63:
        raise ValueError("source_group must be in [0, 63]")
    downscale = math.ldexp(1.0, -source_group)
    height, width = stored_shape
    scale_x, scale_y = scale
    return (
        _lround_positive(height * downscale / scale_y),
        _lround_positive(width * downscale / scale_x),
    )


def _target_volume_id(surface_name: Any) -> str:
    if not isinstance(surface_name, str) or not surface_name.endswith(".zarr"):
        raise ValueError("surface_volume must be a Zarr group name")
    matches = VOLUME_ID_RE.findall(surface_name)
    if len(matches) != 1:
        raise ValueError("surface_volume must contain exactly one target volume id")
    return matches[0]


def _mesh_table(raw_meshes: Any) -> dict[str, Mapping[str, Any]]:
    if not isinstance(raw_meshes, list):
        raise ValueError("segment meshes must be a list")
    table: dict[str, Mapping[str, Any]] = {}
    for index, mesh in enumerate(raw_meshes):
        if not isinstance(mesh, dict):
            raise ValueError(f"mesh {index} must be an object")
        name = mesh.get("dir")
        if not isinstance(name, str) or not name or name.startswith("/"):
            raise ValueError(f"mesh {index} has invalid directory")
        if name in table:
            raise ValueError(f"duplicate mesh directory: {name}")
        table[name] = mesh
    return table


def _verify_surface(
    segment_name: str,
    meshes: Mapping[str, Mapping[str, Any]],
    surface: Mapping[str, Any],
) -> dict[str, Any]:
    surface_name = surface.get("surface_volume")
    identity = f"{segment_name}/surface-volumes/{surface_name}"
    target_id = _target_volume_id(surface_name)
    reference = _positive_int_pair(surface.get("reference_shape"), "reference_shape")
    level0 = surface.get("level0_shape")
    if not isinstance(level0, list) or len(level0) < 2:
        raise ValueError("level0_shape must contain at least two dimensions")
    level0_canvas = _positive_int_pair(level0[-2:], "level0_shape canvas")
    if level0_canvas != reference:
        raise ValueError(
            f"declared canvas {reference} != level-0 canvas {level0_canvas}"
        )
    source_group = surface.get("source_group")
    named: list[dict[str, Any]] = []
    for name, mesh in sorted(meshes.items()):
        if f"-on-{target_id}-" not in name:
            continue
        stored = _positive_int_pair(mesh.get("stored_shape"), f"{name} stored_shape")
        scale = _positive_scale_pair(mesh.get("scale"))
        predicted = _rendered_shape(stored, scale, source_group)
        named.append(
            {
                "mesh": name,
                "stored_shape": list(stored),
                "scale": list(scale),
                "predicted_canvas": list(predicted),
                "matches_canvas": predicted == reference,
            }
        )
    valid = [candidate["mesh"] for candidate in named if candidate["matches_canvas"]]
    if len(valid) == 1:
        outcome = "resolved"
    elif len(valid) > 1:
        outcome = "ambiguous"
    else:
        outcome = "missing"

    producer_candidates = surface.get("exact_target_meshes")
    producer_status = surface.get("status")
    agreement = (
        producer_status == "exact_target"
        and isinstance(producer_candidates, list)
        and producer_candidates == valid
    )
    return {
        "identity": identity,
        "segment": segment_name,
        "surface_volume": surface_name,
        "volume_id": target_id,
        "source_group": source_group,
        "reference_canvas": list(reference),
        "level0_canvas": list(level0_canvas),
        "named_candidates": named,
        "valid_candidates": valid,
        "selected_mesh": valid[0] if outcome == "resolved" else None,
        "outcome": outcome,
        "producer_agreement": agreement,
    }


def verify_documents(paths: Iterable[Path]) -> dict[str, Any]:
    sources: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    global_errors: list[str] = []
    identities: set[str] = set()
    source_names: set[str] = set()

    for path in sorted((Path(item) for item in paths), key=lambda item: item.name):
        payload = path.read_bytes()
        source_name = path.name
        if source_name in source_names:
            global_errors.append(f"duplicate source basename: {source_name}")
            continue
        source_names.add(source_name)
        source = {
            "name": source_name,
            "bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
        }
        sources.append(source)
        try:
            document = _strict_json(payload, source_name)
            segments = document.get("segments")
            if not isinstance(segments, list):
                raise ValueError("segments must be a list")
            for segment_index, segment in enumerate(segments):
                if not isinstance(segment, dict):
                    raise ValueError(f"segment {segment_index} must be an object")
                segment_name = segment.get("segment")
                if (
                    not isinstance(segment_name, str)
                    or not segment_name
                    or "/segments/" not in segment_name
                ):
                    raise ValueError(f"segment {segment_index} has invalid identity")
                meshes = _mesh_table(segment.get("meshes"))
                surfaces = segment.get("surface_volumes")
                if not isinstance(surfaces, list):
                    raise ValueError(f"{segment_name}: surface_volumes must be a list")
                for surface_index, surface in enumerate(surfaces):
                    if not isinstance(surface, dict):
                        raise ValueError(
                            f"{segment_name}: surface {surface_index} must be an object"
                        )
                    fallback_identity = (
                        f"{segment_name}/surface-volumes/"
                        f"{surface.get('surface_volume', '<invalid>')}"
                    )
                    try:
                        row = _verify_surface(segment_name, meshes, surface)
                    except Exception as exc:
                        row = {
                            "identity": fallback_identity,
                            "segment": segment_name,
                            "surface_volume": surface.get("surface_volume"),
                            "outcome": "invalid_metadata",
                            "selected_mesh": None,
                            "producer_agreement": False,
                            "error": f"{type(exc).__name__}: {exc}",
                        }
                    identity = row["identity"]
                    if identity in identities:
                        row = {
                            **row,
                            "outcome": "invalid_metadata",
                            "selected_mesh": None,
                            "producer_agreement": False,
                            "error": "duplicate surface identity",
                        }
                    identities.add(identity)
                    rows.append(row)
        except Exception as exc:
            global_errors.append(f"{source_name}: {type(exc).__name__}: {exc}")

    rows.sort(key=lambda row: row["identity"])
    counts = Counter(row["outcome"] for row in rows)
    disagreements = sum(not row["producer_agreement"] for row in rows)
    decision = (
        "GO"
        if rows
        and counts == Counter({"resolved": len(rows)})
        and disagreements == 0
        and not global_errors
        else "NO-GO"
    )
    manifest_sha256 = hashlib.sha256(canonical_json_bytes(rows)).hexdigest()
    return {
        "schema_version": 1,
        "method": (
            "independent offline recomputation from surface canvas/source_group "
            "and mesh directory/stored_shape/scale"
        ),
        "selection_uses_labels": False,
        "ignored_producer_fields": [
            "status",
            "exact_meshes",
            "target_meshes",
            "exact_target_meshes",
        ],
        "sources": sources,
        "summary": {
            "decision": decision,
            "total": len(rows),
            "counts": dict(sorted(counts.items())),
            "producer_disagreements": disagreements,
            "global_errors": len(global_errors),
            "manifest_sha256": manifest_sha256,
        },
        "global_errors": global_errors,
        "rows": rows,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputs", type=Path, nargs="+")
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = verify_documents(args.inputs)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical_json_bytes(result))
    print(json.dumps(result["summary"], indent=2, sort_keys=True))
    return 0 if result["summary"]["decision"] == "GO" else 2


if __name__ == "__main__":
    raise SystemExit(main())
