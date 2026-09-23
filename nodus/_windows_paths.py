"""Portable filename policy for local Windows transfers."""

import ntpath
import os

from .errors import ValidationError


_DEVICES = {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"}
_DEVICES.update(prefix + number for prefix in ("COM", "LPT") for number in "123456789¹²³")


def validate_name(name):
    if (not isinstance(name, str) or not name or name in (".", "..")
            or name.endswith((".", " "))
            or any(ord(char) < 32 or char in '<>:"/\\|?*' for char in name)
            or name.split(".", 1)[0].rstrip(" ").upper() in _DEVICES):
        raise ValidationError("Local Windows file names cannot contain reserved devices, streams or ambiguous paths")
    try:
        name.encode("utf-16-le")
    except UnicodeError:
        raise ValidationError("Local Windows file names must contain valid Unicode") from None
    return name


def validate_names(names):
    names = list(names)
    folded_names, upper_names = set(), set()
    for name in names:
        folded = validate_name(name).casefold()
        upper = name.upper()
        if folded in folded_names or upper in upper_names:
            raise ValidationError("Directory contains names that collide on a case-insensitive Windows filesystem")
        folded_names.add(folded)
        upper_names.add(upper)
    return names


def normalized_path(path):
    value = os.fspath(path)
    if not isinstance(value, str) or value.startswith(("\\\\", "//", "\\??\\")):
        raise ValidationError("Windows transfers require an ordinary local NTFS path, not a network or device path")
    drive, tail = ntpath.splitdrive(value)
    if drive and (len(drive) != 2 or drive[1] != ":" or not tail.startswith(("\\", "/"))):
        raise ValidationError("Windows transfers require an absolute drive path or an ordinary relative path")
    for part in tail.replace("/", "\\").split("\\"):
        if part and part not in (".", ".."):
            validate_name(part)
    return ntpath.abspath(value)
