#!/usr/bin/env python3
"""Build, install, test, and hash a clean frame-resolver release repository."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
VERSION = "0.1.3"
ARTIFACT = f"vesuvius_frame_mesh-{VERSION}"


def run(command: list[str], *, env: dict[str, str] | None = None) -> str:
    result = subprocess.run(
        command,
        cwd=ROOT,
        env=env,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    if result.stdout:
        print(result.stdout, end="")
    return result.stdout


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def update_hashes() -> None:
    rows = []
    for path in sorted(ROOT.rglob("*")):
        relative = path.relative_to(ROOT)
        if (
            not path.is_file()
            or path.name in {"SHA256SUMS", ".generated-by-build_submission_packages"}
            or ".git" in path.parts
            or "__pycache__" in path.parts
            or path.suffix == ".pyc"
            or relative.parts[0] == "dist"
        ):
            continue
        rows.append(f"{sha256(path)}  {relative.as_posix()}")
    (ROOT / "SHA256SUMS").write_text("\n".join(rows) + "\n", encoding="utf-8")


def main() -> int:
    uv = shutil.which("uv")
    if uv is None:
        raise RuntimeError("uv is required for the deterministic offline build")
    env = os.environ.copy()
    env.update(
        {
            "SOURCE_DATE_EPOCH": "1791619200",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONPATH": "src",
            "OMP_NUM_THREADS": "1",
            "OPENBLAS_NUM_THREADS": "1",
            "MKL_NUM_THREADS": "1",
            "VECLIB_MAXIMUM_THREADS": "1",
            "NUMEXPR_NUM_THREADS": "1",
        }
    )
    with tempfile.TemporaryDirectory(prefix="vesuvius-frame-public-") as tmp_name:
        tmp = Path(tmp_name)
        build_a = tmp / "build-a"
        build_b = tmp / "build-b"
        build_a.mkdir()
        build_b.mkdir()
        run(
            [
                uv,
                "build",
                "--offline",
                "--no-build-isolation",
                "--no-build-logs",
                "--out-dir",
                str(build_a),
                ".",
            ],
            env=env,
        )
        run(
            [
                uv,
                "build",
                "--offline",
                "--no-build-isolation",
                "--no-build-logs",
                "--out-dir",
                str(build_b),
                ".",
            ],
            env=env,
        )
        wheel_name = f"{ARTIFACT}-py3-none-any.whl"
        sdist_name = f"{ARTIFACT}.tar.gz"
        run(
            [
                sys.executable,
                "scripts/normalize_sdist.py",
                str(build_a / sdist_name),
                str(build_b / sdist_name),
            ],
            env=env,
        )
        if (build_a / wheel_name).read_bytes() != (build_b / wheel_name).read_bytes():
            raise RuntimeError("wheel builds differ")
        if (build_a / sdist_name).read_bytes() != (build_b / sdist_name).read_bytes():
            raise RuntimeError("normalized sdist builds differ")

        venv = tmp / "venv"
        run([sys.executable, "-m", "venv", str(venv)], env=env)
        venv_python = venv / "bin/python"
        run(
            [
                str(venv_python),
                "-m",
                "pip",
                "install",
                "--no-index",
                "--no-deps",
                str(build_a / wheel_name),
            ],
            env={key: value for key, value in env.items() if key != "PYTHONPATH"},
        )
        single_command = venv / "bin/vesuvius-frame-mesh"
        batch_command = venv / "bin/vesuvius-frame-mesh-batch"
        run([str(single_command), "--help"], env=env)
        run([str(batch_command), "--help"], env=env)
        test_env = env | {
            "VESUVIUS_FRAME_MESH_COMMAND": str(single_command),
            "VESUVIUS_FRAME_MESH_BATCH_COMMAND": str(batch_command),
        }
        test_output = run(
            [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests"],
            env=test_env,
        )
        if "112 passed" not in test_output:
            raise RuntimeError("unexpected exported test count")

        replay = tmp / "independent-verification.json"
        run(
            [
                sys.executable,
                "src/verify_volume_frame_corpus.py",
                "evidence/corpus/F_canvas_provenance_pherc0139.json",
                "evidence/corpus/F_canvas_provenance_validation.json",
                "evidence/corpus/F_canvas_provenance_controls.json",
                "--output",
                str(replay),
            ],
            env=env,
        )
        frozen = ROOT / "evidence/results/J1_volume_frame_independent_verification.json"
        if replay.read_bytes() != frozen.read_bytes():
            raise RuntimeError("independent 650-row verification changed")

        dist = ROOT / "dist"
        dist.mkdir(exist_ok=True)
        shutil.copy2(build_a / wheel_name, dist / wheel_name)
        shutil.copy2(build_a / sdist_name, dist / sdist_name)
        (dist / "SHA256SUMS").write_text(
            f"{sha256(dist / wheel_name)}  {wheel_name}\n"
            f"{sha256(dist / sdist_name)}  {sdist_name}\n",
            encoding="utf-8",
        )
        result = {
            "schema_version": 1,
            "decision": "GO",
            "version": VERSION,
            "tests": {"passed": 112},
            "independent_corpus": {
                "cases": 650,
                "comparison": "EXACT",
                "sha256": sha256(replay),
            },
            "artifacts": {
                "wheel": {
                    "name": wheel_name,
                    "bytes": (dist / wheel_name).stat().st_size,
                    "sha256": sha256(dist / wheel_name),
                    "two_builds_identical": True,
                },
                "sdist": {
                    "name": sdist_name,
                    "bytes": (dist / sdist_name).stat().st_size,
                    "sha256": sha256(dist / sdist_name),
                    "two_normalized_builds_identical": True,
                },
            },
        }
        (ROOT / "release_verification.json").write_text(
            json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    update_hashes()
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
