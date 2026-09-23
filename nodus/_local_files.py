"""Held local directories for verified project and sandbox transfers."""

import os
import stat
import uuid

from .errors import ValidationError


def fingerprint(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def open_directory(path, *, create=False):
    if os.name == "nt":
        from ._windows_files import Directory
        return Directory.open(path, create=create)
    from ._projects import secure_directory
    return PosixDirectory(secure_directory(path, create=create), path)


class Source:
    def __init__(self, stream, info=None):
        self.stream = stream
        self._info = info or (lambda: os.fstat(stream.fileno()))

    def read(self, size=-1):
        return self.stream.read(size)

    def info(self):
        return self._info()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.stream.close()


class PosixDirectory:
    def __init__(self, descriptor, path):
        self.descriptor, self.path = descriptor, path

    def __enter__(self):
        return self

    def __exit__(self, *args):
        os.close(self.descriptor)

    def stat(self, name):
        return os.stat(name, dir_fd=self.descriptor, follow_symlinks=False)

    def names(self):
        with os.scandir(self.descriptor) as entries:
            return sorted(entry.name for entry in entries)

    def validate_names(self, names):
        return names

    def child(self, name, *, create=False):
        if create:
            try:
                os.mkdir(name, 0o755, dir_fd=self.descriptor)
            except FileExistsError:
                pass
        descriptor = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=self.descriptor)
        return PosixDirectory(descriptor, self.path / name)

    def open_read(self, name):
        descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=self.descriptor)
        stream = os.fdopen(descriptor, "rb")
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            stream.close()
            raise ValidationError("Upload requires regular files without symlinks or special files")
        return Source(stream)

    def stage(self):
        return PosixStage(self)


class PosixStage:
    def __init__(self, directory):
        self.directory = directory
        self.name = ".nodus-download-" + uuid.uuid4().hex
        descriptor = os.open(self.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory.descriptor)
        self.stream = os.fdopen(descriptor, "wb")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        try:
            self.stream.close()
        finally:
            try:
                os.unlink(self.name, dir_fd=self.directory.descriptor)
            except FileNotFoundError:
                pass

    def write(self, data):
        return self.stream.write(data)

    def commit(self, name):
        from ._projects import no_symlinks
        self.stream.close()
        directory = self.directory
        no_symlinks(directory.path / name)
        held, current = os.fstat(directory.descriptor), directory.path.stat()
        if (held.st_dev, held.st_ino) != (current.st_dev, current.st_ino):
            raise ValidationError("Download directory changed during transfer")
        try:
            if not stat.S_ISREG(directory.stat(name).st_mode):
                raise ValidationError("Download destination must be a regular file")
        except FileNotFoundError:
            pass
        os.replace(self.name, name, src_dir_fd=directory.descriptor, dst_dir_fd=directory.descriptor)
