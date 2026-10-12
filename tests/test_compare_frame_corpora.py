import copy
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from compare_frame_corpora import compare_corpora


def _row(identity="PHerc0000/segments/example/surface-volumes/volume-20260101000000.zarr"):
    mesh = "mesh-on-20260101000000-2.4um.tifxyz"
    return {
        "identity": identity,
        "volume_id": "20260101000000",
        "source_group": 0,
        "reference_canvas": [100, 200],
        "level0_canvas": [100, 200],
        "named_candidates": [
            {
                "mesh": mesh,
                "stored_shape": [5, 10],
                "scale": [0.05, 0.05],
                "predicted_canvas": [100, 200],
                "matches_canvas": True,
            }
        ],
        "outcome": "resolved",
        "selected_mesh": mesh,
    }


def _write(path, rows):
    path.write_text(json.dumps({"rows": rows}))


def _compare(tmp_path, before, after):
    baseline = tmp_path / "baseline.json"
    refresh = tmp_path / "refresh.json"
    _write(baseline, before)
    _write(refresh, after)
    return compare_corpora(baseline, refresh)


def test_exact_semantic_replay_ignores_row_and_candidate_order(tmp_path):
    first, second = _row(), _row(
        "PHerc0001/segments/example/surface-volumes/volume-20260101000000.zarr"
    )
    result = _compare(tmp_path, [first, second], [second, first])
    assert result["classification"] == "EXACT"


def test_uniquely_resolved_addition_is_safe(tmp_path):
    added = _row(
        "PHerc0001/segments/example/surface-volumes/volume-20260101000000.zarr"
    )
    result = _compare(tmp_path, [_row()], [_row(), added])
    assert result["classification"] == "SAFE_ADDITIONS"
    assert result["summary"]["safe_additions"] == 1


def test_removed_row_is_no_go(tmp_path):
    result = _compare(tmp_path, [_row()], [])
    assert result["classification"] == "NO-GO"
    assert result["summary"]["removed"] == 1


def test_outcome_change_is_detected(tmp_path):
    changed = copy.deepcopy(_row())
    changed["named_candidates"][0]["stored_shape"] = [4, 10]
    changed["named_candidates"][0]["predicted_canvas"] = [80, 200]
    changed["named_candidates"][0]["matches_canvas"] = False
    changed["outcome"] = "missing"
    changed["selected_mesh"] = None
    result = _compare(tmp_path, [_row()], [changed])
    assert result["classification"] == "NO-GO"
    assert result["summary"]["outcome_changed"] == 1


def test_selection_change_is_detected(tmp_path):
    changed = copy.deepcopy(_row())
    changed["named_candidates"][0]["mesh"] = "replacement-on-20260101000000.tifxyz"
    changed["selected_mesh"] = changed["named_candidates"][0]["mesh"]
    result = _compare(tmp_path, [_row()], [changed])
    assert result["classification"] == "NO-GO"
    assert result["summary"]["selection_changed"] == 1


def test_evidence_change_is_detected(tmp_path):
    changed = copy.deepcopy(_row())
    changed["source_group"] = 1
    changed["reference_canvas"] = [50, 100]
    changed["level0_canvas"] = [50, 100]
    changed["named_candidates"][0]["predicted_canvas"] = [50, 100]
    result = _compare(tmp_path, [_row()], [changed])
    assert result["classification"] == "NO-GO"
    assert result["summary"]["evidence_changed"] == 1


def test_unchanged_non_resolved_row_is_still_no_go(tmp_path):
    missing = copy.deepcopy(_row())
    missing["named_candidates"][0]["stored_shape"] = [4, 10]
    missing["named_candidates"][0]["predicted_canvas"] = [80, 200]
    missing["named_candidates"][0]["matches_canvas"] = False
    missing["outcome"] = "missing"
    missing["selected_mesh"] = None
    result = _compare(tmp_path, [missing], [missing])
    assert result["classification"] == "NO-GO"
    assert result["summary"]["refresh_non_resolved"] == 1


@pytest.mark.parametrize(
    "mutator",
    [
        lambda rows: rows.append(copy.deepcopy(rows[0])),
        lambda rows: rows[0].update(source_group=True),
        lambda rows: rows[0]["named_candidates"][0].update(scale=["0.05", 0.05]),
    ],
)
def test_duplicate_or_malformed_refresh_fails_closed(tmp_path, mutator):
    rows = [_row()]
    mutator(rows)
    result = _compare(tmp_path, [_row()], rows)
    assert result["classification"] == "NO-GO"
    assert result["summary"]["errors"] == 1
