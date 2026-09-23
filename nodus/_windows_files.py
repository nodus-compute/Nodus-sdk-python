"""Handle-relative local NTFS operations for verified transfers."""

import ctypes as C
from ctypes import wintypes as W
from functools import cmp_to_key
import msvcrt
import os
from pathlib import Path
import stat
from types import SimpleNamespace
import uuid

from ._local_files import Source
from ._windows_paths import normalized_path, validate_name, validate_names
from .errors import ValidationError


_kernel = C.WinDLL("kernel32", use_last_error=True)
_ntdll = C.WinDLL("ntdll", use_last_error=True)
_READ_ATTRIBUTES, _SYNCHRONIZE, _DELETE = 0x80, 0x100000, 0x10000
_REPARSE, _DIRECTORY = 0x400, 0x10
_OPEN_REPARSE, _SYNCHRONOUS = 0x200000, 0x20


class _UnicodeString(C.Structure):
    _fields_ = [("Length", W.USHORT), ("MaximumLength", W.USHORT), ("Buffer", C.c_void_p)]


class _ObjectAttributes(C.Structure):
    _fields_ = [("Length", W.ULONG), ("RootDirectory", W.HANDLE),
                ("ObjectName", C.POINTER(_UnicodeString)), ("Attributes", W.ULONG),
                ("SecurityDescriptor", C.c_void_p), ("SecurityQualityOfService", C.c_void_p)]


class _IoStatus(C.Structure):
    _fields_ = [("Status", C.c_void_p), ("Information", C.c_size_t)]


class _FileInfo(C.Structure):
    _fields_ = [("attributes", W.DWORD), ("created", W.FILETIME), ("accessed", W.FILETIME),
                ("written", W.FILETIME), ("volume", W.DWORD), ("size_high", W.DWORD),
                ("size_low", W.DWORD), ("links", W.DWORD), ("id_high", W.DWORD), ("id_low", W.DWORD)]


class _BasicInfo(C.Structure):
    _fields_ = [("created", C.c_int64), ("accessed", C.c_int64),
                ("written", C.c_int64), ("changed", C.c_int64), ("attributes", W.DWORD)]


class _DirectoryEntry(C.Structure):
    _fields_ = [("next", W.DWORD), ("index", W.DWORD), ("created", C.c_int64),
                ("accessed", C.c_int64), ("written", C.c_int64), ("changed", C.c_int64),
                ("size", C.c_int64), ("allocated", C.c_int64), ("attributes", W.DWORD),
                ("name_length", W.DWORD), ("ea_size", W.DWORD), ("short_length", C.c_byte),
                ("short_name", W.WCHAR * 12), ("file_id", C.c_int64)]


class _RenameInfo(C.Structure):
    _fields_ = [("replace", W.DWORD), ("root", W.HANDLE), ("length", W.DWORD), ("name", W.WCHAR * 1)]


def _bind(library, name, result, *arguments):
    function = getattr(library, name)
    function.restype, function.argtypes = result, arguments
    return function


_create = _bind(_kernel, "CreateFileW", W.HANDLE, W.LPCWSTR, W.DWORD, W.DWORD, C.c_void_p, W.DWORD, W.DWORD, W.HANDLE)
_close = _bind(_kernel, "CloseHandle", W.BOOL, W.HANDLE)
_get_info = _bind(_kernel, "GetFileInformationByHandle", W.BOOL, W.HANDLE, C.POINTER(_FileInfo))
_get_info_ex = _bind(_kernel, "GetFileInformationByHandleEx", W.BOOL, W.HANDLE, C.c_int, C.c_void_p, W.DWORD)
_set_info = _bind(_kernel, "SetFileInformationByHandle", W.BOOL, W.HANDLE, C.c_int, C.c_void_p, W.DWORD)
_file_type = _bind(_kernel, "GetFileType", W.DWORD, W.HANDLE)
_drive_type = _bind(_kernel, "GetDriveTypeW", W.UINT, W.LPCWSTR)
_compare_names = _bind(_kernel, "CompareStringOrdinal", C.c_int, W.LPCWSTR, C.c_int, W.LPCWSTR, C.c_int, W.BOOL)
_volume_info = _bind(_kernel, "GetVolumeInformationByHandleW", W.BOOL, W.HANDLE, W.LPWSTR, W.DWORD,
                     C.POINTER(W.DWORD), C.POINTER(W.DWORD), C.POINTER(W.DWORD), W.LPWSTR, W.DWORD)
