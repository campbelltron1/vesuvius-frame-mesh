import copy
import json
from pathlib import Path
import sys
import threading
import time

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import resolve_volume_frame_batch as batch


def _case(case_id="one"):
    return {
        "id": case_id,
        "segment": f"PHerc0000/segments/{case_id}",
        "surface_volume": "volume-20260101000000.zarr",
    }


def _plan(tmp_path, cases=None):
    path = tmp_path / "plan.json"
    path.write_text(
        json.dumps({"schema_version": 1, "cases": cases or [_case()]})
    )
    return path


class FakeClient:
    budget_bytes = 1024

    def __init__(self):
        for field in batch.NETWORK_FIELDS:
            setattr(self, field, 0)


def test_load_plan_is_strict_and_hashes_source(tmp_path):
    path = _plan(tmp_path)
    cases, source = batch.load_plan(path)
    assert cases == [_case()]
    assert source["name"] == "plan.json"
    assert len(source["sha256"]) == 64


@pytest.mark.parametrize(
    "document, message",
    [
        ({"schema_version": 2, "cases": [_case()]}, "schema_version"),
        ({"schema_version": 1, "cases": []}, "non-empty"),
        (
            {"schema_version": 1, "cases": [_case(), _case()]},
            "duplicate case id",
        ),
        (
            {
                "schema_version": 1,
                "cases": [{**_case(), "unexpected": True}],
            },
            "exactly",
        ),
    ],
)
def test_load_plan_rejects_invalid_schema(tmp_path, document, message):
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(document))
    with pytest.raises(ValueError, match=message):
        batch.load_plan(path)


def test_batch_preserves_mixed_results_and_network_deltas(monkeypatch):
    client = FakeClient()
    statuses = iter(("resolved", "missing", "ambiguous", "invalid_metadata"))

    seen_caches = []

    def resolve(fake, segment, surface, *, mesh_directory_cache=None):
        fake.request_count += 1
        fake.downloaded_bytes += 10
        seen_caches.append(mesh_directory_cache)
        status = next(statuses)
        return {
            "status": status,
            "selected_mesh": "target.tifxyz" if status == "resolved" else None,
            "segment": segment,
            "surface_volume": surface,
        }

    monkeypatch.setattr(batch, "resolve_volume_frame_mesh", resolve)
    cases = [_case(str(index)) for index in range(4)]
    result = batch.run_batch(cases, {"sha256": "abc"}, client)
    assert result["summary"]["decision"] == "NO-GO"
    assert result["summary"]["status_counts"] == {
        "ambiguous": 1,
        "invalid_metadata": 1,
        "missing": 1,
        "resolved": 1,
    }
    assert [row["id"] for row in result["cases"]] == ["0", "1", "2", "3"]
    assert all(row["network"]["request_count"] == 1 for row in result["cases"])
    assert result["summary"]["request_count"] == 4
    assert all(value is seen_caches[0] for value in seen_caches)


def test_batch_output_is_deterministic(monkeypatch):
    def resolve(_client, _segment, _surface, *, mesh_directory_cache=None):
        assert mesh_directory_cache is not None
        return {"status": "resolved", "selected_mesh": "target.tifxyz"}

    monkeypatch.setattr(batch, "resolve_volume_frame_mesh", resolve)
    cases = [_case("a"), _case("b")]
    first = batch.run_batch(copy.deepcopy(cases), {"sha256": "abc"}, FakeClient())
    second = batch.run_batch(copy.deepcopy(cases), {"sha256": "abc"}, FakeClient())
    assert batch.canonical_json_bytes(first) == batch.canonical_json_bytes(second)


def test_two_workers_preserve_order_semantics_and_thread_local_network(monkeypatch):
    client = batch.PublicS3(cache_dir=None)
    cases = [
        {
            **_case("a0"),
            "segment": "PHerc0000/segments/a",
        },
        {
            **_case("b0"),
            "segment": "PHerc0000/segments/b",
        },
        {
            **_case("a1"),
            "segment": "https://vesuvius-challenge-open-data.s3.amazonaws.com/PHerc0000/segments/a",
        },
        {
            **_case("b1"),
            "segment": "PHerc0000/segments/b/mesh",
        },
    ]
    barrier = threading.Barrier(2)
    lock = threading.Lock()
    first_call_by_thread: set[int] = set()
    segment_threads: dict[str, set[int]] = {}
    segment_caches: dict[str, set[int]] = {}

    def resolve(fake, segment, _surface, *, mesh_directory_cache=None):
        thread_id = threading.get_ident()
        normalized = batch.normalize_segment(segment, fake.base_url)
        with lock:
            is_first = thread_id not in first_call_by_thread
            first_call_by_thread.add(thread_id)
            segment_threads.setdefault(normalized, set()).add(thread_id)
            segment_caches.setdefault(normalized, set()).add(id(mesh_directory_cache))
        if is_first:
            barrier.wait(timeout=2)
        time.sleep(0.01)
        fake._record(downloaded_bytes=10, request_count=1)
        return {
            "status": "resolved",
            "selected_mesh": normalized.rsplit("/", 1)[-1] + ".tifxyz",
        }

    monkeypatch.setattr(batch, "resolve_volume_frame_mesh", resolve)
    result = batch.run_batch(cases, {"sha256": "abc"}, client, workers=2)

    assert [row["id"] for row in result["cases"]] == ["a0", "b0", "a1", "b1"]
    assert result["summary"]["workers_requested"] == 2
    assert result["summary"]["workers_used"] == 2
    assert result["summary"]["downloaded_bytes"] == 40
    assert result["summary"]["request_count"] == 4
    assert all(row["network"]["downloaded_bytes"] == 10 for row in result["cases"])
    assert all(row["network"]["request_count"] == 1 for row in result["cases"])
    assert len(first_call_by_thread) == 2
    assert all(len(value) == 1 for value in segment_threads.values())
    assert all(len(value) == 1 for value in segment_caches.values())


