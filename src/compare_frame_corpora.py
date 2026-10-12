#!/usr/bin/env python3
"""Compare independently verified frame-resolver corpora without moving baselines."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence


def canonical_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, allow_nan=False, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n"
    ).encode("utf-8")


def _positive_int_pair(value: Any, label: str) -> tuple[int, int]:
    if (
        not isinstance(value, list)
        or len(value) != 2
        or any(isinstance(item, bool) or not isinstance(item, int) for item in value)
        or any(item <= 0 for item in value)
    ):
        raise ValueError(f"{label} must be two positive integers")
    return value[0], value[1]


def _scale_pair(value: Any) -> tuple[float, float]:
    if (
        not isinstance(value, list)
        or len(value) != 2
        or any(
            isinstance(item, bool) or not isinstance(item, (int, float))
            for item in value
        )
    ):
        raise ValueError("candidate scale must contain two numbers")
    result = tuple(float(item) for item in value)
    if any(not math.isfinite(item) or item <= 0 for item in result):
        raise ValueError("candidate scale must be finite and positive")
    return result[0], result[1]


def _rendered_shape(
    stored: tuple[int, int], scale: tuple[float, float], source_group: int
) -> tuple[int, int]:
    downscale = math.ldexp(1.0, -source_group)
    height, width = stored
    scale_x, scale_y = scale
    return (
        max(1, math.floor(height * downscale / scale_y + 0.5)),
        max(1, math.floor(width * downscale / scale_x + 0.5)),
    )


def _normalized_candidate(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("named candidate must be an object")
    mesh = value.get("mesh")
    if not isinstance(mesh, str) or not mesh:
        raise ValueError("candidate mesh must be a non-empty string")
    matches = value.get("matches_canvas")
    if not isinstance(matches, bool):
        raise ValueError("candidate matches_canvas must be boolean")
    stored = _positive_int_pair(value.get("stored_shape"), "candidate stored_shape")
    scale = _scale_pair(value.get("scale"))
    predicted = _positive_int_pair(
        value.get("predicted_canvas"), "candidate predicted_canvas"
    )
    return {
        "mesh": mesh,
        "stored_shape": list(stored),
        "scale": list(scale),
        "predicted_canvas": list(predicted),
        "matches_canvas": matches,
    }


def _normalized_row(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("row must be an object")
    identity = value.get("identity")
    if not isinstance(identity, str) or "/segments/" not in identity or not identity:
        raise ValueError("row identity must be a full segment/surface identity")
    outcome = value.get("outcome")
    if outcome not in {"resolved", "missing", "ambiguous", "invalid_metadata"}:
        raise ValueError(f"invalid outcome for {identity}: {outcome!r}")
    selected = value.get("selected_mesh")
    if outcome == "resolved":
        if not isinstance(selected, str) or not selected:
            raise ValueError(f"resolved row lacks selected mesh: {identity}")
    elif selected is not None:
        raise ValueError(f"abstained row has a selected mesh: {identity}")

    if outcome == "invalid_metadata":
        return {
            "identity": identity,
            "outcome": outcome,
            "selected_mesh": None,
            "evidence": None,
        }

    source_group = value.get("source_group")
    if (
        isinstance(source_group, bool)
        or not isinstance(source_group, int)
        or not 0 <= source_group <= 63
    ):
        raise ValueError(f"invalid source_group for {identity}")
    reference = _positive_int_pair(value.get("reference_canvas"), "reference_canvas")
    level0 = _positive_int_pair(value.get("level0_canvas"), "level0_canvas")
    if reference != level0:
        raise ValueError(f"canvas mismatch for {identity}")
    volume_id = value.get("volume_id")
    if not isinstance(volume_id, str) or len(volume_id) != 14 or not volume_id.isdigit():
        raise ValueError(f"invalid volume id for {identity}")
    raw_candidates = value.get("named_candidates")
    if not isinstance(raw_candidates, list):
        raise ValueError(f"named_candidates must be a list for {identity}")
    candidates = sorted(
        (_normalized_candidate(candidate) for candidate in raw_candidates),
        key=lambda candidate: candidate["mesh"],
    )
    names = [candidate["mesh"] for candidate in candidates]
    if len(names) != len(set(names)):
        raise ValueError(f"duplicate named candidate for {identity}")
    for candidate in candidates:
        calculated = _rendered_shape(
            tuple(candidate["stored_shape"]),
            tuple(candidate["scale"]),
            source_group,
        )
        if list(calculated) != candidate["predicted_canvas"]:
            raise ValueError(f"candidate render arithmetic mismatch for {identity}")
        if candidate["matches_canvas"] != (calculated == reference):
            raise ValueError(f"candidate match flag mismatch for {identity}")
    valid = [candidate["mesh"] for candidate in candidates if candidate["matches_canvas"]]
    expected_outcome = (
        "resolved" if len(valid) == 1 else "ambiguous" if len(valid) > 1 else "missing"
    )
    if outcome != expected_outcome:
        raise ValueError(
            f"outcome/evidence mismatch for {identity}: {outcome} != {expected_outcome}"
        )
    if outcome == "resolved" and selected != valid[0]:
        raise ValueError(f"selected mesh/evidence mismatch for {identity}")
    return {
        "identity": identity,
        "outcome": outcome,
        "selected_mesh": selected,
        "evidence": {
            "volume_id": volume_id,
            "source_group": source_group,
            "reference_canvas": list(reference),
            "level0_canvas": list(level0),
            "named_candidates": candidates,
        },
    }


def _load_corpus(path: Path) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    payload = path.read_bytes()
    try:
        document = json.loads(payload, parse_constant=_reject_constant)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"invalid JSON in {path.name}: {exc}") from exc
    if not isinstance(document, dict) or not isinstance(document.get("rows"), list):
        raise ValueError(f"{path.name} must contain a rows list")
    table: dict[str, dict[str, Any]] = {}
    for index, raw_row in enumerate(document["rows"]):
        try:
            row = _normalized_row(raw_row)
        except Exception as exc:
            raise ValueError(f"{path.name} row {index}: {exc}") from exc
        identity = row["identity"]
        if identity in table:
            raise ValueError(f"{path.name}: duplicate identity: {identity}")
        table[identity] = row
    return table, {
        "name": path.name,
        "bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "rows": len(table),
    }


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant: {value}")


def compare_corpora(baseline_path: Path, refresh_path: Path) -> dict[str, Any]:
    errors: list[str] = []
    try:
        baseline, baseline_source = _load_corpus(baseline_path)
    except Exception as exc:
        baseline, baseline_source = {}, {
            "name": baseline_path.name,
            "bytes": None,
            "sha256": None,
            "rows": 0,
        }
        errors.append(f"baseline: {type(exc).__name__}: {exc}")
    try:
        refresh, refresh_source = _load_corpus(refresh_path)
    except Exception as exc:
        refresh, refresh_source = {}, {
            "name": refresh_path.name,
            "bytes": None,
            "sha256": None,
            "rows": 0,
        }
        errors.append(f"refresh: {type(exc).__name__}: {exc}")

    baseline_ids = set(baseline)
    refresh_ids = set(refresh)
    added = sorted(refresh_ids - baseline_ids)
    removed = sorted(baseline_ids - refresh_ids)
    outcome_changed: list[str] = []
    selection_changed: list[str] = []
    evidence_changed: list[str] = []
    for identity in sorted(baseline_ids & refresh_ids):
        before, after = baseline[identity], refresh[identity]
        if before["outcome"] != after["outcome"]:
            outcome_changed.append(identity)
        if before["selected_mesh"] != after["selected_mesh"]:
            selection_changed.append(identity)
        if before["evidence"] != after["evidence"]:
            evidence_changed.append(identity)

    safe_additions = [
        identity for identity in added if refresh[identity]["outcome"] == "resolved"
    ]
    baseline_non_resolved = sorted(
        identity for identity, row in baseline.items() if row["outcome"] != "resolved"
    )
    refresh_non_resolved = sorted(
        identity for identity, row in refresh.items() if row["outcome"] != "resolved"
    )
    changed = outcome_changed or selection_changed or evidence_changed
    if (
        errors
        or removed
        or changed
        or baseline_non_resolved
        or refresh_non_resolved
        or len(safe_additions) != len(added)
    ):
        classification = "NO-GO"
    elif added:
        classification = "SAFE_ADDITIONS"
    else:
        classification = "EXACT"
    return {
        "schema_version": 1,
        "method": "strict semantic comparison of independently verified frame evidence",
        "classification": classification,
        "sources": {"baseline": baseline_source, "refresh": refresh_source},
        "summary": {
            "baseline_rows": len(baseline),
            "refresh_rows": len(refresh),
            "added": len(added),
            "safe_additions": len(safe_additions),
            "removed": len(removed),
            "outcome_changed": len(outcome_changed),
            "selection_changed": len(selection_changed),
            "evidence_changed": len(evidence_changed),
            "baseline_non_resolved": len(baseline_non_resolved),
            "refresh_non_resolved": len(refresh_non_resolved),
            "errors": len(errors),
        },
        "changes": {
            "added": added,
            "safe_additions": safe_additions,
            "removed": removed,
            "outcome_changed": outcome_changed,
            "selection_changed": selection_changed,
            "evidence_changed": evidence_changed,
            "baseline_non_resolved": baseline_non_resolved,
            "refresh_non_resolved": refresh_non_resolved,
        },
        "errors": errors,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline", type=Path)
    parser.add_argument("refresh", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    result = compare_corpora(args.baseline, args.refresh)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical_json_bytes(result))
    print(json.dumps({"classification": result["classification"], **result["summary"]}, indent=2, sort_keys=True))
    return 0 if result["classification"] in {"EXACT", "SAFE_ADDITIONS"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
