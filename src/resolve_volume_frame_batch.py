#!/usr/bin/env python3
"""Resolve a strict JSON plan of surface volumes in one bounded process."""

from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
import threading
from typing import Any, Mapping, Sequence

from canvas_provenance import BUCKET_URL, PublicS3
from resolve_volume_frame_mesh import normalize_segment, resolve_volume_frame_mesh


NETWORK_FIELDS = (
    "downloaded_bytes",
    "request_count",
    "cache_hit_bytes",
    "cache_hit_count",
    "cache_invalid_bytes",
    "cache_invalid_count",
)


def canonical_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, allow_nan=False, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n"
    ).encode("utf-8")


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant: {value}")


def load_plan(path: Path) -> tuple[list[dict[str, str]], dict[str, Any]]:
    payload = path.read_bytes()
    try:
        document = json.loads(payload, parse_constant=_reject_constant)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"invalid plan JSON: {exc}") from exc
    if not isinstance(document, dict) or set(document) != {"schema_version", "cases"}:
        raise ValueError("plan root must contain exactly schema_version and cases")
    if document["schema_version"] != 1:
        raise ValueError("plan schema_version must be 1")
    raw_cases = document["cases"]
    if not isinstance(raw_cases, list) or not raw_cases:
        raise ValueError("plan cases must be a non-empty list")
    cases: list[dict[str, str]] = []
    ids: set[str] = set()
    required = {"id", "segment", "surface_volume"}
    for index, raw in enumerate(raw_cases):
        if not isinstance(raw, dict) or set(raw) != required:
            raise ValueError(f"case {index} must contain exactly {sorted(required)}")
        if any(not isinstance(raw[key], str) or not raw[key] for key in required):
            raise ValueError(f"case {index} fields must be non-empty strings")
        case_id = raw["id"]
        if case_id in ids:
            raise ValueError(f"duplicate case id: {case_id}")
        ids.add(case_id)
        cases.append({key: raw[key] for key in ("id", "segment", "surface_volume")})
    source = {
        "name": path.name,
        "bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }
    return cases, source


def _network_snapshot(
    client: PublicS3, *, thread_local: bool = False
) -> dict[str, int]:
    snapshot = getattr(client, "network_snapshot", None)
    if snapshot is not None:
        return snapshot(thread_local=thread_local)
    return {field: int(getattr(client, field)) for field in NETWORK_FIELDS}


def _segment_key(case: Mapping[str, str], client: PublicS3) -> str:
    try:
        return normalize_segment(case["segment"], client.base_url)
    except Exception:
        # Invalid inputs must still reach the resolver and become an explicit
        # invalid_metadata row. Identical invalid spellings may safely share a
        # worker; they cannot populate the normalized mesh cache.
        return f"\0invalid\0{case['segment']}"


def _worker_buckets(
    cases: list[Mapping[str, str]],
    client: PublicS3,
    workers: int,
    *,
    completed_indices: set[int] | None = None,
) -> list[list[tuple[int, Mapping[str, str]]]]:
    groups: dict[str, list[tuple[int, Mapping[str, str]]]] = {}
    for index, case in enumerate(cases):
        if completed_indices and index in completed_indices:
            continue
        groups.setdefault(_segment_key(case, client), []).append((index, case))
    if not groups:
        return []
    used = min(workers, len(groups))
    buckets: list[list[tuple[int, Mapping[str, str]]]] = [[] for _ in range(used)]
    loads = [0] * used
    for group in groups.values():
        target = min(range(used), key=lambda index: (loads[index], index))
        buckets[target].extend(group)
        loads[target] += len(group)
    return buckets


def _resolve_case(
    case: Mapping[str, str],
    client: PublicS3,
    mesh_directory_cache: dict[str, list[str]],
) -> dict[str, Any]:
    before = _network_snapshot(client, thread_local=True)
    result = resolve_volume_frame_mesh(
        client,
        case["segment"],
        case["surface_volume"],
        mesh_directory_cache=mesh_directory_cache,
    )
    after = _network_snapshot(client, thread_local=True)
    return {
        "id": case["id"],
        "segment_input": case["segment"],
        "surface_volume_input": case["surface_volume"],
        "network": {field: after[field] - before[field] for field in NETWORK_FIELDS},
        "result": result,
    }


def _validate_checkpoint_row(
    raw: object,
    *,
    index_by_id: Mapping[str, int],
    cases: list[Mapping[str, str]],
) -> tuple[int, dict[str, Any]]:
    required = {
        "id",
        "segment_input",
        "surface_volume_input",
        "network",
        "result",
    }
    if not isinstance(raw, dict) or set(raw) != required:
        raise ValueError(f"checkpoint row must contain exactly {sorted(required)}")
    case_id = raw["id"]
    if not isinstance(case_id, str) or case_id not in index_by_id:
        raise ValueError(f"checkpoint row is outside plan: {case_id!r}")
    index = index_by_id[case_id]
    case = cases[index]
    if raw["segment_input"] != case["segment"]:
        raise ValueError(f"checkpoint segment mismatch for case {case_id}")
    if raw["surface_volume_input"] != case["surface_volume"]:
        raise ValueError(f"checkpoint surface mismatch for case {case_id}")
    network = raw["network"]
    if not isinstance(network, dict) or set(network) != set(NETWORK_FIELDS):
        raise ValueError(f"checkpoint network fields are invalid for case {case_id}")
    if any(
        isinstance(value, bool) or not isinstance(value, int) or value < 0
        for value in network.values()
    ):
        raise ValueError(f"checkpoint network counters are invalid for case {case_id}")
    result = raw["result"]
    if not isinstance(result, dict) or result.get("status") not in {
        "resolved",
        "missing",
        "ambiguous",
        "invalid_metadata",
    }:
        raise ValueError(f"checkpoint result is invalid for case {case_id}")
    if result.get("schema_version", 1) != 1:
        raise ValueError(f"checkpoint result schema is invalid for case {case_id}")
    if result.get("selection_uses_labels", False) is not False:
        raise ValueError(f"checkpoint result is not label-free for case {case_id}")
    selected = result.get("selected_mesh")
    if result["status"] == "resolved":
        if not isinstance(selected, str) or not selected:
            raise ValueError(
                f"checkpoint resolved result lacks a mesh for case {case_id}"
            )
    elif selected is not None:
        raise ValueError(
            f"checkpoint abstention contains a selected mesh for case {case_id}"
        )
    return index, dict(raw)


def load_checkpoint(
    path: Path,
    cases: list[Mapping[str, str]],
    source: Mapping[str, Any],
) -> dict[int, dict[str, Any]]:
    try:
        document = json.loads(path.read_bytes(), parse_constant=_reject_constant)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"invalid checkpoint JSON: {exc}") from exc
    required = {"schema_version", "method", "plan_sha256", "cases"}
    if not isinstance(document, dict) or set(document) != required:
        raise ValueError(f"checkpoint root must contain exactly {sorted(required)}")
    if document["schema_version"] != 1:
        raise ValueError("checkpoint schema_version must be 1")
    if document["method"] != "atomic batch progress checkpoint":
        raise ValueError("checkpoint method is invalid")
    if document["plan_sha256"] != source.get("sha256"):
        raise ValueError("checkpoint plan SHA-256 does not match input plan")
    raw_rows = document["cases"]
    if not isinstance(raw_rows, list):
        raise ValueError("checkpoint cases must be a list")
    index_by_id = {case["id"]: index for index, case in enumerate(cases)}
    rows: dict[int, dict[str, Any]] = {}
    for raw in raw_rows:
        index, row = _validate_checkpoint_row(
            raw, index_by_id=index_by_id, cases=cases
        )
        if index in rows:
            raise ValueError(f"duplicate checkpoint case id: {row['id']}")
        rows[index] = row
    return rows


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, tmp_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    tmp = Path(tmp_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


class CheckpointWriter:
    def __init__(
        self,
        path: Path,
        source: Mapping[str, Any],
        initial_rows: Mapping[int, dict[str, Any]] | None = None,
    ) -> None:
        self.path = path
        self.plan_sha256 = source["sha256"]
        self.rows = dict(initial_rows or {})
        self._lock = threading.Lock()

    def record(self, index: int, row: dict[str, Any]) -> None:
        with self._lock:
            self.rows[index] = row
            document = {
                "schema_version": 1,
                "method": "atomic batch progress checkpoint",
                "plan_sha256": self.plan_sha256,
                "cases": [self.rows[key] for key in sorted(self.rows)],
            }
            _atomic_write(self.path, canonical_json_bytes(document))


def run_batch(
    cases: list[Mapping[str, str]],
    source: Mapping[str, Any],
    client: PublicS3,
    *,
    workers: int = 1,
    initial_rows: Mapping[int, dict[str, Any]] | None = None,
    progress_callback: Any | None = None,
) -> dict[str, Any]:
    if workers not in {1, 2}:
        raise ValueError("workers must be 1 or 2")
    resumed_rows = dict(initial_rows or {})
    if any(index < 0 or index >= len(cases) for index in resumed_rows):
        raise ValueError("initial row index is outside plan")
    buckets = _worker_buckets(
        cases, client, workers, completed_indices=set(resumed_rows)
    )
    rows: list[dict[str, Any] | None] = [None] * len(cases)
    for index, row in resumed_rows.items():
        rows[index] = row
    cache_sizes = [0] * len(buckets)

    def run_bucket(worker_index: int) -> None:
        mesh_directory_cache: dict[str, list[str]] = {}
        for case_index, case in buckets[worker_index]:
            row = _resolve_case(case, client, mesh_directory_cache)
            rows[case_index] = row
            if progress_callback is not None:
                progress_callback(case_index, row)
        cache_sizes[worker_index] = len(mesh_directory_cache)

    if not buckets:
        pass
    elif len(buckets) == 1:
        run_bucket(0)
    else:
        with ThreadPoolExecutor(
            max_workers=len(buckets), thread_name_prefix="frame-resolver"
        ) as pool:
            futures = [pool.submit(run_bucket, index) for index in range(len(buckets))]
            for future in futures:
                future.result()

    if any(row is None for row in rows):
        raise RuntimeError("internal error: batch left unresolved plan rows")
    completed_rows = [row for row in rows if row is not None]
    counts = Counter(row["result"]["status"] for row in completed_rows)
    all_resolved = counts == Counter({"resolved": len(completed_rows)})
    total_network = _network_snapshot(client)
    prior_network = {
        field: sum(row["network"][field] for row in resumed_rows.values())
        for field in NETWORK_FIELDS
    }
    return {
        "schema_version": 1,
        "method": "one-process bounded label-free volume-frame mesh resolution",
        "selection_uses_labels": False,
        "plan": dict(source),
        "summary": {
            "decision": "GO" if all_resolved else "NO-GO",
            "cases": len(completed_rows),
            "status_counts": dict(sorted(counts.items())),
            "workers_requested": workers,
            "workers_used": len(buckets),
            "resumed_cases": len(resumed_rows),
            "current_invocation_cases": len(completed_rows) - len(resumed_rows),
            "prior_case_network": prior_network,
            "mesh_directory_cache_entries": sum(cache_sizes),
            **total_network,
            "budget_bytes": client.budget_bytes,
        },
        "cases": completed_rows,
    }


def _invalid_plan_result(path: Path, error: Exception) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "method": "one-process bounded label-free volume-frame mesh resolution",
        "selection_uses_labels": False,
        "plan": {"name": path.name},
        "summary": {
            "decision": "INVALID_PLAN",
            "cases": 0,
            "status_counts": {},
        },
        "cases": [],
        "error": f"{type(error).__name__}: {error}",
    }