@pytest.mark.parametrize("workers", [0, 3])
def test_run_batch_rejects_worker_counts_outside_resource_contract(workers):
    with pytest.raises(ValueError, match="workers must be 1 or 2"):
        batch.run_batch([_case()], {"sha256": "abc"}, FakeClient(), workers=workers)


def test_atomic_checkpoint_resumes_without_reexecuting_completed_cases(
    monkeypatch, tmp_path
):
    cases = [_case(str(index)) for index in range(4)]
    source = {"name": "plan.json", "sha256": "a" * 64}
    checkpoint = tmp_path / "progress.json"
    writer = batch.CheckpointWriter(checkpoint, source)
    attempted: list[str] = []

    def interrupted(fake, segment, _surface, *, mesh_directory_cache=None):
        assert mesh_directory_cache is not None
        case_id = segment.rsplit("/", 1)[-1]
        attempted.append(case_id)
        if case_id == "2":
            raise RuntimeError("injected interruption")
        fake.request_count += 1
        return {"status": "resolved", "selected_mesh": f"{case_id}.tifxyz"}

    monkeypatch.setattr(batch, "resolve_volume_frame_mesh", interrupted)
    with pytest.raises(RuntimeError, match="injected interruption"):
        batch.run_batch(
            cases,
            source,
            FakeClient(),
            workers=1,
            progress_callback=writer.record,
        )
    assert attempted == ["0", "1", "2"]
    assert not list(tmp_path.glob(".*.tmp"))

    prior = batch.load_checkpoint(checkpoint, cases, source)
    assert list(prior) == [0, 1]
    resumed_attempts: list[str] = []

    def resumed(fake, segment, _surface, *, mesh_directory_cache=None):
        assert mesh_directory_cache is not None
        case_id = segment.rsplit("/", 1)[-1]
        resumed_attempts.append(case_id)
        fake.request_count += 1
        return {"status": "resolved", "selected_mesh": f"{case_id}.tifxyz"}

    monkeypatch.setattr(batch, "resolve_volume_frame_mesh", resumed)
    resumed_writer = batch.CheckpointWriter(checkpoint, source, prior)
    result = batch.run_batch(
        cases,
        source,
        FakeClient(),
        workers=1,
        initial_rows=prior,
        progress_callback=resumed_writer.record,
    )

    assert resumed_attempts == ["2", "3"]
    assert [row["id"] for row in result["cases"]] == ["0", "1", "2", "3"]
    assert result["summary"]["resumed_cases"] == 2
    assert result["summary"]["current_invocation_cases"] == 2
    assert result["summary"]["prior_case_network"]["request_count"] == 2
    assert result["summary"]["request_count"] == 2
    final_checkpoint = batch.load_checkpoint(checkpoint, cases, source)
    assert list(final_checkpoint) == [0, 1, 2, 3]


def test_checkpoint_rejects_wrong_plan_duplicate_and_input_mismatch(tmp_path):
    cases = [_case("one"), _case("two")]
    source = {"name": "plan.json", "sha256": "b" * 64}
    path = tmp_path / "checkpoint.json"
    valid_row = {
        "id": "one",
        "segment_input": cases[0]["segment"],
        "surface_volume_input": cases[0]["surface_volume"],
        "network": {field: 0 for field in batch.NETWORK_FIELDS},
        "result": {"status": "resolved", "selected_mesh": "one.tifxyz"},
    }

    def write(*, plan_sha=None, rows=None):
        path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "method": "atomic batch progress checkpoint",
                    "plan_sha256": plan_sha or source["sha256"],
                    "cases": rows if rows is not None else [valid_row],
                }
            )
        )

    write(plan_sha="c" * 64)
    with pytest.raises(ValueError, match="does not match"):
        batch.load_checkpoint(path, cases, source)

    write(rows=[valid_row, valid_row])
    with pytest.raises(ValueError, match="duplicate"):
        batch.load_checkpoint(path, cases, source)

    write(rows=[{**valid_row, "segment_input": "PHerc9999/segments/wrong"}])
    with pytest.raises(ValueError, match="segment mismatch"):
        batch.load_checkpoint(path, cases, source)

    write(
        rows=[
            {
                **valid_row,
                "result": {
                    **valid_row["result"],
                    "selection_uses_labels": True,
                },
            }
        ]
    )
    with pytest.raises(ValueError, match="not label-free"):
        batch.load_checkpoint(path, cases, source)
