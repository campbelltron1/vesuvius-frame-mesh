#!/usr/bin/env python3
"""Verify the frozen 42-case extension and its separation from the main corpus."""

from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXPECTATIONS = ROOT / "evidence/extension/HP7_extension_expectations.json"
RESULTS = ROOT / "evidence/extension/HP7_extension_result.json"
PRIMARY = ROOT / "evidence/results/J1_volume_frame_independent_verification.json"

EXPECTED_SHA256 = {
    EXPECTATIONS: "2e20f58e0bdf2be1f09cbca280cee65efdf18cff2977059b280cea97ceb50197",
    RESULTS: "b9411f5e73db9e0501a3aa53a1c22241489196b7e502798d8c8fb7907818293a",
    PRIMARY: "b1fec8095406da8f2a2bf8c760e942e82cda25a6ffd9207aacfe92c7e52d5420",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load(path: Path) -> dict:
    if sha256(path) != EXPECTED_SHA256[path]:
        raise RuntimeError(f"frozen evidence hash changed: {path.relative_to(ROOT)}")
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    expectations = load(EXPECTATIONS)
    results = load(RESULTS)
    primary = load(PRIMARY)

    expected_rows = expectations.get("cases")
    result_rows = results.get("cases")
    primary_rows = primary.get("rows")
    if not isinstance(expected_rows, list) or len(expected_rows) != 42:
        raise RuntimeError("expected exactly 42 frozen extension expectations")
    if not isinstance(result_rows, list) or len(result_rows) != 42:
        raise RuntimeError("expected exactly 42 frozen extension results")
    if not isinstance(primary_rows, list) or len(primary_rows) != 650:
        raise RuntimeError("expected exactly 650 primary corpus rows")

    expected = {row["id"]: row["expected_mesh"] for row in expected_rows}
    observed = {row["id"]: row for row in result_rows}
    if len(expected) != 42 or len(observed) != 42:
        raise RuntimeError("duplicate extension identities")
    if set(expected) != set(observed):
        raise RuntimeError("extension expectation/result identity sets differ")

    errors: list[str] = []
    for identity, expected_mesh in expected.items():
        row = observed[identity]
        result = row.get("result", {})
        if result.get("status") != "resolved":
            errors.append(f"{identity}: status is not resolved")
        if result.get("selection_uses_labels") is not False:
            errors.append(f"{identity}: selection is not explicitly label-free")
        if result.get("selected_mesh") != expected_mesh:
            errors.append(f"{identity}: selected mesh differs from frozen expectation")
        if result.get("valid_candidate_count") != 1:
            errors.append(f"{identity}: valid candidate count is not one")
    if errors:
        raise RuntimeError("; ".join(errors))

    if results.get("selection_uses_labels") is not False:
        raise RuntimeError("extension result is not explicitly label-free")
    summary = results.get("summary", {})
    if summary.get("decision") != "GO" or summary.get("status_counts") != {"resolved": 42}:
        raise RuntimeError("frozen extension summary changed")

    sample_counts = Counter(identity.split("/", 1)[0] for identity in expected)
    if sample_counts != {"PHerc0009B": 36, "PHerc0841": 6}:
        raise RuntimeError(f"unexpected extension sample counts: {dict(sample_counts)}")

    primary_identities = {row.get("identity") for row in primary_rows}
    if len(primary_identities) != 650 or None in primary_identities:
        raise RuntimeError("primary corpus identities are missing or duplicated")
    overlap = set(expected) & primary_identities
    if overlap:
        raise RuntimeError(f"extension overlaps primary corpus: {sorted(overlap)[:3]}")

    primary_samples = {identity.split("/", 1)[0] for identity in primary_identities}
    combined_samples = primary_samples | set(sample_counts)
    if len(primary_samples) != 9 or len(combined_samples) != 11:
        raise RuntimeError("unexpected primary or combined sample count")

    report = {
        "schema_version": 1,
        "decision": "GO",
        "selection_uses_labels": False,
        "primary_cases": 650,
        "extension_cases": 42,
        "combined_cases": 692,
        "primary_samples": len(primary_samples),
        "extension_sample_counts": dict(sorted(sample_counts.items())),
        "combined_samples": len(combined_samples),
        "extension_primary_overlap": 0,
        "exact_expected_selections": 42,
        "hashes": {
            str(path.relative_to(ROOT)): expected_hash
            for path, expected_hash in EXPECTED_SHA256.items()
        },
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
