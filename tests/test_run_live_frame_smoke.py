import json
from pathlib import Path
import sys
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from run_live_frame_smoke import run_plan


def _plan(tmp_path):
    path = tmp_path / "plan.json"
    path.write_text(
        json.dumps(
            {
                "selection": "frozen",
                "source_manifest_sha256": "abc",
                "cases": [
                    {
                        "scroll": "A",
                        "segment": "A/segments/one",
                        "surface_volume": "volume-20260101000000.zarr",
                        "expected_mesh": "one-on-20260101000000-x.tifxyz",
                    },
                    {
                        "scroll": "B",
                        "segment": "B/segments/two",
                        "surface_volume": "volume-20260202000000.zarr",
                        "expected_mesh": "two-on-20260202000000-x.tifxyz",
                    },
                ],
            }
        )
    )
    return path


def test_live_plan_go_requires_exact_expected_mesh(monkeypatch, tmp_path):
    def run(command, **_kwargs):
        segment = command[1]
        mesh = (
            "one-on-20260101000000-x.tifxyz"
            if segment.startswith("A/")
            else "two-on-20260202000000-x.tifxyz"
        )
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {
                    "status": "resolved",
                    "selected_mesh": mesh,
                    "network": {"downloaded_bytes": 10, "request_count": 1},
                }
            ),
            stderr="",
        )

    monkeypatch.setattr("run_live_frame_smoke.subprocess.run", run)
    result = run_plan(_plan(tmp_path), Path("installed-command"), tmp_path / "cache")
    assert result["summary"] == {
        "decision": "GO",
        "cases": 2,
        "scrolls": 2,
        "passed": 2,
        "failed": 0,
        "downloaded_bytes": 20,
        "request_count": 2,
        "cache_hit_bytes": 0,
        "cache_hit_count": 0,
        "cache_invalid_bytes": 0,
        "cache_invalid_count": 0,
        "download_gate_bytes": 8 * 1024 * 1024,
    }


def test_live_plan_mismatch_is_no_go(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "run_live_frame_smoke.subprocess.run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {
                    "status": "resolved",
                    "selected_mesh": "wrong.tifxyz",
                    "network": {},
                }
            ),
            stderr="",
        ),
    )
    result = run_plan(_plan(tmp_path), Path("installed-command"), tmp_path / "cache")
    assert result["summary"]["decision"] == "NO-GO"
    assert result["summary"]["passed"] == 0