_nt_create = _bind(_ntdll, "NtCreateFile", C.c_long, C.POINTER(W.HANDLE), W.DWORD,
                   C.POINTER(_ObjectAttributes), C.POINTER(_IoStatus), C.c_void_p,
                   W.ULONG, W.ULONG, W.ULONG, W.ULONG, C.c_void_p, W.ULONG)
_nt_set_info = _bind(_ntdll, "NtSetInformationFile", C.c_long, W.HANDLE,
                     C.POINTER(_IoStatus), C.c_void_p, W.ULONG, C.c_int)
_dos_error = _bind(_ntdll, "RtlNtStatusToDosError", W.ULONG, C.c_long)


def _checked(success):
    if not success:
        raise C.WinError(C.get_last_error())


def _information(handle):
    information, basic = _FileInfo(), _BasicInfo()
    _checked(_get_info(handle, C.byref(information)))
    if information.attributes & _REPARSE:
        raise ValidationError("Windows transfers reject reparse points, including junctions and cloud placeholders. Use an ordinary local NTFS folder")
    if _file_type(handle) != 1:
        raise ValidationError("Windows transfers require ordinary files or directories")
    _checked(_get_info_ex(handle, 0, C.byref(basic), C.sizeof(basic)))
    mode = stat.S_IFDIR | 0o755 if information.attributes & _DIRECTORY else stat.S_IFREG | 0o644
    return SimpleNamespace(st_mode=mode, st_dev=information.volume,
                           st_ino=(information.id_high << 32) | information.id_low,
                           st_size=(information.size_high << 32) | information.size_low,
                           st_mtime_ns=basic.written * 100, st_ctime_ns=basic.changed * 100)


