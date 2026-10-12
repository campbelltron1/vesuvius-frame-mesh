#!/usr/bin/env python3
"""Run a frozen stratified frame-resolver plan through an installed command."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
from typing import Any, Sequence


def canonical_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def run_plan(
    plan_path: Path,
    command: Path,
    cache_dir: Path,
    *,
    base_url: str | None = None,
    budget_mib: int = 8,
) -> dict[str, Any]:
    plan_payload = plan_path.read_bytes()
    plan = json.loads(plan_payload)
    cases = plan.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("smoke plan must contain a non-empty cases list")
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    rows: list[dict[str, Any]] = []
    for case in cases:
        if not isinstance(case, dict):
            raise ValueError("each smoke case must be an object")
        scroll = case.get("scroll")
        segment = case.get("segment")
        surface = case.get("surface_volume")
        expected = case.get("expected_mesh")
        if not all(isinstance(value, str) and value for value in (scroll, segment, surface, expected)):
            raise ValueError("smoke case fields must be non-empty strings")
        invocation = [
            str(command),
            segment,
            surface,
            "--cache-dir",
            str(cache_dir),
            "--budget-mib",
            str(budget_mib),
        ]
        if base_url:
            invocation.extend(["--base-url", base_url])
        completed = subprocess.run(
            invocation,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
            env=env,
        )
        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            payload = {
                "status": "invalid_output",
                "error": f"JSONDecodeError: {exc}",
            }
        network = payload.get("network") if isinstance(payload, dict) else None
        network = network if isinstance(network, dict) else {}
        actual = payload.get("selected_mesh") if isinstance(payload, dict) else None
        passed = (
            completed.returncode == 0
            and payload.get("status") == "resolved"
            and actual == expected
        )
        rows.append(
            {
                "scroll": scroll,
                "segment": segment,
                "surface_volume": surface,
                "expected_mesh": expected,
                "selected_mesh": actual,
                "status": payload.get("status"),
                "exit_code": completed.returncode,
                "passed": passed,
                "network": {
                    key: int(network.get(key, 0))
                    for key in (
                        "downloaded_bytes",
                        "request_count",
                        "cache_hit_bytes",
                        "cache_hit_count",
                        "cache_invalid_bytes",
                        "cache_invalid_count",
                    )
                },
                "stderr": completed.stderr.strip() or None,
                "error": payload.get("error"),
            }
        )
    total_downloaded = sum(row["network"]["downloaded_bytes"] for row in rows)
    passed = sum(row["passed"] for row in rows)
    scrolls = {row["scroll"] for row in rows}
    decision = (
        "GO"
        if passed == len(rows)
        and len(scrolls) == len(rows)
        and total_downloaded < budget_mib * 1024 * 1024
        else "NO-GO"
    )
    return {
        "schema_version": 1,
        "method": "installed-wheel live smoke from frozen per-scroll plan",
        "selection_uses_labels": False,
        "plan": {
            "path": plan_path.name,
            "selection": plan.get("selection"),
            "source_manifest_sha256": plan.get("source_manifest_sha256"),
        },
        "command": command.name,
        "summary": {
            "decision": decision,
            "cases": len(rows),
            "scrolls": len(scrolls),
            "passed": passed,
            "failed": len(rows) - passed,
            "downloaded_bytes": total_downloaded,
            "request_count": sum(row["network"]["request_count"] for row in rows),
            "cache_hit_bytes": sum(row["network"]["cache_hit_bytes"] for row in rows),
            "cache_hit_count": sum(row["network"]["cache_hit_count"] for row in rows),
            "cache_invalid_bytes": sum(
                row["network"]["cache_invalid_bytes"] for row in rows
            ),
            "cache_invalid_count": sum(
                row["network"]["cache_invalid_count"] for row in rows
            ),
            "download_gate_bytes": budget_mib * 1024 * 1024,
        },
        "cases": rows,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--command", type=Path, required=True)
    parser.add_argument("--cache-dir", type=Path, required=True)
    parser.add_argument("--base-url")
    parser.add_argument("--budget-mib", type=int, default=8)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.budget_mib <= 0:
        parser.error("--budget-mib must be positive")
    result = run_plan(
        args.plan,
        args.command,
        args.cache_dir,
        base_url=args.base_url,
        budget_mib=args.budget_mib,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical_json_bytes(result))
    print(json.dumps(result["summary"], indent=2, sort_keys=True))
    return 0 if result["summary"]["decision"] == "GO" else 2


if __name__ == "__main__":
    raise SystemExit(main())
