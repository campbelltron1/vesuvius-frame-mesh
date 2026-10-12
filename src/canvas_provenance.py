#!/usr/bin/env python3
"""Audit whether published surface-volume canvases have matching public meshes.

The audit intentionally reads only S3 listings, JSON metadata, and two small
byte ranges from each x.tif. It never downloads mesh pixels or volume chunks.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import http.client
import json
import math
import os
import re
import struct
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable


BUCKET_URL = "https://vesuvius-challenge-open-data.s3.amazonaws.com"
DEFAULT_SCROLLS = (
    "PHerc0139",
    "PHerc0172",
    "PHerc0343P",
    "PHerc0500P2",
    "PHerc0800",
    "PHerc0814",
    "PHerc1447",
    "PHerc1667",
    "PHercParis4",
)
VOLUME_ID_RE = re.compile(r"volume-(\d{14})")
RANGE_REQUEST_RE = re.compile(r"^bytes=(\d+)-(\d+)$")
CONTENT_RANGE_RE = re.compile(r"^bytes (\d+)-(\d+)/(?:\d+|\*)$")


class DownloadBudgetExceeded(RuntimeError):
    pass


@dataclass(frozen=True)
class Listing:
    keys: tuple[str, ...]
    prefixes: tuple[str, ...]


class PublicS3:
    """Tiny anonymous S3 client with an on-disk response cache and byte cap."""

    def __init__(
        self,
        base_url: str = BUCKET_URL,
        cache_dir: Path | None = None,
        budget_bytes: int = 250 * 1024 * 1024,
        max_response_bytes: int = 8 * 1024 * 1024,
        request_attempts: int = 3,
        retry_delay_s: float = 0.25,
        timeout_s: float = 30.0,
        refresh_listings: bool = False,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.cache_dir = cache_dir
        self.budget_bytes = budget_bytes
        self.max_response_bytes = max_response_bytes
        self.request_attempts = request_attempts
        self.retry_delay_s = retry_delay_s
        self.timeout_s = timeout_s
        self.refresh_listings = refresh_listings
        self.downloaded_bytes = 0
        self.request_count = 0
        self.cache_hit_bytes = 0
        self.cache_hit_count = 0
        self.cache_invalid_bytes = 0
        self.cache_invalid_count = 0
        self._lock = threading.Lock()
        self._thread_counters = threading.local()
        if cache_dir is not None:
            cache_dir.mkdir(parents=True, exist_ok=True)

    def _local_counters(self) -> dict[str, int]:
        counters = getattr(self._thread_counters, "values", None)
        if counters is None:
            counters = {
                "downloaded_bytes": 0,
                "request_count": 0,
                "cache_hit_bytes": 0,
                "cache_hit_count": 0,
                "cache_invalid_bytes": 0,
                "cache_invalid_count": 0,
            }
            self._thread_counters.values = counters
        return counters

    def _record(self, **changes: int) -> None:
        local = self._local_counters()
        with self._lock:
            for field, amount in changes.items():
                setattr(self, field, int(getattr(self, field)) + amount)
                local[field] += amount

    def network_snapshot(self, *, thread_local: bool = False) -> dict[str, int]:
        """Return exact global counters or counters attributed to this thread."""

        fields = (
            "downloaded_bytes",
            "request_count",
            "cache_hit_bytes",
            "cache_hit_count",
            "cache_invalid_bytes",
            "cache_invalid_count",
        )
        if thread_local:
            local = self._local_counters()
            return {field: local[field] for field in fields}
        with self._lock:
            return {field: int(getattr(self, field)) for field in fields}

    def _cached_read(
        self, cache_key: str, *, expected_bytes: int | None = None
    ) -> bytes | None:
        if self.cache_dir is None:
            return None
        path = self.cache_dir / hashlib.sha256(cache_key.encode()).hexdigest()
        try:
            data = path.read_bytes()
        except FileNotFoundError:
            return None
        if expected_bytes is not None and len(data) != expected_bytes:
            # unlink is atomic. A later request must traverse the strict live
            # response boundary before any replacement can enter the cache.
            path.unlink(missing_ok=True)
            self._record(cache_invalid_bytes=len(data), cache_invalid_count=1)
            return None
        self._record(cache_hit_bytes=len(data), cache_hit_count=1)
        return data

    def _cached_write(self, cache_key: str, data: bytes) -> None:
        if self.cache_dir is None:
            return
        path = self.cache_dir / hashlib.sha256(cache_key.encode()).hexdigest()
        # Writers for the same uncached URL can arrive together.  A fixed
        # `<hash>.tmp` name lets one writer rename another's file and leaves
        # the loser with FileNotFoundError.  Use a per-writer temporary in the
        # destination directory so os.replace remains atomic on one filesystem.
        descriptor, tmp_name = tempfile.mkstemp(
            dir=self.cache_dir,
            prefix=f".{path.name}.",
            suffix=".tmp",
        )
        tmp = Path(tmp_name)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(data)
            os.replace(tmp, path)
        finally:
            tmp.unlink(missing_ok=True)

    def _request(
        self,
        url: str,
        byte_range: tuple[int, int] | None = None,
        *,
        bypass_cache: bool = False,
    ) -> bytes:
        cache_key = url if byte_range is None else f"{url}#{byte_range[0]}-{byte_range[1]}"
        if not bypass_cache:
            expected_bytes = (
                byte_range[1] - byte_range[0] + 1
                if byte_range is not None
                else None
            )
            cached = self._cached_read(cache_key, expected_bytes=expected_bytes)
            if cached is not None:
                return cached

        headers = {"User-Agent": "vesuvius-canvas-provenance/0.1"}
        if byte_range is not None:
            headers["Range"] = f"bytes={byte_range[0]}-{byte_range[1]}"
        req = urllib.request.Request(url, headers=headers)
        last_error: BaseException | None = None
        for attempt in range(self.request_attempts):
            try:
                data = self._read_response(req)
                break
            except urllib.error.HTTPError as exc:
                if exc.code not in {408, 429, 500, 502, 503, 504}:
                    raise
                last_error = exc
            except (
                urllib.error.URLError,
                TimeoutError,
                ConnectionError,
                http.client.HTTPException,
            ) as exc:
                last_error = exc
            if attempt + 1 < self.request_attempts:
                time.sleep(self.retry_delay_s * (2**attempt))
        else:
            assert last_error is not None
            raise last_error

        if len(data) > self.max_response_bytes:
            raise DownloadBudgetExceeded(
                f"single response exceeds cap: > {self.max_response_bytes} bytes"
            )

        with self._lock:
            if self.downloaded_bytes + len(data) > self.budget_bytes:
                raise DownloadBudgetExceeded(
                    f"download cap exceeded: {self.downloaded_bytes + len(data)} "
                    f"> {self.budget_bytes} bytes"
                )
            self.downloaded_bytes += len(data)
            self.request_count += 1
            local = self._local_counters()
            local["downloaded_bytes"] += len(data)
            local["request_count"] += 1
        self._cached_write(cache_key, data)
        return data

    def _read_response(self, request: urllib.request.Request) -> bytes:
        with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
            requested_range = request.get_header("Range")
            if requested_range is not None:
                requested = RANGE_REQUEST_RE.fullmatch(requested_range)
                if requested is None:
                    raise ValueError(f"unsupported Range request: {requested_range!r}")
                start, end = map(int, requested.groups())
                if end < start:
                    raise ValueError(f"invalid Range request: {requested_range!r}")
                expected = end - start + 1
                if expected > self.max_response_bytes:
                    raise DownloadBudgetExceeded(
                        f"requested range exceeds response cap: {expected} > "
                        f"{self.max_response_bytes} bytes"
                    )
                status = getattr(response, "status", None)
                if status is None:
                    status = response.getcode()
                if status != 206:
                    raise ValueError(
                        f"server ignored Range {requested_range!r}: HTTP {status}"
                    )
                content_range = response.headers.get("Content-Range", "")
                returned = CONTENT_RANGE_RE.fullmatch(content_range)
                if returned is None or tuple(map(int, returned.groups())) != (start, end):
                    raise ValueError(
                        f"response Content-Range {content_range!r} does not match "
                        f"request {requested_range!r}"
                    )
                declared = response.headers.get("Content-Length")
                if declared and int(declared) != expected:
                    raise ValueError(
                        f"range Content-Length {declared} != expected {expected}"
                    )
                data = response.read(expected + 1)
                if len(data) != expected:
                    raise ValueError(
                        f"range body length {len(data)} != expected {expected}"
                    )
                return data
            declared = response.headers.get("Content-Length")
            if declared and int(declared) > self.max_response_bytes:
                raise DownloadBudgetExceeded(
                    f"single response exceeds cap: {declared} > {self.max_response_bytes} bytes"
                )
            return response.read(self.max_response_bytes + 1)

    def object_bytes(
        self, key: str, byte_range: tuple[int, int] | None = None
    ) -> bytes:
        quoted = urllib.parse.quote(key, safe="/._-~")
        return self._request(f"{self.base_url}/{quoted}", byte_range)

    def json(self, key: str) -> dict:
        return json.loads(self.object_bytes(key))

    def list(self, prefix: str, delimiter: str = "/") -> Listing:
        keys: list[str] = []
        prefixes: list[str] = []
        token: str | None = None
        while True:
            query = {
                "list-type": "2",
                "prefix": prefix,
                "delimiter": delimiter,
                "max-keys": "1000",
            }
            if token:
                query["continuation-token"] = token
            url = f"{self.base_url}/?{urllib.parse.urlencode(query)}"
            root = ET.fromstring(
                self._request(url, bypass_cache=self.refresh_listings)
            )
            keys.extend(x.text or "" for x in root.findall("{*}Contents/{*}Key"))
            prefixes.extend(
                x.text or "" for x in root.findall("{*}CommonPrefixes/{*}Prefix")
            )
            truncated = (root.findtext("{*}IsTruncated") or "false").lower() == "true"
            if not truncated:
                break
            token = root.findtext("{*}NextContinuationToken")
            if not token:
                raise RuntimeError(f"truncated listing lacks continuation token: {prefix}")
        return Listing(tuple(keys), tuple(prefixes))


def _scalar_from_ifd_entry(
    entry: bytes, endian: str, bigtiff: bool
) -> tuple[int, int] | None:
    if bigtiff:
        tag, value_type = struct.unpack(endian + "HH", entry[:4])
        count = struct.unpack(endian + "Q", entry[4:12])[0]
        inline = entry[12:20]
        capacity = 8
    else:
        tag, value_type, count = struct.unpack(endian + "HHI", entry[:8])
        inline = entry[8:12]
        capacity = 4
    sizes = {3: 2, 4: 4, 16: 8}
    if count != 1 or value_type not in sizes or sizes[value_type] > capacity:
        return None
    fmt = {3: "H", 4: "I", 16: "Q"}[value_type]
    return tag, int(struct.unpack(endian + fmt, inline[: sizes[value_type]])[0])


def parse_tiff_shape(read_range: Callable[[int, int], bytes]) -> tuple[int, int]:
    """Return (height, width) using only the TIFF header and first IFD."""

    header = read_range(0, 15)
    if len(header) < 8 or header[:2] not in (b"II", b"MM"):
        raise ValueError("not a TIFF header")
    endian = "<" if header[:2] == b"II" else ">"
    magic = struct.unpack(endian + "H", header[2:4])[0]
    if magic == 42:
        bigtiff = False
        ifd_offset = struct.unpack(endian + "I", header[4:8])[0]
        count_size, entry_size = 2, 12
    elif magic == 43:
        bigtiff = True
        if len(header) < 16 or struct.unpack(endian + "H", header[4:6])[0] != 8:
            raise ValueError("unsupported BigTIFF offset size")
        ifd_offset = struct.unpack(endian + "Q", header[8:16])[0]
        count_size, entry_size = 8, 20
    else:
        raise ValueError(f"unsupported TIFF magic: {magic}")

    count_raw = read_range(ifd_offset, ifd_offset + count_size - 1)
    if len(count_raw) != count_size:
        raise ValueError("truncated TIFF IFD count")
    entry_count = struct.unpack(endian + ("Q" if bigtiff else "H"), count_raw)[0]
    if entry_count > 4096:
        raise ValueError(f"implausible TIFF IFD entry count: {entry_count}")
    start = ifd_offset + count_size
    raw = read_range(start, start + entry_count * entry_size - 1)
    if len(raw) != entry_count * entry_size:
        raise ValueError("truncated TIFF IFD")

    values: dict[int, int] = {}
    for index in range(entry_count):
        entry = raw[index * entry_size : (index + 1) * entry_size]
        parsed = _scalar_from_ifd_entry(entry, endian, bigtiff)
        if parsed is not None:
            values[parsed[0]] = parsed[1]
    try:
        return values[257], values[256]
    except KeyError as exc:
        raise ValueError("TIFF IFD lacks ImageWidth/ImageLength") from exc


def tiff_shape(client: PublicS3, key: str) -> tuple[int, int]:
    return parse_tiff_shape(lambda start, end: client.object_bytes(key, (start, end)))


def cxx_lround_positive(value: float) -> int:
    return max(1, math.floor(value + 0.5))


def rendered_shape(
    stored_shape: Iterable[int],
    scale: Iterable[float],
    render_scale: float = 1.0,
) -> tuple[int, int]:
    height, width = (int(x) for x in stored_shape)
    scale_x, scale_y = (float(x) for x in scale)
    if scale_x <= 0 or scale_y <= 0:
        raise ValueError("mesh scale must be positive")
    if render_scale <= 0:
        raise ValueError("render scale must be positive")
    return (
        cxx_lround_positive(height * render_scale / scale_y),
        cxx_lround_positive(width * render_scale / scale_x),
    )


def volume_id(surface_volume_name: str) -> str | None:
    match = VOLUME_ID_RE.search(surface_volume_name)
    return match.group(1) if match else None


def discover_mesh_dirs(client: PublicS3, segment: str) -> list[str]:
    """Find mesh directories to depth two, including names ending in .tifxyz."""

    root = f"{segment.rstrip('/')}/mesh/"
    found: list[str] = []
    frontier = [(root, 0)]
    while frontier:
        prefix, depth = frontier.pop()
        listing = client.list(prefix)
        basenames = {key.removeprefix(prefix) for key in listing.keys}
        if "meta.json" in basenames and "x.tif" in basenames:
            found.append(prefix.rstrip("/"))
        if depth < 2:
            frontier.extend((child, depth + 1) for child in listing.prefixes)
    return sorted(set(found))


def discover_surface_dirs(client: PublicS3, segment: str) -> list[str]:
    root = f"{segment.rstrip('/')}/surface-volumes/"
    return sorted(
        prefix.rstrip("/")
        for prefix in client.list(root).prefixes
        if prefix.rstrip("/").endswith(".zarr")
    )


def audit_segment(client: PublicS3, segment: str) -> dict:
    meshes: list[dict] = []
    for directory in discover_mesh_dirs(client, segment):
        try:
            meta = client.json(f"{directory}/meta.json")
            shape = tiff_shape(client, f"{directory}/x.tif")
            scale = meta.get("scale")
            if isinstance(scale, (int, float)):
                scale = [scale, scale]
            meshes.append(
                {
                    "dir": directory.removeprefix(f"{segment.rstrip('/')}/mesh/"),
                    "stored_shape": list(shape),
                    "scale": scale,
                }
            )
        except Exception as exc:  # preserve per-object failures in the audit output
            meshes.append(
                {
                    "dir": directory.removeprefix(f"{segment.rstrip('/')}/mesh/"),
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )

    rows: list[dict] = []
    for directory in discover_surface_dirs(client, segment):
        name = directory.rsplit("/", 1)[-1]
        try:
            attrs = client.json(f"{directory}/.zattrs")
            array_meta = client.json(f"{directory}/0/.zarray")
            canvas = attrs.get("canvas_size")
            if not canvas or len(canvas) != 2:
                raise ValueError("missing two-element canvas_size")
            reference = (int(canvas[1]), int(canvas[0]))
            level0_shape = array_meta.get("shape")
            if not level0_shape or len(level0_shape) < 2:
                raise ValueError("missing level-0 Zarr shape")
            array_canvas = (int(level0_shape[-2]), int(level0_shape[-1]))
            if array_canvas != reference:
                raise ValueError(
                    f"canvas_size {reference} != level-0 array shape {array_canvas}"
                )
            target_id = volume_id(name)
            source_group = int(attrs.get("source_group", 0))
            if source_group < 0:
                raise ValueError(f"negative source_group: {source_group}")
            ds_scale = math.ldexp(1.0, -source_group)
            predictions = {
                mesh["dir"]: rendered_shape(
                    mesh["stored_shape"], mesh["scale"], ds_scale
                )
                for mesh in meshes
                if "stored_shape" in mesh and mesh.get("scale")
            }
            exact = [
                mesh_dir for mesh_dir, predicted in predictions.items() if predicted == reference
            ]
            target = [
                mesh["dir"]
                for mesh in meshes
                if target_id and f"-on-{target_id}-" in mesh["dir"]
            ]
            exact_target = sorted(set(exact).intersection(target))
            rows.append(
                {
                    "surface_volume": name,
                    "volume_id": target_id,
                    "source_group": source_group,
                    "ds_scale": ds_scale,
                    "reference_shape": list(reference),
                    "level0_shape": [int(value) for value in level0_shape],
                    "exact_meshes": exact,
                    "target_meshes": target,
                    "exact_target_meshes": exact_target,
                    "status": (
                        "exact_target"
                        if exact_target
                        else "exact_other"
                        if exact
                        else "unresolved"
                    ),
                }
            )
        except Exception as exc:
            rows.append(
                {
                    "surface_volume": name,
                    "status": "error",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
    return {"segment": segment, "meshes": meshes, "surface_volumes": rows}


def discover_segments(client: PublicS3, scroll: str) -> list[str]:
    prefix = f"{scroll}/segments/"
    return [item.rstrip("/") for item in client.list(prefix).prefixes]


def summarize(segments: list[dict]) -> dict:
    counts = {"exact_target": 0, "exact_other": 0, "unresolved": 0, "error": 0}
    dotted_target_meshes = 0
    mesh_errors = 0
    for segment in segments:
        dotted_target_meshes += sum(
            ".tifxyz" in mesh.get("dir", "") and "-on-" in mesh.get("dir", "")
            for mesh in segment["meshes"]
        )
        mesh_errors += sum("error" in mesh for mesh in segment["meshes"])
        for row in segment["surface_volumes"]:
            counts[row["status"]] = counts.get(row["status"], 0) + 1
    total = sum(counts.values())
    return {
        "surface_volumes": total,
        "status_counts": counts,
        "exact_total": counts["exact_target"] + counts["exact_other"],
        "dotted_target_meshes": dotted_target_meshes,
        "mesh_errors": mesh_errors,
    }


def audit_scrolls(client: PublicS3, scrolls: list[str], workers: int) -> dict:
    segment_names: list[str] = []
    for scroll in scrolls:
        segment_names.extend(discover_segments(client, scroll))
    with ThreadPoolExecutor(max_workers=max(1, min(4, workers))) as executor:
        segments = []
        for index, segment in enumerate(
            executor.map(lambda name: audit_segment(client, name), segment_names), 1
        ):
            segments.append(segment)
            if index % 25 == 0 or index == len(segment_names):
                print(
                    f"canvas audit: {index}/{len(segment_names)} segments",
                    file=sys.stderr,
                    flush=True,
                )
    segments.sort(key=lambda row: row["segment"])
    by_scroll = {
        scroll: summarize(
            [row for row in segments if row["segment"].startswith(f"{scroll}/")]
        )
        for scroll in scrolls
    }
    return {
        "schema_version": 1,
        "generated_at_utc": dt.datetime.now(dt.UTC).isoformat(),
        "git_sha": _git_sha(),
        "bucket_url": client.base_url,
        "method": "public-listings-json-and-tiff-ifd-only",
        "scrolls": scrolls,
        "summary": summarize(segments),
        "by_scroll": by_scroll,
        "segments": segments,
    }


def _git_sha() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scrolls", nargs="+", default=list(DEFAULT_SCROLLS))
    parser.add_argument("--workers", type=int, default=4, choices=range(1, 5))
    parser.add_argument("--cache-dir", type=Path, default=Path("data/canvas_provenance_cache"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--budget-mib", type=int, default=250)
    parser.add_argument(
        "--refresh-listings",
        action="store_true",
        help="bypass cached S3 XML listings while retaining object metadata cache",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    client = PublicS3(
        cache_dir=args.cache_dir,
        budget_bytes=args.budget_mib * 1024 * 1024,
        refresh_listings=args.refresh_listings,
    )
    result = audit_scrolls(client, args.scrolls, args.workers)
    result["network"] = {
        "downloaded_bytes": client.downloaded_bytes,
        "request_count": client.request_count,
        "cache_hit_bytes": client.cache_hit_bytes,
        "cache_hit_count": client.cache_hit_count,
        "cache_invalid_bytes": client.cache_invalid_bytes,
        "cache_invalid_count": client.cache_invalid_count,
        "budget_bytes": client.budget_bytes,
        "max_response_bytes": client.max_response_bytes,
        "request_attempts": client.request_attempts,
        "refresh_listings": client.refresh_listings,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"summary": result["summary"], "network": result["network"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
