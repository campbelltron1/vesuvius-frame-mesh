import contextlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import threading
from urllib.parse import parse_qs, unquote, urlparse
from xml.sax.saxutils import escape


ROOT = Path(__file__).resolve().parents[1]
SEGMENT = "PHerc0000/segments/example-w001"
SURFACE = "2.4um-volume-20260101000000.zarr"
TARGET = "example-on-20260101000000-2.4um.tifxyz"


def _classic_tiff(height, width):
    header = b"II" + struct.pack("<H", 42) + struct.pack("<I", 8)
    entries = b"".join(
        [
            struct.pack("<HHII", 256, 4, 1, width),
            struct.pack("<HHII", 257, 4, 1, height),
        ]
    )
    return header + struct.pack("<H", 2) + entries + struct.pack("<I", 0)


@contextlib.contextmanager
def _s3_server(meshes, *, bad_surface=False):
    requests = []
    mesh_root = f"{SEGMENT}/mesh/"
    surface_root = f"{SEGMENT}/surface-volumes/{SURFACE}"

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, _format, *_args):
            pass

        def _send(
            self,
            body,
            *,
            status=200,
            content_type="application/octet-stream",
            extra_headers=None,
        ):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            for key, value in (extra_headers or {}).items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            parsed = urlparse(self.path)
            range_header = self.headers.get("Range")
            requests.append({"path": parsed.path, "query": parsed.query, "range": range_header})
            if parsed.path == "/":
                prefix = parse_qs(parsed.query).get("prefix", [""])[0]
                if prefix == mesh_root:
                    prefixes = "".join(
                        f"<CommonPrefixes><Prefix>{escape(mesh_root + name + '/')}</Prefix>"
                        "</CommonPrefixes>"
                        for name in sorted(meshes)
                    )
                    body = (
                        '<?xml version="1.0" encoding="UTF-8"?>'
                        '<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
                        f"<IsTruncated>false</IsTruncated>{prefixes}</ListBucketResult>"
                    ).encode()
                    self._send(body, content_type="application/xml")
                    return
                for name in meshes:
                    directory = f"{mesh_root}{name}/"
                    if prefix == directory:
                        body = (
                            '<?xml version="1.0" encoding="UTF-8"?>'
                            '<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
                            f"<Contents><Key>{escape(directory + 'meta.json')}</Key></Contents>"
                            f"<Contents><Key>{escape(directory + 'x.tif')}</Key></Contents>"
                            "<IsTruncated>false</IsTruncated></ListBucketResult>"
                        ).encode()
                        self._send(body, content_type="application/xml")
                        return
                self._send(b"not found", status=404)
                return

            key = unquote(parsed.path.lstrip("/"))
            if key == f"{surface_root}/.zattrs":
                canvas = [201, 100] if bad_surface else [200, 100]
                self._send(json.dumps({"canvas_size": canvas, "source_group": 0}).encode())
                return
            if key == f"{surface_root}/0/.zarray":
                self._send(json.dumps({"shape": [65, 100, 200]}).encode())
                return
            for name, spec in meshes.items():
                directory = f"{mesh_root}{name}"
                if key == f"{directory}/meta.json":
                    self._send(json.dumps({"scale": spec.get("scale", [0.05, 0.05])}).encode())
                    return
                if key == f"{directory}/x.tif":
                    body = _classic_tiff(*spec.get("shape", [5, 10]))
                    if range_header:
                        unit, bounds = range_header.split("=", 1)
                        assert unit == "bytes"
                        start, end = (int(value) for value in bounds.split("-", 1))
                        self._send(
                            body[start : end + 1],
                            status=206,
                            extra_headers={
                                "Content-Range": f"bytes {start}-{end}/{len(body)}"
                            },
                        )
                    else:
                        self._send(body)
                    return
            self._send(b"not found", status=404)

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", requests
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def _run_cli(tmp_path, base_url, *, segment=SEGMENT, surface=SURFACE, budget=1):
    env = os.environ.copy()
    installed_command = env.get("VESUVIUS_FRAME_MESH_COMMAND")
    if installed_command:
        # Prove the wheel is self-contained rather than importing the checkout.
        env.pop("PYTHONPATH", None)
    else:
        env["PYTHONPATH"] = str(ROOT / "src")
    command = ([installed_command] if installed_command else [
        sys.executable,
        "-m",
        "resolve_volume_frame_mesh",
    ]) + [
        segment,
        surface,
        "--base-url",
        base_url,
        "--cache-dir",
        str(tmp_path / "cache"),
        "--budget-mib",
        str(budget),
    ]
    completed = subprocess.run(
        command,
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    return completed, json.loads(completed.stdout)


def _run_batch_cli(tmp_path, base_url, cases, *, extra_args=None, output_name="batch-result.json"):
    plan = tmp_path / "batch-plan.json"
    output = tmp_path / output_name
    plan.write_text(json.dumps({"schema_version": 1, "cases": cases}))
    env = os.environ.copy()
    installed_command = env.get("VESUVIUS_FRAME_MESH_BATCH_COMMAND")
    if installed_command:
        env.pop("PYTHONPATH", None)
    else:
        env["PYTHONPATH"] = str(ROOT / "src")
    command = ([installed_command] if installed_command else [
        sys.executable,
        "-m",
        "resolve_volume_frame_batch",
    ]) + [
        "--plan",
        str(plan),
        "--base-url",
        base_url,
        "--cache-dir",
        str(tmp_path / "batch-cache"),
        "--budget-mib",
        "1",
        "--output",
        str(output),
    ]
    command.extend(extra_args or [])
    completed = subprocess.run(
        command,
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    return completed, json.loads(output.read_text())


def test_resolved_http_path_and_cache_use_only_metadata_ranges(tmp_path):
    with _s3_server({TARGET: {"shape": [5, 10]}}) as (base_url, requests):
        first, result = _run_cli(tmp_path, base_url)
        assert first.returncode == 0, first.stderr
        assert result["status"] == "resolved"
        assert result["selected_mesh"] == TARGET
        assert result["network"]["request_count"] == len(requests)
        request_count = len(requests)

        segment_url = f"{base_url}/{SEGMENT}/surface-volumes/{SURFACE}"
        surface_url = segment_url
        second, cached = _run_cli(
            tmp_path,
            base_url,
            segment=segment_url,
            surface=surface_url,
        )
        assert second.returncode == 0, second.stderr
        assert cached["status"] == "resolved"
        assert cached["network"]["downloaded_bytes"] == 0
        assert cached["network"]["request_count"] == 0
        assert cached["network"]["cache_hit_count"] > 0
        assert len(requests) == request_count

    allowed_json = {"/.zattrs", "/.zarray", "/meta.json"}
    for request in requests:
        if request["path"].endswith("/x.tif"):
            assert request["range"] is not None
            _, bounds = request["range"].split("=", 1)
            _start, end = (int(value) for value in bounds.split("-", 1))
            assert end < len(_classic_tiff(5, 10))
        elif request["path"] != "/":
            assert any(request["path"].endswith(suffix) for suffix in allowed_json)
    assert not any("/0/0/" in request["path"] for request in requests)


def test_missing_http_path_exits_two(tmp_path):
    with _s3_server({TARGET: {"shape": [4, 10]}}) as (base_url, _requests):
        completed, result = _run_cli(tmp_path, base_url)
    assert completed.returncode == 2
    assert result["status"] == "missing"


def test_ambiguous_http_path_exits_three(tmp_path):
    other = "example-v2-on-20260101000000-2.4um.tifxyz"
    with _s3_server(
        {TARGET: {"shape": [5, 10]}, other: {"shape": [5, 10]}}
    ) as (base_url, _requests):
        completed, result = _run_cli(tmp_path, base_url)
    assert completed.returncode == 3
    assert result["status"] == "ambiguous"


def test_invalid_http_metadata_exits_four(tmp_path):
    with _s3_server({TARGET: {"shape": [5, 10]}}, bad_surface=True) as (
        base_url,
        _requests,
    ):
        completed, result = _run_cli(tmp_path, base_url)
    assert completed.returncode == 4
    assert result["status"] == "invalid_metadata"


def test_wrong_bucket_url_is_rejected_before_request(tmp_path):
    with _s3_server({TARGET: {"shape": [5, 10]}}) as (base_url, requests):
        wrong = f"https://wrong.example/{SEGMENT}"
        completed, result = _run_cli(tmp_path, base_url, segment=wrong)
    assert completed.returncode == 4
    assert result["status"] == "invalid_metadata"
    assert requests == []


def test_zero_budget_fails_closed_on_first_response(tmp_path):
    with _s3_server({TARGET: {"shape": [5, 10]}}) as (base_url, requests):
        completed, result = _run_cli(tmp_path, base_url, budget=0)
    assert completed.returncode == 4
    assert result["status"] == "invalid_metadata"
    assert "DownloadBudgetExceeded" in result["error"]
    assert len(requests) == 1


def test_batch_http_preserves_mixed_status_and_reuses_shared_cache(tmp_path):
    cases = [
        {"id": "first", "segment": SEGMENT, "surface_volume": SURFACE},
        {"id": "replay", "segment": SEGMENT, "surface_volume": SURFACE},
        {"id": "invalid", "segment": SEGMENT, "surface_volume": "invalid.zarr"},
    ]
    with _s3_server({TARGET: {"shape": [5, 10]}}) as (base_url, requests):
        completed, result = _run_batch_cli(tmp_path, base_url, cases)
    assert completed.returncode == 2, completed.stderr
    assert result["summary"]["decision"] == "NO-GO"
    assert result["summary"]["status_counts"] == {
        "invalid_metadata": 1,
        "resolved": 2,
    }
    assert [row["result"]["status"] for row in result["cases"]] == [
        "resolved",
        "resolved",
        "invalid_metadata",
    ]
    assert result["cases"][0]["network"]["request_count"] == len(requests)
    assert result["cases"][1]["network"]["request_count"] == 0
    assert result["cases"][1]["network"]["cache_hit_count"] > 0
    assert result["cases"][2]["network"]["request_count"] == 0
    assert result["summary"]["request_count"] == len(requests)


def test_batch_cli_checkpoint_full_resume_performs_no_requests(tmp_path):
    cases = [
        {"id": "first", "segment": SEGMENT, "surface_volume": SURFACE},
        {"id": "second", "segment": SEGMENT, "surface_volume": SURFACE},
    ]
    checkpoint = tmp_path / "checkpoint.json"
    with _s3_server({TARGET: {"shape": [5, 10]}}) as (base_url, requests):
        first, first_result = _run_batch_cli(
            tmp_path,
            base_url,
            cases,
            extra_args=["--workers", "2", "--checkpoint", str(checkpoint)],
            output_name="first.json",
        )
        request_count = len(requests)
        resumed, resumed_result = _run_batch_cli(
            tmp_path,
            base_url,
            cases,
            extra_args=[
                "--workers",
                "2",
                "--checkpoint",
                str(checkpoint),
                "--resume",
            ],
            output_name="resumed.json",
        )
    assert first.returncode == 0, first.stderr
    assert first_result["summary"]["current_invocation_cases"] == 2
    assert resumed.returncode == 0, resumed.stderr
    assert resumed_result["summary"]["resumed_cases"] == 2
    assert resumed_result["summary"]["current_invocation_cases"] == 0
    assert resumed_result["summary"]["workers_used"] == 0
    assert resumed_result["summary"]["request_count"] == 0
    assert len(requests) == request_count


def test_batch_cli_rejects_malformed_checkpoint_before_network(tmp_path):
    cases = [{"id": "first", "segment": SEGMENT, "surface_volume": SURFACE}]
    checkpoint = tmp_path / "bad-checkpoint.json"
    checkpoint.write_text("not json")
    completed, result = _run_batch_cli(
        tmp_path,
        "http://127.0.0.1:1",
        cases,
        extra_args=["--checkpoint", str(checkpoint), "--resume"],
    )
    assert completed.returncode == 4
    assert result["summary"]["decision"] == "INVALID_CHECKPOINT"
    assert "invalid checkpoint JSON" in result["error"]
