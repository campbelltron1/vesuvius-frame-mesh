from concurrent.futures import ThreadPoolExecutor
import os
import struct
from threading import Barrier
import urllib.error
import urllib.parse
import urllib.request

import pytest

from canvas_provenance import (
    Listing,
    PublicS3,
    audit_segment,
    cxx_lround_positive,
    discover_mesh_dirs,
    parse_tiff_shape,
    rendered_shape,
    volume_id,
)


def _classic_tiff(width: int, height: int, ifd_offset: int = 64) -> bytes:
    data = bytearray(ifd_offset + 2 + 2 * 12 + 4)
    data[:8] = b"II" + struct.pack("<HI", 42, ifd_offset)
    struct.pack_into("<H", data, ifd_offset, 2)
    struct.pack_into("<HHI", data, ifd_offset + 2, 256, 4, 1)
    struct.pack_into("<I", data, ifd_offset + 10, width)
    struct.pack_into("<HHI", data, ifd_offset + 14, 257, 4, 1)
    struct.pack_into("<I", data, ifd_offset + 22, height)
    return bytes(data)


def _bigtiff(width: int, height: int, ifd_offset: int = 64) -> bytes:
    data = bytearray(ifd_offset + 8 + 2 * 20 + 8)
    data[:16] = b"II" + struct.pack("<HHHQ", 43, 8, 0, ifd_offset)
    struct.pack_into("<Q", data, ifd_offset, 2)
    struct.pack_into("<HHQ", data, ifd_offset + 8, 256, 16, 1)
    struct.pack_into("<Q", data, ifd_offset + 20, width)
    struct.pack_into("<HHQ", data, ifd_offset + 28, 257, 16, 1)
    struct.pack_into("<Q", data, ifd_offset + 40, height)
    return bytes(data)


def test_parse_tiff_shape_when_ifd_is_at_end():
    payload = _classic_tiff(371, 361)

    def read_range(start: int, end: int) -> bytes:
        return payload[start : end + 1]

    assert parse_tiff_shape(read_range) == (361, 371)


def test_parse_bigtiff_shape_when_ifd_is_at_end():
    payload = _bigtiff(3072, 2986)
    assert parse_tiff_shape(lambda start, end: payload[start : end + 1]) == (2986, 3072)


def test_renderer_shape_matches_cpp_positive_lround():
    assert rendered_shape((361, 371), (0.05, 0.05)) == (7220, 7420)
    assert rendered_shape((2986, 3072), (0.05, 0.05), 0.5) == (29860, 30720)
    assert cxx_lround_positive(2.5) == 3


def test_target_id_survives_suffixes():
    assert (
        volume_id("1.129um-0.22m-59keV-volume-20260413113053-L1.zarr")
        == "20260413113053"
    )


def test_dotted_tifxyz_directory_is_discovered():
    segment = "PHerc0000/segments/example"
    root = f"{segment}/mesh/"
    dotted = f"{root}example-on-20260101000000-2.4um.tifxyz/"

    class Client:
        def list(self, prefix):
            if prefix == root:
                return Listing((), (dotted,))
            if prefix == dotted:
                return Listing((f"{dotted}meta.json", f"{dotted}x.tif"), ())
            raise AssertionError(prefix)

    assert discover_mesh_dirs(Client(), segment) == [dotted.rstrip("/")]


def test_audit_applies_source_group_to_target_mesh(monkeypatch):
    segment = "PHerc0000/segments/example"
    mesh = f"{segment}/mesh/example-on-20260101000000-1.129um.tifxyz"
    surface = f"{segment}/surface-volumes/1.129um-volume-20260101000000-L1.zarr"

    class Client:
        def json(self, key):
            if key.endswith("meta.json"):
                return {"scale": [0.05, 0.05]}
            if key.endswith(".zarray"):
                return {"shape": [116, 29860, 30720]}
            return {"canvas_size": [30720, 29860], "source_group": 1}

    monkeypatch.setattr("canvas_provenance.discover_mesh_dirs", lambda *_: [mesh])
    monkeypatch.setattr("canvas_provenance.discover_surface_dirs", lambda *_: [surface])
    monkeypatch.setattr("canvas_provenance.tiff_shape", lambda *_: (2986, 3072))
    result = audit_segment(Client(), segment)
    row = result["surface_volumes"][0]
    assert row["ds_scale"] == 0.5
    assert row["status"] == "exact_target"


def test_cache_hits_are_accounted_without_network(tmp_path):
    client = PublicS3(cache_dir=tmp_path)
    client._cached_write("example", b"metadata")
    assert client._cached_read("example") == b"metadata"
    assert client.cache_hit_count == 1
    assert client.cache_hit_bytes == 8