def _invalid_checkpoint_result(
    plan_path: Path, checkpoint_path: Path, error: Exception
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "method": "one-process bounded label-free volume-frame mesh resolution",
        "selection_uses_labels": False,
        "plan": {"name": plan_path.name},
        "checkpoint": {"name": checkpoint_path.name},
        "summary": {
            "decision": "INVALID_CHECKPOINT",
            "cases": 0,
            "status_counts": {},
        },
        "cases": [],
        "error": f"{type(error).__name__}: {error}",
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--base-url", default=BUCKET_URL)
    parser.add_argument(
        "--cache-dir", type=Path, default=Path("data/canvas_provenance_cache")
    )
    parser.add_argument("--budget-mib", type=float, default=250)
    parser.add_argument("--workers", type=int, choices=(1, 2), default=1)
    parser.add_argument("--checkpoint", type=Path)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if not math.isfinite(args.budget_mib) or args.budget_mib <= 0:
        parser.error("--budget-mib must be finite and positive")
    try:
        cases, source = load_plan(args.plan)
    except Exception as exc:
        result = _invalid_plan_result(args.plan, exc)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_bytes(canonical_json_bytes(result))
        print(json.dumps(result["summary"], indent=2, sort_keys=True))
        return 4
    if args.resume and args.checkpoint is None:
        parser.error("--resume requires --checkpoint")
    initial_rows: dict[int, dict[str, Any]] = {}
    if args.checkpoint is not None:
        try:
            if args.resume:
                initial_rows = load_checkpoint(args.checkpoint, cases, source)
            elif args.checkpoint.exists():
                raise ValueError(
                    "checkpoint already exists; use --resume or choose a new path"
                )
        except Exception as exc:
            result = _invalid_checkpoint_result(args.plan, args.checkpoint, exc)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_bytes(canonical_json_bytes(result))
            print(json.dumps(result["summary"], indent=2, sort_keys=True))
            return 4
    client = PublicS3(
        base_url=args.base_url,
        cache_dir=args.cache_dir,
        budget_bytes=math.floor(args.budget_mib * 1024 * 1024),
    )
    checkpoint_writer = (
        CheckpointWriter(args.checkpoint, source, initial_rows)
        if args.checkpoint is not None
        else None
    )
    result = run_batch(
        cases,
        source,
        client,
        workers=args.workers,
        initial_rows=initial_rows,
        progress_callback=checkpoint_writer.record if checkpoint_writer else None,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical_json_bytes(result))
    print(json.dumps(result["summary"], indent=2, sort_keys=True))
    return 0 if result["summary"]["decision"] == "GO" else 2


if __name__ == "__main__":
    raise SystemExit(main())
