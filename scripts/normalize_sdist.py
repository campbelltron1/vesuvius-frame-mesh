#!/usr/bin/env python3
"""Normalize a gzip-compressed source distribution for reproducible bytes."""

from __future__ import annotations

import argparse
import gzip
import io
import os
from pathlib import Path
import tarfile
import tempfile
from typing import Sequence


SOURCE_DATE_EPOCH = 1791619200


def normalized_tar_bytes(path: Path, *, epoch: int = SOURCE_DATE_EPOCH) -> bytes:
    output = io.BytesIO()
    with tarfile.open(path, "r:gz") as source, tarfile.open(
        fileobj=output, mode="w", format=tarfile.PAX_FORMAT
    ) as destination:
        members = sorted(source.getmembers(), key=lambda member: member.name)
        for member in members:
            if member.name.startswith("/") or ".." in Path(member.name).parts:
                raise ValueError(f"unsafe archive member: {member.name!r}")
            member.uid = 0
            member.gid = 0
            member.uname = ""
            member.gname = ""
            member.mtime = epoch
            member.pax_headers = {}
            payload = source.extractfile(member) if member.isfile() else None
            destination.addfile(member, payload)
    return output.getvalue()


def normalize_sdist(path: Path, *, epoch: int = SOURCE_DATE_EPOCH) -> None:
    path = path.resolve()
    tar_payload = normalized_tar_bytes(path, epoch=epoch)
    descriptor, tmp_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    tmp = Path(tmp_name)
    try:
        with os.fdopen(descriptor, "wb") as raw:
            with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=epoch) as stream:
                stream.write(tar_payload)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archives", type=Path, nargs="+")
    parser.add_argument("--epoch", type=int, default=SOURCE_DATE_EPOCH)
    args = parser.parse_args(argv)
    for archive in args.archives:
        normalize_sdist(archive, epoch=args.epoch)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