def test_concurrent_same_key_cache_writes_are_atomic(monkeypatch, tmp_path):
    workers = 64
    barrier = Barrier(workers)
    original_replace = os.replace

    def synchronized_replace(source, destination):
        # Every unique temporary is fully written before replacement starts.
        barrier.wait(timeout=10)
        return original_replace(source, destination)

    monkeypatch.setattr("canvas_provenance.os.replace", synchronized_replace)
    client = PublicS3(cache_dir=tmp_path)
    payloads = [index.to_bytes(2, "big") * 32 for index in range(workers)]

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [
            pool.submit(client._cached_write, "same-key", payload)
            for payload in payloads
        ]
        for future in futures:
            future.result(timeout=15)

    files = list(tmp_path.iterdir())
    completed = [path for path in files if not path.name.endswith(".tmp")]
    assert len(completed) == 1
    assert completed[0].read_bytes() in payloads
    assert not [path for path in files if path.name.endswith(".tmp")]


def test_cache_temp_is_cleaned_when_atomic_replace_fails(monkeypatch, tmp_path):
    def fail_replace(_source, _destination):
        raise OSError("injected replacement failure")

    monkeypatch.setattr("canvas_provenance.os.replace", fail_replace)
    client = PublicS3(cache_dir=tmp_path)
    with pytest.raises(OSError, match="injected replacement failure"):
        client._cached_write("example", b"metadata")
    assert list(tmp_path.iterdir()) == []


def test_concurrent_distinct_cache_keys_preserve_hit_counters(tmp_path):
    client = PublicS3(cache_dir=tmp_path)
    entries = {f"key-{index}": bytes([index]) * (index + 1) for index in range(32)}
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda item: client._cached_write(*item), entries.items()))
        values = list(pool.map(client._cached_read, entries))
    assert values == list(entries.values())
    assert client.cache_hit_count == len(entries)
    assert client.cache_hit_bytes == sum(map(len, entries.values()))
    assert client.request_count == 0
    assert client.downloaded_bytes == 0


def test_transient_request_is_retried(monkeypatch):
    calls = 0

    class Response:
        headers = {"Content-Length": "4"}

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self, _limit):
            return b"test"

    def urlopen(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise urllib.error.URLError("connection reset")
        return Response()

    monkeypatch.setattr("canvas_provenance.urllib.request.urlopen", urlopen)
    monkeypatch.setattr("canvas_provenance.time.sleep", lambda _seconds: None)
    client = PublicS3(cache_dir=None)
    assert client._request("https://example.invalid/metadata") == b"test"
    assert calls == 2
    assert client.request_count == 1


class _RangeResponse:
    def __init__(
        self,
        body,
        *,
        status=206,
        content_range="bytes 0-15/4096",
        content_length=None,
    ):
        self.body = body
        self.status = status
        self.read_called = False
        self.headers = {
            "Content-Length": str(len(body) if content_length is None else content_length)
        }
        if content_range is not None:
            self.headers["Content-Range"] = content_range

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, limit):
        self.read_called = True
        return self.body[:limit]


def _range_request():
    return urllib.request.Request(
        "https://example.invalid/x.tif", headers={"Range": "bytes=0-15"}
    )


def test_range_response_requires_http_206_before_read(monkeypatch):
    response = _RangeResponse(b"x" * 4096, status=200, content_range=None)
    monkeypatch.setattr("canvas_provenance.urllib.request.urlopen", lambda *_a, **_k: response)
    with pytest.raises(ValueError, match="ignored Range"):
        PublicS3(max_response_bytes=8192)._read_response(_range_request())
    assert response.read_called is False


@pytest.mark.parametrize(
    "content_range, body, content_length, message",
    [
        (None, b"x" * 16, 16, "Content-Range"),
        ("bytes 1-16/4096", b"x" * 16, 16, "does not match"),
        ("bytes 0-15/4096", b"x" * 15, 15, "Content-Length"),
        ("bytes 0-15/4096", b"x" * 17, 17, "Content-Length"),
        ("bytes 0-15/4096", b"x" * 15, 16, "body length"),
        ("bytes 0-15/4096", b"x" * 17, 16, "body length"),
    ],
)
def test_range_response_rejects_malformed_or_wrong_length(
    monkeypatch, content_range, body, content_length, message
):
    response = _RangeResponse(
        body, content_range=content_range, content_length=content_length
    )
    monkeypatch.setattr("canvas_provenance.urllib.request.urlopen", lambda *_a, **_k: response)
    with pytest.raises(ValueError, match=message):
        PublicS3(max_response_bytes=8192)._read_response(_range_request())


