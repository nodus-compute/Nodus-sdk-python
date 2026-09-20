"""Bounded, deterministic project archives for the workspace transfer protocol."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import posixpath
import stat
import struct
import tempfile
from typing import Any, Iterator

from .errors import ValidationError

_PAYLOAD_LIMIT = 10_000_000_000
_METADATA_LIMIT = 512 << 20
_CHUNK_LIMIT = 64 << 20
_ENTRY_LIMIT = 100_000
_BUFFER = 1 << 20


@dataclass(frozen=True)
class WorkspaceArchive:
    manifest: dict[str, Any]
    path: Path


def _path_bytes(name: str) -> bytes:
    try:
        encoded = name.encode("utf-8", "strict")
    except UnicodeError as exc:
        raise ValidationError("Project paths must be valid UTF-8") from exc
    if (not encoded or len(encoded) > 1024 or name.startswith("/")
            or "\\" in name or "\x00" in name
            or (len(name) >= 2 and name[0].isascii() and name[0].isalpha() and name[1] == ":")
            or any(part in ("", ".", "..") for part in name.split("/"))):
        raise ValidationError("Project contains an unsupported or unsafe file path")
    return encoded


def _octal(value: int, width: int) -> bytes:
    # Oversized sizes are represented in the preceding PAX size record.
    if value >= 1 << (3 * (width - 1)):
        value = 0
    return f"{value:0{width - 1}o}".encode("ascii") + b"\0"


def _raw_header(name: str, kind: bytes, size: int, mode: int, *, extended: bool = False) -> bytes:
    block = bytearray(512)
    ascii_name = name.encode("ascii", "ignore")[:100]
    if extended:
        ascii_name = ascii_name.rstrip(b"/")
    block[:len(ascii_name)] = ascii_name
    for start, width, value in ((100, 8, mode), (108, 8, 0), (116, 8, 0), (124, 12, size), (136, 12, 0)):
        block[start:start + width] = _octal(value, width)
    block[148:156] = b" " * 8
    block[156:157] = kind
    block[257:265] = b"ustar\x0000"
    if not extended:
        block[329:337] = _octal(0, 8)
        block[337:345] = _octal(0, 8)
    block[148:156] = _octal(sum(block), 7) + b" "
    return bytes(block)


def _pax_record(key: str, value: str) -> bytes:
    suffix = f" {key}={value}\n".encode("utf-8")
    length = len(suffix) + 1
    while len(str(length)) + len(suffix) != length:
        length = len(str(length)) + len(suffix)
    return str(length).encode("ascii") + suffix


def _header(name: str, size: int, mode: int, directory: bool) -> bytes:
    encoded = _path_bytes(name)
    pax: dict[str, str] = {}
    if len(encoded) > 100 or not name.isascii():
        pax["path"] = name
    if size >= 1 << 33:
        pax["size"] = str(size)
    prefix = b""
    if pax:
        records = b"".join(_pax_record(key, pax[key]) for key in sorted(pax))
        parent, base = posixpath.split(name)
        pax_name = posixpath.join(parent, "PaxHeaders.0", base)
        prefix = _raw_header(pax_name, b"x", len(records), 0, extended=True)
        prefix += records + bytes((-len(records)) % 512)
    return prefix + _raw_header(name, b"5" if directory else b"0", size, mode)


def _identity(info: os.stat_result) -> tuple[int, int, int, int, int]:
    return info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns


@contextmanager
def build_workspace_archive(directory: str | os.PathLike[str]) -> Iterator[WorkspaceArchive]:
    """Snapshot regular project files to a private temporary archive.

    Keep the project unchanged while preparing this snapshot. Symbolic links and
    special files are rejected. Uploading never follows a discovered file link.
    Temporary disk space is proportional to the archive, memory is bounded.
    """
    root = Path(directory).resolve(strict=True)
    if not root.is_dir():
        raise ValidationError("Choose a project directory to upload")
    entries: list[tuple[str, Path, os.stat_result]] = []
    directories: list[tuple[Path, os.stat_result]] = [(root, root.stat())]
    pending = directories.copy()
    payload = 0
    while pending:
        parent, initial = pending.pop()
        try:
            if _identity(parent.lstat()) != _identity(initial):
                raise ValidationError("Project directory changed while preparing upload. Retry after saving your files")
            # os.walk materializes an entire directory before yielding. Iterate
            # incrementally so an oversized directory cannot bypass the bound.
            with os.scandir(parent) as children:
                for child in children:
                    if len(entries) >= _ENTRY_LIMIT:
                        raise ValidationError("Project exceeds the workspace file count or 10 GB capacity")
                    path = parent / child.name
                    relative = path.relative_to(root).as_posix()
                    _path_bytes(relative)
                    info = child.stat(follow_symlinks=False)
                    if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
                        raise ValidationError("Upload regular files and directories only. Keep links and environment dependencies outside the project")
                    if stat.S_ISDIR(info.st_mode):
                        directories.append((path, info))
                        pending.append((path, info))
                    else:
                        payload += info.st_size
                    entries.append((relative, path, info))
                    if payload > _PAYLOAD_LIMIT:
                        raise ValidationError("Project exceeds the workspace file count or 10 GB capacity")
        except OSError as exc:
            raise ValidationError("Could not read every project directory") from exc
    if not entries:
        raise ValidationError("The project directory is empty")
    entries.sort(key=lambda entry: entry[0].encode("utf-8"))
    inventory = hashlib.sha256(b"nodus-research-storage-inventory-v1\0")
    with tempfile.TemporaryDirectory(prefix="nodus-workspace-") as temporary:
        archive = Path(temporary) / "project.tar"
        records: list[tuple[int, int]] = []
        with archive.open("xb") as output:
            os.chmod(archive, 0o600)
            for name, path, initial in entries:
                is_directory = stat.S_ISDIR(initial.st_mode)
                size = 0 if is_directory else initial.st_size
                mode = stat.S_IMODE(initial.st_mode) & 0o777
                start = output.tell()
                output.write(_header(name, size, mode, is_directory))
                content = hashlib.sha256()
                if not is_directory:
                    # Reject replaced files before reading and check the same
                    # descriptor again after copying, including truncation/growth.
                    if not path.resolve(strict=True).is_relative_to(root):
                        raise ValidationError("Project path changed outside the selected directory")
                    # O_NONBLOCK prevents a replacement FIFO from blocking
                    # before fstat can reject its changed identity and type.
                    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0) | getattr(os, "O_NONBLOCK", 0)
                    try:
                        descriptor = os.open(path, flags)
                    except OSError as exc:
                        raise ValidationError("Project file changed or could not be read while preparing upload") from exc
                    with os.fdopen(descriptor, "rb") as source:
                        if _identity(os.fstat(source.fileno())) != _identity(initial):
                            raise ValidationError("Project changed while preparing upload. Retry after saving your files")
                        remaining = size
                        while remaining:
                            data = source.read(min(remaining, _BUFFER))
                            if not data:
                                raise ValidationError("Project file was truncated while preparing upload")
                            output.write(data)
                            content.update(data)
                            remaining -= len(data)
                        if source.read(1) or _identity(os.fstat(source.fileno())) != _identity(initial):
                            raise ValidationError("Project changed while preparing upload. Retry after saving your files")
                    output.write(bytes((-size) % 512))
                encoded = name.encode("utf-8")
                inventory.update(struct.pack(">I", len(encoded)) + encoded)
                inventory.update(struct.pack(">BQII", 2 if is_directory else 1, size, mode, 0))
                inventory.update(bytes(32) if is_directory else content.digest())
                records.append((start, output.tell() - start))
                if output.tell() - payload > _METADATA_LIMIT - 1024:
                    raise ValidationError("Project archive exceeds the metadata allowance")
            records.append((output.tell(), 1024))
            output.write(bytes(1024))
        for path, initial in directories:
            if _identity(path.lstat()) != _identity(initial):
                raise ValidationError("Project directory changed while preparing upload. Retry after saving your files")
        whole = hashlib.sha256()
        segments = []
        with archive.open("rb") as source:
            for start, length in records:
                offset = start
                while length:
                    count = min(length, _CHUNK_LIMIT)
                    digest = hashlib.sha256()
                    remaining = count
                    while remaining:
                        data = source.read(min(remaining, _BUFFER))
                        if not data:
                            raise ValidationError("Prepared project archive was truncated")
                        digest.update(data)
                        whole.update(data)
                        remaining -= len(data)
                    segments.append({"sha256": digest.hexdigest(), "offset": offset, "bytes": count})
                    offset += count
                    length -= count
        yield WorkspaceArchive({
            "version": 3,
            "archive_sha256": whole.hexdigest(),
            "archive_bytes": archive.stat().st_size,
            "inventory": {"sha256": inventory.hexdigest(), "payload_bytes": payload, "entries": len(entries)},
            "segments": segments,
        }, archive)
