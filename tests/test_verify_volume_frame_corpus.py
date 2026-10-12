import copy
import json
import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from verify_volume_frame_corpus import verify_documents


SEGMENT = "PHerc0000/segments/example"
SURFACE = "2.4um-volume-20260101000000.zarr"
TARGET = "example-on-20260101000000-2.4um.tifxyz"


def _document():
    return {
        "git_sha": "abc",
        "segments": [
            {
                "segment": SEGMENT,
                "meshes": [
                    {"dir": TARGET, "stored_shape": [5, 10], "scale": [0.05, 0.05]},
                    {
                        "dir": "example-on-20260202000000-1.2um.tifxyz",
                        "stored_shape": [10, 20],
                        "scale": [0.05, 0.05],
                    },
                ],
                "surface_volumes": [
                    {
                        "surface_volume": SURFACE,
                        "source_group": 0,
                        "reference_shape": [100, 200],
                        "level0_shape": [65, 100, 200],
                        "status": "exact_target",
                        "exact_target_meshes": [TARGET],
                    }
                ],
            }
        ],
    }


def _run(tmp_path, document, *, name="audit.json"):
    path = tmp_path / name
    path.write_text(json.dumps(document))
    return verify_documents([path])


def test_recomputes_without_trusting_producer_decision_fields(tmp_path):
    document = _document()
    document["segments"][0]["surface_volumes"][0]["status"] = "unresolved"
    document["segments"][0]["surface_volumes"][0]["exact_target_meshes"] = []
    result = _run(tmp_path, document)
    assert result["rows"][0]["outcome"] == "resolved"
    assert result["rows"][0]["selected_mesh"] == TARGET
    assert result["rows"][0]["producer_agreement"] is False
    assert result["summary"]["decision"] == "NO-GO"


def test_clean_fixture_is_go_and_deterministic(tmp_path):
    first = _run(tmp_path, _document(), name="b.json")
    second_path = tmp_path / "a.json"
    second_path.write_text(json.dumps({"segments": []}))
    with_empty = verify_documents([tmp_path / "b.json", second_path])
    assert first["summary"]["decision"] == "GO"
    assert first["summary"]["counts"] == {"resolved": 1}
    assert with_empty["rows"] == first["rows"]
    assert with_empty["summary"]["manifest_sha256"] == first["summary"]["manifest_sha256"]


@pytest.mark.parametrize(
    "mutate, expected",
    [
        (
            lambda d: d["segments"][0]["surface_volumes"][0].update(
                surface_volume="2.4um-volume-20260101000001.zarr"
            ),
            "missing",
        ),
        (
            lambda d: d["segments"][0]["surface_volumes"][0].update(source_group=1),
            "missing",
        ),
        (
            lambda d: d["segments"][0]["surface_volumes"][0].update(
                reference_shape=[101, 200], level0_shape=[65, 101, 200]
            ),
            "missing",
        ),
        (
            lambda d: d["segments"][0]["meshes"][0].update(stored_shape=[6, 10]),
            "missing",
        ),
        (
            lambda d: d["segments"][0]["meshes"][0].update(scale=[0.04, 0.05]),
            "missing",
        ),
    ],
)
def test_mutations_are_detected(tmp_path, mutate, expected):
    document = _document()
    mutate(document)
    result = _run(tmp_path, document)
    assert result["rows"][0]["outcome"] == expected
    assert result["summary"]["decision"] == "NO-GO"


def test_candidate_multiplicity_is_ambiguous(tmp_path):
    document = _document()
    duplicate = copy.deepcopy(document["segments"][0]["meshes"][0])
    duplicate["dir"] = "example-v2-on-20260101000000-2.4um.tifxyz"
    document["segments"][0]["meshes"].append(duplicate)
    result = _run(tmp_path, document)
    assert result["rows"][0]["outcome"] == "ambiguous"
    assert result["summary"]["decision"] == "NO-GO"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d["segments"][0]["meshes"][0].update(scale=[math.nan, 0.05]),
        lambda d: d["segments"][0]["meshes"][0].update(scale=[0.0, 0.05]),
        lambda d: d["segments"][0]["surface_volumes"][0].update(source_group=-1),
        lambda d: d["segments"][0]["surface_volumes"][0].update(
            level0_shape=[65, 99, 200]
        ),
    ],
)
def test_malformed_evidence_fails_closed(tmp_path, mutate):
    document = _document()
    mutate(document)
    result = _run(tmp_path, document)
    assert result["summary"]["decision"] == "NO-GO"
    if result["rows"]:
        assert result["rows"][0]["outcome"] == "invalid_metadata"
    else:
        # Non-standard JSON constants are rejected before any row is trusted.
        assert result["summary"]["global_errors"] == 1


def test_duplicate_surface_identity_fails_closed(tmp_path):
    document = _document()
    document["segments"][0]["surface_volumes"].append(
        copy.deepcopy(document["segments"][0]["surface_volumes"][0])
    )
    result = _run(tmp_path, document)
    assert result["summary"]["decision"] == "NO-GO"
    assert result["summary"]["counts"] == {
        "invalid_metadata": 1,
        "resolved": 1,
    }
    assert result["rows"][1]["error"] == "duplicate surface identity"


def test_duplicate_mesh_directory_fails_closed(tmp_path):
    document = _document()
    document["segments"][0]["meshes"].append(
        copy.deepcopy(document["segments"][0]["meshes"][0])
    )
    result = _run(tmp_path, document)
    assert result["summary"]["decision"] == "NO-GO"
    assert result["summary"]["global_errors"] == 1