def test_range_response_accepts_exact_partial_and_caches_it(monkeypatch, tmp_path):
    body = b"0123456789abcdef"
    response = _RangeResponse(body)
    monkeypatch.setattr("canvas_provenance.urllib.request.urlopen", lambda *_a, **_k: response)
    client = PublicS3(cache_dir=tmp_path, max_response_bytes=8192)
    assert client._request("https://example.invalid/x.tif", (0, 15)) == body
    assert client.request_count == 1
    assert client.downloaded_bytes == 16
    assert len(list(tmp_path.iterdir())) == 1


def test_rejected_range_response_is_not_cached(monkeypatch, tmp_path):
    response = _RangeResponse(b"x" * 4096, status=200, content_range=None)
    monkeypatch.setattr("canvas_provenance.urllib.request.urlopen", lambda *_a, **_k: response)
    client = PublicS3(cache_dir=tmp_path, max_response_bytes=8192, request_attempts=1)
    with pytest.raises(ValueError, match="ignored Range"):
        client._request("https://example.invalid/x.tif", (0, 15))
    assert list(tmp_path.iterdir()) == []


def test_refresh_listings_bypasses_cached_xml_but_replaces_it(monkeypatch, tmp_path):
    old_xml = b"<ListBucketResult><CommonPrefixes><Prefix>old/</Prefix></CommonPrefixes></ListBucketResult>"
    new_xml = b"<ListBucketResult><CommonPrefixes><Prefix>new/</Prefix></CommonPrefixes></ListBucketResult>"
    client = PublicS3(cache_dir=tmp_path, refresh_listings=True)
    query = urllib.parse.urlencode(
        {
            "list-type": "2",
            "prefix": "root/",
            "delimiter": "/",
            "max-keys": "1000",
        }
    )
    url = f"{client.base_url}/?{query}"
    client._cached_write(url, old_xml)
    calls = []

    def read_response(_request):
        calls.append(True)
        return new_xml

    monkeypatch.setattr(client, "_read_response", read_response)
    assert client.list("root/").prefixes == ("new/",)
    assert calls == [True]
    assert client.request_count == 1

    replay = PublicS3(cache_dir=tmp_path)
    assert replay.list("root/").prefixes == ("new/",)
    assert replay.request_count == 0


@pytest.mark.parametrize("bad_length", [0, 15, 17, 4096])
def test_wrong_length_range_cache_is_evicted_and_refetched(
    monkeypatch, tmp_path, bad_length
):
    body = b"0123456789abcdef"
    response = _RangeResponse(body)
    monkeypatch.setattr("canvas_provenance.urllib.request.urlopen", lambda *_a, **_k: response)
    client = PublicS3(cache_dir=tmp_path, max_response_bytes=8192)
    url = "https://example.invalid/x.tif"
    client._cached_write(f"{url}#0-15", b"x" * bad_length)

    assert client._request(url, (0, 15)) == body
    assert client.cache_invalid_count == 1
    assert client.cache_invalid_bytes == bad_length
    assert client.cache_hit_count == 0
    assert client.request_count == 1
    files = list(tmp_path.iterdir())
    assert len(files) == 1
    assert files[0].read_bytes() == body


def test_exact_range_cache_hit_stays_network_free(monkeypatch, tmp_path):
    body = b"0123456789abcdef"
    client = PublicS3(cache_dir=tmp_path, max_response_bytes=8192)
    url = "https://example.invalid/x.tif"
    client._cached_write(f"{url}#0-15", body)
    monkeypatch.setattr(
        "canvas_provenance.urllib.request.urlopen",
        lambda *_a, **_k: pytest.fail("valid cache hit must not use network"),
    )

    assert client._request(url, (0, 15)) == body
    assert client.cache_hit_count == 1
    assert client.cache_hit_bytes == 16
    assert client.cache_invalid_count == 0
    assert client.request_count == 0


def test_wrong_range_cache_and_invalid_replacement_leave_no_cache(
    monkeypatch, tmp_path
):
    response = _RangeResponse(b"x" * 4096, status=200, content_range=None)
    monkeypatch.setattr("canvas_provenance.urllib.request.urlopen", lambda *_a, **_k: response)
    client = PublicS3(
        cache_dir=tmp_path, max_response_bytes=8192, request_attempts=1
    )
    url = "https://example.invalid/x.tif"
    client._cached_write(f"{url}#0-15", b"x" * 4096)

    with pytest.raises(ValueError, match="ignored Range"):
        client._request(url, (0, 15))
    assert client.cache_invalid_count == 1
    assert client.cache_hit_count == 0
    assert list(tmp_path.iterdir()) == []
