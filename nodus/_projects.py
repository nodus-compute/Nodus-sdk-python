"""Bounded immutable packaging for managed project uploads."""

from contextlib import contextmanager
import gzip
import hashlib
import os
from pathlib import Path
import stat
import tarfile
import tempfile

from .errors import APIError, ValidationError


EXCLUDED = frozenset({".git", ".env", ".ssh", ".aws", ".azure", ".gcloud", ".cache",
                      ".venv", "venv", "node_modules", "__pycache__", "state", ".nodus"})


def setup_command(value):
    try:
        valid = isinstance(value, str) and "\x00" not in value and len(value.encode("utf-8")) <= 8192
    except UnicodeError:
        valid = False
    if not valid:
        raise ValidationError("setup must contain at most 8192 UTF-8 bytes without NUL characters")
    return value


def excluded(name):
    if os.name == "nt":
        name = name.casefold()
    return name in EXCLUDED or name.startswith(".env.") or name.endswith(".pyc")


def project_limit(capabilities):
    limit = capabilities.get("max_project_bytes")
    if limit is None:
        rows = capabilities.get("templates", [])
        limit = next((row.get("max_project_bytes") for row in rows
                      if row.get("id") == "nodus:agent-tools-v1"), None)
    if type(limit) is not int or limit <= 0:
        raise APIError("The server did not provide a valid project upload limit")
    return limit


def no_symlinks(path):
    if os.name == "nt":
        from ._windows_paths import normalized_path
        return Path(normalized_path(path))
    path = Path(os.path.abspath(path))
    for ancestor in (path, *path.parents):
        if ancestor.is_symlink():
            raise ValidationError("Project and file paths cannot traverse symlinks")
    return path


def secure_directory(path, *, create=False):
    if not hasattr(os, "O_NOFOLLOW") or os.open not in os.supports_dir_fd:
        raise ValidationError("Secure local file transfers require a POSIX filesystem")
    descriptor = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            try:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            except FileNotFoundError:
                if not create:
                    raise
                try:
                    os.mkdir(part, 0o755, dir_fd=descriptor)
                except FileExistsError:
                    pass
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


@contextmanager
def _held_directory(path):
    descriptor = secure_directory(path)
    try:
        yield descriptor
    finally:
        os.close(descriptor)


@contextmanager
def archive_project(project, limit):
    if os.name == "nt":
        with _archive_windows_project(project, limit) as target:
            yield target
        return
    if not hasattr(os, "fwalk") or not hasattr(os, "O_NOFOLLOW"):
        raise ValidationError("Secure project uploads require a POSIX filesystem")
    root = no_symlinks(project)
    if not root.is_dir():
        raise ValidationError("project must name a local directory")
    with _held_directory(root) as root_fd, tempfile.TemporaryDirectory(prefix="nodus-project-") as temporary:
        target = Path(temporary) / "project.tar.gz"
        total = count = 0
        with target.open("wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0, filename="") as compressed:
            with tarfile.open(fileobj=compressed, mode="w|") as archive:
                for directory, dirs, files, directory_fd in os.fwalk(".", dir_fd=root_fd, follow_symlinks=False):
                    dirs[:] = sorted(name for name in dirs if not excluded(name))
                    for name in dirs:
                        if stat.S_ISLNK(os.stat(name, dir_fd=directory_fd, follow_symlinks=False).st_mode):
                            raise ValidationError("Project contains a symlink")
                    for name in sorted(files):
                        if excluded(name):
                            continue
                        before = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                        if not stat.S_ISREG(before.st_mode):
                            raise ValidationError("Project contains a symlink or special file")
                        total += before.st_size
                        count += 1
                        if total > limit or count > 10000:
                            raise ValidationError("Project exceeds the server upload limit")
                        descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd)
                        with os.fdopen(descriptor, "rb") as source:
                            actual = os.fstat(source.fileno())
                            if not stat.S_ISREG(actual.st_mode):
                                raise ValidationError("Project contains a symlink or special file")
                            if (actual.st_dev, actual.st_ino, actual.st_size) != (before.st_dev, before.st_ino, before.st_size):
                                raise ValidationError("Project changed while packaging")
                            entry = tarfile.TarInfo((Path(directory) / name).as_posix())
                            entry.size = actual.st_size
                            entry.mode = 0o755 if actual.st_mode & 0o111 else 0o644
                            archive.addfile(entry, source)
                            after = os.fstat(source.fileno())
                            if (actual.st_size, actual.st_mtime_ns, actual.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                                raise ValidationError("Project changed while packaging")
        if target.stat().st_size > limit:
            raise ValidationError("Project archive exceeds the server upload limit")
        yield target


def _windows_project_files(directory, prefix=""):
    directories = []
    for name in directory.validate_names(directory.names()):
        if excluded(name):
            continue
        information = directory.stat(name)
        if stat.S_ISDIR(information.st_mode):
            directories.append(name)
        elif stat.S_ISREG(information.st_mode):
            yield prefix + name, directory, name, information
        else:
            raise ValidationError("Project contains a symlink or special file")
    for name in directories:
        with directory.child(name) as child:
            yield from _windows_project_files(child, prefix + name + "/")


@contextmanager
def _archive_windows_project(project, limit):
    from ._local_files import fingerprint, open_directory
    with open_directory(no_symlinks(project)) as root, tempfile.TemporaryDirectory(prefix="nodus-project-") as temporary:
        target = Path(temporary) / "project.tar.gz"
        total = count = 0
        with target.open("wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0, filename="") as compressed:
            with tarfile.open(fileobj=compressed, mode="w|") as archive:
                for path, directory, name, before in _windows_project_files(root):
                    total += before.st_size
                    count += 1
                    if total > limit or count > 10000:
                        raise ValidationError("Project exceeds the server upload limit")
                    with directory.open_read(name) as source:
                        actual = source.info()
                        if fingerprint(before) != fingerprint(actual):
                            raise ValidationError("Project changed while packaging")
                        entry = tarfile.TarInfo(path)
                        entry.size, entry.mode = actual.st_size, 0o644
                        archive.addfile(entry, source)
                        if fingerprint(actual) != fingerprint(source.info()):
                            raise ValidationError("Project changed while packaging")
        if target.stat().st_size > limit:
            raise ValidationError("Project archive exceeds the server upload limit")
        yield target


def _cached(client, path, key):
    cache = getattr(client, "_project_uploads", None)
    if cache is None:
        cache = client._project_uploads = {}
    hasher = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            hasher.update(chunk)
    digest = hasher.hexdigest()
    previous = cache.get(key)
    if previous and previous[0] != digest:
        raise ValidationError("Project changed for this idempotency key. Use the original files or a new key")
    return cache, digest, previous[1] if previous else None


def upload_project(client, project, key=None):
    limit = project_limit(client._request("GET", "/v1/sandboxes/capabilities"))
    with archive_project(project, limit) as path:
        cache, digest, existing = _cached(client, path, key) if key else ({}, "", None)
        if existing:
            return existing
        asset = client.assets.upload(path)
    if asset.state != "ready":
        raise APIError("Project asset is not ready")
    if key:
        cache[key] = (digest, asset.id)
    return asset.id


async def upload_project_async(client, project, key=None):
    limit = project_limit(await client._request("GET", "/v1/sandboxes/capabilities"))
    with archive_project(project, limit) as path:
        cache, digest, existing = _cached(client, path, key) if key else ({}, "", None)
        if existing:
            return existing
        asset = await client.assets.upload(path)
    if asset.state != "ready":
        raise APIError("Project asset is not ready")
    if key:
        cache[key] = (digest, asset.id)
    return asset.id
