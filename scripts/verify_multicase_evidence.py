#!/usr/bin/env python3
"""Verify internal consistency of the packaged eight-case evidence."""

from __future__ import annotations

import json
import math
import statistics
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXPECTED = [
    "20250108000005-w030_2025010818",
    "20250831000000-w040_2025083102",
    "20260108000000-w041_2026010816",
    "20260112000000-w043_2026011217",
    "20260115000000-w044_2026011522",
    "20260126000000-w045_2026012619",
    "20260302000000-w039_2026030210",
    "20260317000000-w035_2026031718",
]


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def close(actual: float, expected: float) -> bool:
    return math.isclose(actual, expected, rel_tol=0, abs_tol=1e-15)


def verify(root: Path = ROOT) -> dict[str, object]:
    evidence = root / "evidence/multicase"
    aggregate = load(evidence / "frame_resolver_multicase.json")
    case_rows = [load(evidence / "cases" / f"{segment}.json") for segment in EXPECTED]
    if [row["segment"] for row in case_rows] != EXPECTED:
        raise ValueError("case order or membership changed")
    if aggregate["cases"] != case_rows or not aggregate["complete"]:
        raise ValueError("aggregate cases do not exactly match packaged case files")
    for row in case_rows:
        for digest in row["sha256"].values():
            if len(digest) != 64 or any(
                character not in "0123456789abcdef" for character in digest
            ):
                raise ValueError(f"invalid prediction hash in {row['segment']}")

    deltas = [float(row["paired_auc_delta"]) for row in case_rows]
    summary = aggregate["aggregate"]
    checks = {
        "mean": close(sum(deltas) / len(deltas), summary["mean_paired_auc_delta"]),
        "median": close(statistics.median(deltas), summary["median_paired_auc_delta"]),
        "positive": sum(value > 0 for value in deltas) == summary["positive_cases"],
        "worst": close(min(deltas), summary["worst_paired_auc_delta"]),
        "decision": summary["decision"] == "GO",
    }

    sensitivity = load(evidence / "frame_resolver_sensitivity.json")
    configurations = sensitivity["alignment_search"] + sensitivity["evaluation_masks"]
    checks.update(
        {
            "sensitivity_count": len(configurations) == 36,
            "sensitivity_all_go": all(
                row["aggregate"]["decision"] == "GO" for row in configurations
            ),
            "sensitivity_decision": sensitivity["summary"]["decision"] == "ROBUST",
            "hash_checks": all(row["match"] for row in sensitivity["hash_checks"]),
        }
    )
    displacement = load(evidence / "frame_resolver_displacement.json")
    checks.update(
        {
            "displacement_cases": len(displacement["cases"]) == 8,
            "case_studies": len(displacement["case_studies"]) == 2,
            "figures": all(
                (root / relative).is_file()
                for relative in (
                    "evidence/figures/frame_resolver_multicase_deltas.png",
                    "evidence/figures/frame_resolver_case_studies.png",
                )
            ),
        }
    )
    if not all(checks.values()):
        failed = sorted(key for key, value in checks.items() if not value)
        raise ValueError(f"multi-case evidence verification failed: {failed}")
    return {"decision": "GO", "cases": 8, "configurations": 36, "checks": checks}


def main() -> int:
    print(json.dumps(verify(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
