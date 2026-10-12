import json
import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from canvas_provenance import Listing
from evaluate_volume_frame_resolver import evaluate
from resolve_volume_frame_mesh import (
    EXIT_AMBIGUOUS,
    EXIT_INVALID_METADATA,
    EXIT_MISSING,
    EXIT_RESOLVED,
    exit_code,
    normalize_segment,
    normalize_surface_volume,
    resolve_volume_frame_mesh,
    _scale_pair,
)


SEGMENT = "PHerc0000/segments/example-w001"
SURFACE = "2.399um-volume-20260101000000.zarr"


class FakeClient:
    base_url = "https://example.test"

    def __init__(
        self,
        mesh_names,
        *,
        source_group=0,
        bad_canvas=False,
        bad_mesh=None,
        canvas=None,
        shape=None,
        mesh_scale=None,
    ):
        self.mesh_names = mesh_names
        self.source_group = source_group
        self.bad_canvas = bad_canvas
        self.bad_mesh = bad_mesh
        self.canvas = canvas
        self.shape = shape
        self.mesh_scale = mesh_scale

    def list(self, prefix):
        root = f"{SEGMENT}/mesh/"
        if prefix == root:
            return Listing((), tuple(f"{root}{name}/" for name in self.mesh_names))
        if prefix.startswith(root):
            return Listing((f"{prefix}meta.json", f"{prefix}x.tif"), ())
        raise AssertionError(prefix)

    def json(self, key):
        if key.endswith("/.zattrs"):
            canvas = self.canvas
            if canvas is None:
                canvas = [200, 100] if not self.bad_canvas else [201, 100]
            return {"canvas_size": canvas, "source_group": self.source_group}
        if key.endswith("/.zarray"):
            return {"shape": self.shape if self.shape is not None else [65, 100, 200]}
        if key.endswith("/meta.json"):
            if self.bad_mesh and self.bad_mesh in key:
                raise ValueError("broken mesh metadata")
            return {
                "scale": self.mesh_scale
                if self.mesh_scale is not None
                else [0.05, 0.05]
            }
        raise AssertionError(key)


def _patch_shapes(monkeypatch, shapes):
    def shape(_client, key):
        for name, value in shapes.items():
            if name in key:
                return value
        raise AssertionError(key)

    monkeypatch.setattr("resolve_volume_frame_mesh.tiff_shape", shape)


def test_resolves_exact_target_and_ignores_decoy(monkeypatch):
    target = "example-on-20260101000000-2.399um.tifxyz"
    decoy = "example-on-20260202000000-1.129um.tifxyz"
    _patch_shapes(monkeypatch, {target: (5, 10), decoy: (10, 20)})
    result = resolve_volume_frame_mesh(FakeClient([decoy, target]), SEGMENT, SURFACE)
    assert result["status"] == "resolved"
    assert result["selected_mesh"] == target
    assert result["selection_uses_labels"] is False
    assert result["named_candidate_count"] == 1
    assert exit_code(result["status"]) == EXIT_RESOLVED


def test_group_one_scale_is_applied(monkeypatch):
    target = "example-on-20260101000000-1.129um.tifxyz"
    _patch_shapes(monkeypatch, {target: (10, 20)})
    result = resolve_volume_frame_mesh(
        FakeClient([target], source_group=1), SEGMENT, SURFACE
    )
    assert result["status"] == "resolved"
    assert result["candidates"][0]["predicted_canvas"] == [100, 200]


def test_optional_mesh_directory_cache_discovers_segment_once(monkeypatch):
    target = "example-on-20260101000000-2.399um.tifxyz"
    _patch_shapes(monkeypatch, {target: (5, 10)})
    calls = []

    def discover(_client, segment):
        calls.append(segment)
        return [f"{SEGMENT}/mesh/{target}"]

    monkeypatch.setattr("resolve_volume_frame_mesh.discover_mesh_dirs", discover)
    cache = {}
    client = FakeClient([target])
    first = resolve_volume_frame_mesh(
        client, SEGMENT, SURFACE, mesh_directory_cache=cache
    )
    second = resolve_volume_frame_mesh(
        client, SEGMENT, SURFACE, mesh_directory_cache=cache
    )
    assert first["status"] == second["status"] == "resolved"
    assert calls == [SEGMENT]
    assert cache == {SEGMENT: [f"{SEGMENT}/mesh/{target}"]}


def test_ambiguous_exact_targets_abstain(monkeypatch):
    first = "example-on-20260101000000-2.399um.tifxyz"
    second = "example-v2-on-20260101000000-2.399um.tifxyz"
    _patch_shapes(monkeypatch, {first: (5, 10), second: (5, 10)})
    result = resolve_volume_frame_mesh(FakeClient([first, second]), SEGMENT, SURFACE)
    assert result["status"] == "ambiguous"
    assert result["selected_mesh"] is None
    assert exit_code(result["status"]) == EXIT_AMBIGUOUS


def test_missing_target_abstains(monkeypatch):
    decoy = "example-on-20260202000000-2.399um.tifxyz"
    _patch_shapes(monkeypatch, {decoy: (5, 10)})
    result = resolve_volume_frame_mesh(FakeClient([decoy]), SEGMENT, SURFACE)
    assert result["status"] == "missing"
    assert result["candidates"] == []
    assert exit_code(result["status"]) == EXIT_MISSING


