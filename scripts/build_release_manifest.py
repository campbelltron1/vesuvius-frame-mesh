#!/usr/bin/env python3
"""Create or verify a deterministic frame-resolver release manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tomllib
from typing import Any, Sequence


SOURCE_DATE_EPOCH = 1791619200
REPOSITORY_ARTIFACTS = (
    "LICENSE",
    "MANIFEST.in",
    "FRAME_RESOLVER.md",
    "pyproject.toml",
    "src/canvas_provenance.py",
    "src/resolve_volume_frame_batch.py",
    "src/resolve_volume_frame_mesh.py",
    "src/verify_volume_frame_corpus.py",
    "src/compare_frame_corpora.py",
    "src/benchmark_batch_resolver.py",
    "src/run_live_frame_smoke.py",
    "src/run_python_compatibility.py",
    "src/build_release_manifest.py",
    "src/normalize_sdist.py",
    "tests/test_canvas_provenance.py",
    "tests/test_resolve_volume_frame_mesh.py",
    "tests/test_resolve_volume_frame_batch.py",
    "tests/test_frame_resolver_http_e2e.py",
    "tests/test_verify_volume_frame_corpus.py",
    "tests/test_compare_frame_corpora.py",
    "tests/test_run_live_frame_smoke.py",
    "tests/test_build_release_manifest.py",
    "tests/test_normalize_sdist.py",
    "runs/J5_verify_resolver_release.sh",
    "runs/K5_python_compatibility.sh",
    "runs/L4_verify_resolver_release.sh",
    "runs/N4_verify_resolver_release.sh",
    "results/F_canvas_provenance.json",
    "results/F_canvas_provenance_pherc0139.json",
    "results/F_canvas_provenance_validation.json",
    "results/F_canvas_provenance_controls.json",
    "results/J1_volume_frame_independent_verification.json",
    "results/J2_frame_resolver_package.json",
    "results/J3_frame_resolver_http_contract.json",
    "results/J4_metadata_cache_atomicity.json",
    "results/J5_frame_resolver_release.json",
    "results/K1_installed_wheel_live_smoke.json",
    "results/K1_live_smoke_plan.json",
    "results/K2_strict_http_range_contract.json",
    "results/K3_strict_numeric_metadata_contract.json",
    "results/K4_canvas_provenance_refresh.json",
    "results/K4_canvas_provenance_refresh_verification.json",
    "results/K4_corpus_drift_comparison.json",
    "results/K4_corpus_drift_result.json",
    "results/K5_python_compatibility.json",
    "results/L1_cached_range_integrity.json",
    "results/L2_final_hardened_live_smoke.json",
    "results/L3_batch_benchmark.json",
    "results/L3_batch_resolver_result.json",
    "results/L3_live_batch_plan.json",
    "results/L3_live_batch_result.json",
    "results/M1_full_batch_expectations.json",
    "results/M1_full_batch_plan.json",
    "results/M1_full_batch_plan_result.json",
    "results/M2_full_batch_after.json",
    "results/M2_full_batch_before.json",
    "results/M2_segment_discovery_cache_result.json",
    "results/M3_batch_expectation_comparator_result.json",
    "results/M3_warm_full_batch_comparison.json",
    "results/M3_warm_full_batch_result.json",
    "results/M4_installed_full_batch_comparison.json",
    "results/M4_installed_full_batch_prefetch_result.json",
    "results/M4_installed_full_batch_result.json",
    "results/M4_installed_full_corpus_replay.json",
    "results/M5_full_corpus_draft_refresh.json",
    "results/N1_native_parallel_result.json",
    "results/N2_checkpoint_resume_result.json",
    "results/N3_installed_native_full_batch_comparison.json",
    "results/N3_installed_native_full_batch_result.json",
    "results/N3_installed_native_full_corpus_replay.json",
    "docs/G_VOLUME_FRAME_MESH_RESOLVER_PREREG.md",
    "docs/G_VOLUME_FRAME_MESH_RESOLVER_RESULT.md",
    "docs/J1_INDEPENDENT_FRAME_VERIFIER_RESULT.md",
    "docs/J2_FRAME_RESOLVER_PACKAGE_RESULT.md",
    "docs/J3_FRAME_RESOLVER_HTTP_CONTRACT_RESULT.md",
    "docs/J4_METADATA_CACHE_ATOMICITY_RESULT.md",
    "docs/J5_FRAME_RESOLVER_RELEASE_RESULT.md",
    "docs/J_RESOLVER_RELEASE_PREREG.md",
    "docs/K1_INSTALLED_WHEEL_LIVE_SMOKE_RESULT.md",
    "docs/K2_STRICT_HTTP_RANGE_RESULT.md",
    "docs/K3_STRICT_NUMERIC_METADATA_RESULT.md",
    "docs/K4_CORPUS_DRIFT_RESULT.md",
    "docs/K5_PYTHON_COMPATIBILITY_RESULT.md",
    "docs/K_RESOLVER_OVERFLOW_PREREG.md",
    "docs/L1_CACHED_RANGE_INTEGRITY_RESULT.md",
    "docs/L2_FINAL_HARDENED_LIVE_SMOKE_RESULT.md",
    "docs/L3_BATCH_RESOLVER_RESULT.md",
    "docs/L_RESOLVER_PUBLICATION_PREREG.md",
    "docs/M1_FULL_BATCH_PLAN_RESULT.md",
    "docs/M2_SEGMENT_DISCOVERY_CACHE_RESULT.md",
    "docs/M3_BATCH_EXPECTATION_COMPARATOR_RESULT.md",
    "docs/M4_INSTALLED_FULL_CORPUS_REPLAY_RESULT.md",
    "docs/M5_FULL_CORPUS_DRAFT_REFRESH_RESULT.md",
    "docs/M_FULL_CORPUS_BATCH_PREREG.md",
    "docs/N1_NATIVE_PARALLEL_RESULT.md",
    "docs/N2_CHECKPOINT_RESUME_RESULT.md",
    "docs/N3_INSTALLED_NATIVE_FULL_CORPUS_RESULT.md",
    "docs/N_NATIVE_PARALLEL_RESUME_PREREG.md",
)
DIST_PATTERNS = (
    "vesuvius_frame_mesh-*.tar.gz",
    "vesuvius_frame_mesh-*-py3-none-any.whl",
)


def canonical_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _record(path: Path, display_path: str, kind: str) -> dict[str, Any]:
    payload = path.read_bytes()
    return {
        "path": display_path,
        "kind": kind,
        "bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def _git_sha(root: Path) -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=root, text=True
    ).strip()


def _project_version(root: Path) -> str:
    try:
        value = tomllib.loads((root / "pyproject.toml").read_text())["project"][
            "version"
        ]
    except (OSError, KeyError, tomllib.TOMLDecodeError) as exc:
        raise ValueError(f"cannot read project version: {exc}") from exc
    if not isinstance(value, str) or not value:
        raise ValueError("project version must be a non-empty string")
    return value


def build_manifest(root: Path, dist_dir: Path, *, git_sha: str | None = None) -> dict:
    root = root.resolve()
    dist_dir = dist_dir.resolve()
    version = _project_version(root)
    records: list[dict[str, Any]] = []
    for relative in REPOSITORY_ARTIFACTS:
        path = root / relative
        if not path.is_file():
            raise ValueError(f"required release file is missing: {relative}")
        records.append(_record(path, relative, "repository"))
    seen_dist: set[Path] = set()
    for pattern in DIST_PATTERNS:
        matches = sorted(dist_dir.glob(pattern))
        if len(matches) != 1:
            raise ValueError(
                f"expected exactly one distribution matching {pattern!r}, "
                f"found {len(matches)}"
            )
        path = matches[0]
        if path in seen_dist:
            raise ValueError(f"distribution matched more than once: {path.name}")
        if not path.name.startswith(f"vesuvius_frame_mesh-{version}"):
            raise ValueError(
                f"distribution version does not match pyproject.toml: {path.name}"
            )
        seen_dist.add(path)
        records.append(_record(path, f"dist/{path.name}", "distribution"))
    records.sort(key=lambda row: row["path"])
    manifest_digest = hashlib.sha256(canonical_json_bytes(records)).hexdigest()
    return {
        "schema_version": 1,
        "project": "vesuvius-frame-mesh",
        "version": version,
        "source_git_sha": git_sha or _git_sha(root),
        "source_date_epoch": SOURCE_DATE_EPOCH,
        "artifact_count": len(records),
        "artifact_manifest_sha256": manifest_digest,
        "artifacts": records,
    }


def verify_manifest(root: Path, dist_dir: Path, manifest: dict) -> list[str]:
    errors: list[str] = []
    if manifest.get("schema_version") != 1:
        errors.append("unsupported schema_version")
    try:
        expected_version = _project_version(root)
    except ValueError as exc:
        errors.append(str(exc))
    else:
        if manifest.get("version") != expected_version:
            errors.append("manifest version does not match pyproject.toml")
    records = manifest.get("artifacts")
    if not isinstance(records, list):
        return errors + ["artifacts must be a list"]
    paths = [row.get("path") for row in records if isinstance(row, dict)]
    if len(paths) != len(records) or len(paths) != len(set(paths)):
        errors.append("artifact paths must be present and unique")
    for row in records:
        if not isinstance(row, dict) or not isinstance(row.get("path"), str):
            continue
        relative = row["path"]
        if (
            row.get("kind") == "distribution"
            and isinstance(manifest.get("version"), str)
            and not relative.startswith(
                f"dist/vesuvius_frame_mesh-{manifest['version']}"
            )
        ):
            errors.append(f"distribution version mismatch: {relative}")
        path = (
            dist_dir / relative.removeprefix("dist/")
            if relative.startswith("dist/")
            else root / relative
        )
        if not path.is_file():
            errors.append(f"missing: {relative}")
            continue
        actual = _record(path, relative, row.get("kind", "unknown"))
        if actual["bytes"] != row.get("bytes"):
            errors.append(f"size mismatch: {relative}")
        if actual["sha256"] != row.get("sha256"):
            errors.append(f"sha256 mismatch: {relative}")
    digest = hashlib.sha256(canonical_json_bytes(records)).hexdigest()
    if digest != manifest.get("artifact_manifest_sha256"):
        errors.append("artifact manifest digest mismatch")
    return errors


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    create = subparsers.add_parser("create")
    create.add_argument("--root", type=Path, default=Path("."))
    create.add_argument("--dist-dir", type=Path, required=True)
    create.add_argument("--git-sha")
    create.add_argument("--output", type=Path, required=True)
    verify = subparsers.add_parser("verify")
    verify.add_argument("--root", type=Path, default=Path("."))
    verify.add_argument("--dist-dir", type=Path, required=True)
    verify.add_argument("--manifest", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "create":
        result = build_manifest(args.root, args.dist_dir, git_sha=args.git_sha)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_bytes(canonical_json_bytes(result))
        print(
            json.dumps(
                {
                    "artifact_count": result["artifact_count"],
                    "artifact_manifest_sha256": result[
                        "artifact_manifest_sha256"
                    ],
                },
                sort_keys=True,
            )
        )
        return 0
    manifest = json.loads(args.manifest.read_text())
    errors = verify_manifest(args.root.resolve(), args.dist_dir.resolve(), manifest)
    print(json.dumps({"valid": not errors, "errors": errors}, sort_keys=True))
    return 0 if not errors else 2


if __name__ == "__main__":
    raise SystemExit(main())