def _open(parent, name, *, access=_READ_ATTRIBUTES | _SYNCHRONIZE, sharing=3, disposition=1, options=0):
    validate_name(name)
    encoded = name.encode("utf-16-le")
    if len(encoded) > 65532:
        raise ValidationError("Windows file name is too long")
    buffer = C.create_unicode_buffer(name, len(encoded) // 2 + 1)
    string = _UnicodeString(len(encoded), len(encoded) + 2, C.cast(buffer, C.c_void_p))
    attributes = _ObjectAttributes(C.sizeof(_ObjectAttributes), parent, C.pointer(string), 0x40, None, None)
    handle, status = W.HANDLE(), _IoStatus()
    result = _nt_create(C.byref(handle), access, C.byref(attributes), C.byref(status), None,
                        0, sharing, disposition, options | _OPEN_REPARSE | _SYNCHRONOUS, None, 0)
    if result < 0:
        raise C.WinError(_dos_error(result))
    try:
        _information(handle)
        return handle.value
    except BaseException:
        _close(handle)
        raise


class Directory:
    def __init__(self, handles, path):
        self.handles, self.path = handles, path
        self.handle = handles[-1]

    @classmethod
    def open(cls, path, *, create=False):
        path = Path(normalized_path(path))
        if _drive_type(path.anchor) not in (2, 3):
            raise ValidationError("Windows transfers require a local NTFS drive, not a network drive")
        handle = _create("\\\\?\\" + path.anchor, 1 | _READ_ATTRIBUTES | _SYNCHRONIZE, 3, None, 3, 0x2200000, None)
        if handle == W.HANDLE(-1).value:
            raise C.WinError(C.get_last_error())
        handles = [handle]
        try:
            _information(handle)
            filesystem = C.create_unicode_buffer(256)
            _checked(_volume_info(handle, None, 0, None, None, None, filesystem, len(filesystem)))
            if filesystem.value != "NTFS":
                raise ValidationError("Windows transfers currently require a local NTFS filesystem")
            for component in path.parts[1:]:
                handles.append(_open(handles[-1], component, access=1 | _READ_ATTRIBUTES | _SYNCHRONIZE,
                                     disposition=3 if create else 1, options=1))
            return cls(handles, path)
        except BaseException:
            for held in reversed(handles):
                _close(held)
            raise

    def __enter__(self):
        return self

    def __exit__(self, *args):
        for handle in reversed(self.handles):
            _close(handle)
        self.handles.clear()

    def stat(self, name):
        handle = _open(self.handle, name, sharing=7)
        try:
            return _information(handle)
        finally:
            _close(handle)

    def child(self, name, *, create=False):
        handle = _open(self.handle, name, access=1 | _READ_ATTRIBUTES | _SYNCHRONIZE,
                       disposition=3 if create else 1, options=1)
        return Directory([handle], self.path / name)

    def names(self):
        names, first = [], True
        buffer = C.create_string_buffer(65536)
        while True:
            if not _get_info_ex(self.handle, 11 if first else 10, buffer, len(buffer)):
                error = C.get_last_error()
                if error == 18:
                    break
                raise C.WinError(error)
            first, offset = False, 0
            while True:
                if offset + C.sizeof(_DirectoryEntry) > len(buffer):
                    raise ValidationError("Invalid Windows directory enumeration")
                entry = _DirectoryEntry.from_buffer(buffer, offset)
                begin = offset + C.sizeof(_DirectoryEntry)
                end = begin + entry.name_length
                if entry.name_length % 2 or end > len(buffer) or (entry.next and (entry.next % 8 or offset + entry.next < end)):
                    raise ValidationError("Invalid Windows directory entry")
                name = bytes(buffer[begin:end]).decode("utf-16-le")
                if name not in (".", ".."):
                    names.append(name)
                if not entry.next:
                    break
                offset += entry.next
        return sorted(names)

    def validate_names(self, names):
        names = validate_names(names)
        def compare(left, right):
            result = _compare_names(left, -1, right, -1, True)
            _checked(result)
            return result - 2
        ordered = sorted(names, key=cmp_to_key(compare))
        if any(compare(left, right) == 0 for left, right in zip(ordered, ordered[1:])):
            raise ValidationError("Directory contains names that collide on a case-insensitive Windows filesystem")
        return names

    def open_read(self, name):
        handle = _open(self.handle, name, access=1 | _READ_ATTRIBUTES | _SYNCHRONIZE, sharing=1, options=0x40)
        try:
            descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
        except BaseException:
            _close(handle)
            raise
        try:
            stream = os.fdopen(descriptor, "rb")
        except BaseException:
            os.close(descriptor)
            raise
        return Source(stream, lambda: _information(handle))

    def stage(self):
        return Stage(self)


class Stage:
    def __init__(self, directory):
        self.directory, self.committed = directory, False
        name = ".nodus-download-" + uuid.uuid4().hex
        self.handle = _open(directory.handle, name, access=2 | _READ_ATTRIBUTES | _DELETE | _SYNCHRONIZE,
                            sharing=1, disposition=2, options=0x40)
        try:
            descriptor = msvcrt.open_osfhandle(self.handle, os.O_WRONLY | os.O_BINARY)
        except BaseException:
            try:
                self._discard()
            finally:
                _close(self.handle)
            raise
        try:
            self.stream = os.fdopen(descriptor, "wb")
        except BaseException:
            try:
                self._discard()
            finally:
                os.close(descriptor)
            raise

    def _discard(self):
        disposition = W.BOOL(True)
        _checked(_set_info(self.handle, 4, C.byref(disposition), C.sizeof(disposition)))

    def __enter__(self):
        return self

    def __exit__(self, *args):
        try:
            if not self.committed:
                self._discard()
        finally:
            self.stream.close()

    def write(self, data):
        return self.stream.write(data)

    def commit(self, name):
        validate_name(name)
        try:
            if not stat.S_ISREG(self.directory.stat(name).st_mode):
                raise ValidationError("Download destination must be a regular file")
        except FileNotFoundError:
            pass
        self.stream.flush()
        os.fsync(self.stream.fileno())
        encoded = name.encode("utf-16-le")
        buffer = C.create_string_buffer(C.sizeof(_RenameInfo) + len(encoded))
        rename = _RenameInfo.from_buffer(buffer)
        rename.replace, rename.root, rename.length = 1, None, len(encoded)
        C.memmove(C.addressof(buffer) + _RenameInfo.name.offset, encoded, len(encoded))
        # A simple NT filename renames within the held source directory.
        # The source and its ancestors deny relocation until publication completes.
        status = _IoStatus()
        result = _nt_set_info(self.handle, C.byref(status), buffer, len(buffer), 10)
        if result < 0:
            raise C.WinError(_dos_error(result))
        self.committed = True
