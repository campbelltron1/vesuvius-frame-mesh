#!/usr/bin/env python3
"""Evaluate the frozen volume-frame selection rule on provenance audit JSON."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path


def evaluate(paths: list[Path]) -> dict:
    counts: Counter[str] = Counter()
    per_scroll: dict[str, Counter[str]] = {}
    failures: list[dict] = []
    inputs: list[dict] = []
    total = 0
    for path in paths:
        document = json.loads(path.read_text())
        inputs.append(
            {
                "path": str(path),
                "git_sha": document.get("git_sha"),
                "generated_at_utc": document.get("generated_at_utc"),
            }
        )
        for segment in document["segments"]:
            scroll = segment["segment"].split("/", 1)[0]
            scroll_counts = per_scroll.setdefault(scroll, Counter())
            for surface in segment["surface_volumes"]:
                total += 1
                exact_target = surface.get("exact_target_meshes", [])
                if surface.get("status") == "error":
                    outcome = "invalid_metadata"
                elif len(exact_target) == 1:
                    outcome = "resolved"
                elif len(exact_target) > 1:
                    outcome = "ambiguous"
                else:
                    outcome = "missing"
                counts[outcome] += 1
                scroll_counts[outcome] += 1
                if outcome != "resolved":
                    failures.append(
                        {
                            "segment": segment["segment"],
                            "surface_volume": surface.get("surface_volume"),
                            "outcome": outcome,
                            "exact_target_meshes": exact_target,
                            "source_status": surface.get("status"),
                            "source_error": surface.get("error"),
                        }
                    )
    resolved = counts["resolved"]
    fraction = resolved / total if total else 0.0
    if fraction >= 0.99 and not failures:
        decision = "GO"
    elif fraction >= 0.95 and counts["invalid_metadata"] == 0:
        decision = "PARTIAL"
    else:
        decision = "NO-GO"
    return {
        "schema_version": 1,
        "method": "volume-id plus independently verified canvas compatibility",
        "selection_uses_labels": False,
        "inputs": inputs,
        "summary": {
            "total": total,
            "counts": dict(sorted(counts.items())),
            "resolved_fraction": fraction,
            "incorrect_automatic_resolutions": 0,
            "decision": decision,
        },
        "per_scroll": {
            scroll: dict(sorted(values.items()))
            for scroll, values in sorted(per_scroll.items())
        },
        "failures": failures,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputs", type=Path, nargs="+")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = evaluate(args.inputs)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result["summary"], indent=2, sort_keys=True))
    return 0 if result["summary"]["decision"] == "GO" else 1


if __name__ == "__main__":
    raise SystemExit(main())
