"""Bounded-memory output delivery with atomic, integrity-checked publication."""
from __future__ import annotations

import hashlib
import os
import re
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator, Mapping

from .errors import NodusError, ValidationError


def download_path(workload_id: str, name: str) -> str:
    if (not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+", name)
            or name in (".", "..")):
        raise ValidationError("Output name must be a single name containing letters, digits, dots, underscores or hyphens.")
    return f"/v1/workloads/{workload_id}/outputs/{name}"


@contextmanager
def verified_file(destination: str | os.PathLike[str], headers: Mapping[str, str]) -> Iterator[Callable[[bytes], None]]:
    digest = headers.get("X-Nodus-SHA256", "")
    if not re.fullmatch(r"[a-fA-F0-9]{64}", digest):
        raise NodusError("Output response has no valid SHA-256; download was not saved.")
    length = headers.get("Content-Length")
    if length is not None and not re.fullmatch(r"[0-9]+", length):
        raise NodusError("Output response has an invalid Content-Length.")
    expected = int(length) if length is not None else None
    destination = Path(destination)
    fd, temporary = tempfile.mkstemp(prefix=".nodus-download-", dir=destination.parent)
    hasher = hashlib.sha256()
    count = 0
    try:
        with os.fdopen(fd, "wb") as stream:
            def write(chunk: bytes) -> None:
                nonlocal count
                count += len(chunk)
                if expected is not None and count > expected:
                    raise NodusError("Output is larger than its declared Content-Length.")
                hasher.update(chunk)
                stream.write(chunk)
            yield write
            if expected is not None and count != expected:
                raise NodusError("Output length mismatch; download was not saved.")
            if hasher.hexdigest() != digest.lower():
                raise NodusError("Output SHA-256 mismatch; download was not saved.")
        os.replace(temporary, destination)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