def test_bad_candidate_metadata_invalidates_resolution(monkeypatch):
    target = "example-on-20260101000000-2.399um.tifxyz"
    broken = "example-v2-on-20260101000000-2.399um.tifxyz"
    _patch_shapes(monkeypatch, {target: (5, 10), broken: (5, 10)})
    result = resolve_volume_frame_mesh(
        FakeClient([target, broken], bad_mesh=broken), SEGMENT, SURFACE
    )
    assert result["status"] == "invalid_metadata"
    assert result["selected_mesh"] is None


def test_surface_canvas_must_match_level_zero(monkeypatch):
    target = "example-on-20260101000000-2.399um.tifxyz"
    _patch_shapes(monkeypatch, {target: (5, 10)})
    result = resolve_volume_frame_mesh(
        FakeClient([target], bad_canvas=True), SEGMENT, SURFACE
    )
    assert result["status"] == "invalid_metadata"
    assert exit_code(result["status"]) == EXIT_INVALID_METADATA


def test_malformed_volume_id_is_invalid(monkeypatch):
    _patch_shapes(monkeypatch, {})
    result = resolve_volume_frame_mesh(
        FakeClient([]), SEGMENT, "2.399um-volume-unknown.zarr"
    )
    assert result["status"] == "invalid_metadata"


@pytest.mark.parametrize(
    "client_kwargs",
    [
        {"canvas": ["200", 100]},
        {"canvas": [200.0, 100]},
        {"canvas": [True, 100]},
        {"canvas": [0, 100]},
        {"canvas": [-200, 100]},
        {"shape": [65, "100", 200]},
        {"shape": [65, 100.0, 200]},
        {"shape": [65, True, 200]},
        {"shape": [0, 100, 200]},
        {"source_group": True},
        {"source_group": "1"},
        {"source_group": 1.0},
        {"source_group": -1},
        {"source_group": 64},
    ],
)
def test_malformed_surface_numerics_return_invalid_metadata(monkeypatch, client_kwargs):
    target = "example-on-20260101000000-2.399um.tifxyz"
    _patch_shapes(monkeypatch, {target: (5, 10)})
    result = resolve_volume_frame_mesh(
        FakeClient([target], **client_kwargs), SEGMENT, SURFACE
    )
    assert result["status"] == "invalid_metadata"


@pytest.mark.parametrize(
    "scale",
    [
        True,
        "0.05",
        [True, 0.05],
        ["0.05", 0.05],
        [0, 0.05],
        [-1, 0.05],
        [math.nan, 0.05],
        [math.inf, 0.05],
    ],
)
def test_malformed_scale_numerics_return_invalid_metadata(monkeypatch, scale):
    target = "example-on-20260101000000-2.399um.tifxyz"
    _patch_shapes(monkeypatch, {target: (5, 10)})
    result = resolve_volume_frame_mesh(
        FakeClient([target], mesh_scale=scale), SEGMENT, SURFACE
    )
    assert result["status"] == "invalid_metadata"


@pytest.mark.parametrize("shape", [(0, 10), (-1, 10)])
def test_nonpositive_mesh_dimensions_return_invalid_metadata(monkeypatch, shape):
    target = "example-on-20260101000000-2.399um.tifxyz"
    _patch_shapes(monkeypatch, {target: shape})
    result = resolve_volume_frame_mesh(FakeClient([target]), SEGMENT, SURFACE)
    assert result["status"] == "invalid_metadata"


@pytest.mark.parametrize("scale", [0.05, [1, 2.5]])
def test_scale_accepts_valid_json_numeric_forms(scale):
    result = _scale_pair({"scale": scale})
    assert all(isinstance(value, float) and value > 0 for value in result)


def test_normalizes_bucket_urls_and_rejects_wrong_segment():
    base = "https://example.test"
    url = f"{base}/{SEGMENT}/surface-volumes/{SURFACE}"
    assert normalize_segment(url, base) == SEGMENT
    assert normalize_surface_volume(url, SEGMENT, base) == (
        f"{SEGMENT}/surface-volumes/{SURFACE}",
        SURFACE,
    )
    try:
        normalize_surface_volume(
            f"{base}/PHerc9999/segments/wrong/surface-volumes/{SURFACE}",
            SEGMENT,
            base,
        )
    except ValueError as exc:
        assert "does not belong" in str(exc)
    else:
        raise AssertionError("wrong segment should fail")


def test_accepts_regional_alias_for_same_s3_bucket():
    base = "https://vesuvius-challenge-open-data.s3.amazonaws.com"
    url = (
        "https://vesuvius-challenge-open-data.s3.us-east-1.amazonaws.com/"
        f"{SEGMENT}/surface-volumes/{SURFACE}"
    )
    assert normalize_segment(url, base) == SEGMENT


def test_corpus_evaluator_requires_unique_target(tmp_path):
    source = tmp_path / "audit.json"
    source.write_text(
        json.dumps(
            {
                "git_sha": "abc",
                "generated_at_utc": "2026-10-10T00:00:00Z",
                "segments": [
                    {
                        "segment": SEGMENT,
                        "surface_volumes": [
                            {
                                "surface_volume": SURFACE,
                                "status": "exact_target",
                                "exact_target_meshes": ["target.tifxyz"],
                            },
                            {
                                "surface_volume": "other.zarr",
                                "status": "exact_target",
                                "exact_target_meshes": ["a.tifxyz", "b.tifxyz"],
                            },
                        ],
                    }
                ],
            }
        )
    )
    result = evaluate([source])
    assert result["summary"]["counts"] == {"ambiguous": 1, "resolved": 1}
    assert result["summary"]["decision"] == "NO-GO"
    assert result["failures"][0]["outcome"] == "ambiguous"
