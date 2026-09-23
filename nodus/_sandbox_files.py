"""Verified sandbox file transfers through durable command receipts."""

import base64
import hashlib
import json
from pathlib import PurePosixPath
import re
import stat
import uuid

from .errors import NodusError, ValidationError
from ._projects import no_symlinks
from ._local_files import fingerprint, open_directory
from ._sandboxes import SandboxExec, AsyncSandboxExec, _mutation_receipt, _valid_id


def _path(value, *, root=False):
    if root and value == ".":
        return value
    if not isinstance(value, str) or not value or len(value.encode()) > 4096 or any(c in value for c in "\\\x00\r\n"):
        raise ValidationError("Use a relative sandbox file path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in ("", ".", "..") for part in value.split("/")):
        raise ValidationError("Sandbox file paths cannot escape the project")
    return str(path)


def _hash(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value):
        raise NodusError("File response has no valid content hash")
    return value




def _output(frames, execution):
    if not execution.succeeded:
        raise NodusError("Sandbox file operation failed. Inspect execution " + execution.id)
    raw = bytearray()
    for frame in frames:
        if frame.stream == "stdout":
            raw.extend(frame.data)
        if len(raw) > 1024 * 1024:
            raise NodusError("File response exceeds its output limit")
    try:
        body = json.loads(raw)
    except (ValueError, UnicodeError):
        raise NodusError("Sandbox file operation returned invalid JSON") from None
    if not isinstance(body, dict):
        raise NodusError("Sandbox file operation returned invalid JSON")
    return body


class _Files:
    def __init__(self, sandbox):
        self._sandbox = sandbox

    def list(self, path="."):
        """List direct children of a project directory."""
        return self._drive(self._list(_path(path, root=True)))

    def read(self, path):
        """Read bytes after verifying every chunk and the complete content hash."""
        return self._drive(self._read(_path(path)))

    def write(self, path, data, *, expected_sha256=None, idempotency_key=None):
        """Create a file or replace the explicitly expected version."""
        if not isinstance(data, (str, bytes)):
            raise ValidationError("File data must be bytes or text")
        raw = data.encode() if isinstance(data, str) else data
        if expected_sha256 is not None:
            _hash(expected_sha256)
        from ._managed_agents import _key
        key = _key(idempotency_key)
        return self._drive(self._write(_path(path), raw, expected_sha256, key), mutation_key=key)

    def upload(self, source, path=None, *, idempotency_key=None):
        """Upload a file or directory without following local symlinks."""
        from ._managed_agents import _key
        key = _key(idempotency_key)
        source = no_symlinks(source)
        return self._drive(self._upload(source, _path(path or source.name), key), mutation_key=key)

    def download(self, path, destination, *, recursive=None, idempotency_key=None):
        """Download a verified file or recursively copy a project directory."""
        from . import _valid_idempotency_key
        key = _valid_idempotency_key(idempotency_key) if idempotency_key is not None else None
        return self._drive(self._download(_path(path, root=recursive is not False), no_symlinks(destination), recursive), mutation_key=key)

    def _list(self, path):
        body = yield {"operation": "list", "path": path}
        rows = body.get("entries")
        if not isinstance(rows, list) or len(rows) > 1000:
            raise NodusError("Invalid sandbox directory response")
        seen = set()
        for row in rows:
            if not isinstance(row, dict) or _path(row.get("name")) != row["name"] or "/" in row["name"]:
                raise NodusError("Unsafe sandbox directory entry")
            if row["name"] in seen or row.get("type") not in ("file", "directory"):
                raise NodusError("Unsafe or duplicated sandbox directory entry")
            seen.add(row["name"])
        return rows

    def _read(self, path, sink=None):
        offset, digest, expected, total = 0, hashlib.sha256(), None, None
        result = bytearray()
        while True:
            request = {"operation": "read", "path": path, "offset": offset, "limit": 262144}
            if expected is not None:
                request["expected_sha256"] = expected
            body = yield request
            current = _hash(body.get("sha256"))
            try:
                chunk = base64.b64decode(body["data"], validate=True)
            except (ValueError, TypeError, KeyError):
                raise NodusError("Invalid file chunk") from None
            size = body.get("size_bytes")
            if type(size) is not int or size < 0 or len(chunk) > 262144 or body.get("offset") != offset or body.get("next_offset") != offset + len(chunk):
                raise NodusError("Invalid file chunk offsets")
            if expected is not None and (current != expected or size != total):
                raise NodusError("File changed while downloading")
            expected, total = current, size
            offset += len(chunk)
            if offset > total or type(body.get("eof")) is not bool or body["eof"] != (offset == total) or (not chunk and not body["eof"]):
                raise NodusError("Incomplete file response")
            digest.update(chunk)
            if sink is not None:
                sink.write(chunk)
            else:
                result.extend(chunk)
            if body["eof"]:
                if digest.hexdigest() != expected:
                    raise NodusError("Downloaded bytes do not match the file hash")
                return bytes(result) if sink is None else {"size_bytes": total, "sha256": expected}

    def _write(self, path, raw, expected, key):
        digest = hashlib.sha256()
        base_key = key or "file-" + uuid.uuid4().hex
        result = None
        for offset in range(0, max(1, len(raw)), 32768):
            chunk = raw[offset:offset + 32768]
            body = {"operation": "write", "path": path, "offset": offset,
                    "data": base64.b64encode(chunk).decode(), "create_parents": True}
            if expected is not None:
                body["expected_sha256"] = expected
            body["_key"] = "file-" + hashlib.sha256((base_key + ":" + str(offset)).encode()).hexdigest()
            result = yield body
            digest.update(chunk)
            expected = digest.hexdigest()
            if result.get("size_bytes") != offset + len(chunk) or _hash(result.get("sha256")) != expected:
                raise NodusError("Uploaded bytes do not match the acknowledged file")
        return result

    def _upload(self, source, path, key):
        with open_directory(source.parent) as parent:
            return (yield from self._upload_at(parent, source.name, path, key))

    def _upload_at(self, parent, leaf, path, key):
        info = parent.stat(leaf)
        if stat.S_ISDIR(info.st_mode):
            with parent.child(leaf) as directory:
                for name in directory.validate_names(directory.names()):
                    child = _path(path + "/" + name)
                    yield from self._upload_at(directory, name, child, (key + "/" + name) if key else None)
            return {"path": path}
        if not stat.S_ISREG(info.st_mode) or info.st_size > 512 << 20:
            raise ValidationError("Upload requires regular files within the 512 MiB file limit")
        with parent.open_read(leaf) as source:
            before = source.info()
            if fingerprint(info) != fingerprint(before):
                raise ValidationError("Local file changed before upload")
            digest, offset, expected = hashlib.sha256(), 0, None
            while True:
                chunk = source.read(min(32768, before.st_size - offset))
                if fingerprint(source.info()) != fingerprint(before):
                    raise ValidationError("Local file changed during upload")
                if not chunk and offset < before.st_size:
                    raise ValidationError("Local file became incomplete during upload")
                body = {"operation": "write", "path": path, "offset": offset, "create_parents": True,
                        "data": base64.b64encode(chunk).decode(),
                        "_key": "file-" + hashlib.sha256((key + ":" + str(offset)).encode()).hexdigest()}
                if expected is not None:
                    body["expected_sha256"] = expected
                result = yield body
                digest.update(chunk)
                offset += len(chunk)
                expected = digest.hexdigest()
                if result.get("size_bytes") != offset or _hash(result.get("sha256")) != expected:
                    raise NodusError("Uploaded bytes do not match the acknowledged file")
                if fingerprint(source.info()) != fingerprint(before):
                    raise ValidationError("Local file changed during upload")
                if offset == before.st_size:
                    return result

    def _download(self, path, target, recursive):
        no_symlinks(target)
        if recursive is None:
            metadata = yield {"operation": "stat", "path": path}
            if metadata.get("type") == "missing":
                raise FileNotFoundError("Sandbox file is missing: " + path)
            if metadata.get("type") not in ("file", "directory"):
                raise NodusError("Download requires a regular file or directory")
            recursive = metadata["type"] == "directory"
        if recursive:
            with open_directory(target, create=True) as directory:
                rows = yield from self._list(path)
                directory.validate_names([row["name"] for row in rows])
                for row in rows:
                    remote = row["name"] if path == "." else path + "/" + row["name"]
                    yield from self._download(remote, target / row["name"], row["type"] == "directory")
            return target
        with open_directory(target.parent, create=True) as parent:
            parent.validate_names([target.name])
            with parent.stage() as output:
                yield from self._read(path, output)
                output.commit(target.name)
        return target


class SandboxFiles(_Files):
    def _drive(self, workflow, mutation_key=None):
        result, sequence = None, 0
        while True:
            try:
                request = workflow.send(result)
            except StopIteration as done:
                return done.value
            if mutation_key and "_key" not in request:
                request["_key"] = "file-" + hashlib.sha256((mutation_key + ":" + str(sequence)).encode()).hexdigest()
            sequence += 1
            try:
                result = self._request(request)
            except BaseException as error:
                workflow.close()
                if mutation_key and isinstance(error, NodusError):
                    error.body = {**(error.body if isinstance(error.body, dict) else {}), "idempotency_key": mutation_key}
                raise

    def _request(self, body):
        box = self._sandbox
        path = f"/v1/sandboxes/{_valid_id(box.id, 'sandbox')}/files"
        key = body.pop("_key", None) or "sandbox-files-" + uuid.uuid4().hex
        response = box._client._request("POST", path, json=body, idempotency_key=key)
        execution = SandboxExec(box._client, box.id)
        execution._absorb(_mutation_receipt(box._client, response, path, key, expected_sandbox_id=box.id))
        execution.wait()
        result = _output(execution.iter_output(follow=False), execution)
        if result.get("operation") != body["operation"] or result.get("path") != body["path"]:
            raise NodusError("File response differs from the requested operation")
        return result


class AsyncSandboxFiles(_Files):
    async def _drive(self, workflow, mutation_key=None):
        result, sequence = None, 0
        while True:
            try:
                request = workflow.send(result)
            except StopIteration as done:
                return done.value
            if mutation_key and "_key" not in request:
                request["_key"] = "file-" + hashlib.sha256((mutation_key + ":" + str(sequence)).encode()).hexdigest()
            sequence += 1
            try:
                result = await self._request(request)
            except BaseException as error:
                workflow.close()
                if mutation_key and isinstance(error, NodusError):
                    error.body = {**(error.body if isinstance(error.body, dict) else {}), "idempotency_key": mutation_key}
                raise

    async def _request(self, body):
        box = self._sandbox
        path = f"/v1/sandboxes/{_valid_id(box.id, 'sandbox')}/files"
        key = body.pop("_key", None) or "sandbox-files-" + uuid.uuid4().hex
        response = await box._client._request("POST", path, json=body, idempotency_key=key)
        execution = AsyncSandboxExec(box._client, box.id)
        execution._absorb(_mutation_receipt(box._client, response, path, key, expected_sandbox_id=box.id))
        await execution.wait()
        result = _output([frame async for frame in execution.iter_output(follow=False)], execution)
        if result.get("operation") != body["operation"] or result.get("path") != body["path"]:
            raise NodusError("File response differs from the requested operation")
        return result
