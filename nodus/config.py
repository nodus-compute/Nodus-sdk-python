"""``~/.nodus/config.toml`` — the file ``nodus login`` writes and the client reads.

One section, five keys, all text::

    [default]
    api_key    = "nk_live_..."
    base_url   = "https://api.nodus.run"
    key_id     = "key_a1b2c3d4e5f6"
    tenant     = "acme"
    expires_at = "2026-11-30T00:00:00Z"

Building a client reads ``api_key`` and ``base_url`` and nothing else. The rest
names the key, so ``nodus logout`` can say which one is left to revoke;
``expires_at`` is kept exactly as the server spelled it and is never parsed
here, because no two runtimes agree on what a timestamp may contain.

Every key already in the file survives a rewrite, in any section. **Comments
and layout do not** — the file is rewritten from what was parsed, so a comment
someone added is gone after the next login. A value this module cannot read,
or cannot write back unchanged, is refused by name rather than dropped or
treated as absent.
"""

from __future__ import annotations

import os
import re
import sys
import tempfile
import warnings
from pathlib import Path
from typing import Any, Callable

from .errors import ConfigurationError

__all__ = [
    "PROFILE",
    "config_path",
    "read_credentials",
    "ensure_writable",
    "save_credentials",
    "clear_api_key",
]

#: The one section the SDK reads and writes.
PROFILE = "default"

#: Written by a login, cleared by a logout. ``api_key`` is the credential; the
#: rest identifies it for the person who has to revoke it.
CREDENTIAL_FIELDS = ("api_key", "key_id", "tenant", "expires_at")

# A key TOML lets stand without quotes. Anything else is quoted on the way out,
# because a parsed key is written back verbatim and "my key" is not a bare key.
_BARE_KEY = re.compile(r"^[A-Za-z0-9_-]+$")


def config_path() -> Path:
    """Where the credentials live."""
    return Path.home() / ".nodus" / "config.toml"


# -- reading ---------------------------------------------------------------


def _parse_simple_toml(text: str, where: str | os.PathLike[str]) -> dict[str, Any]:
    """Enough TOML for the documented shape, for Pythons without ``tomllib``.

    Sections and ``key = "value"`` lines only. A line outside that is refused
    with its number: reading half a file is how a stale address survives a
    login. ``tomllib`` arrived in 3.11 and the SDK supports 3.10, and a whole
    TOML dependency to read five lines is not worth what it costs a caller.
    """
    data: dict[str, Any] = {}
    table: dict[str, Any] = data
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            table = data
            for part in line[1:-1].split("."):
                name = part.strip().strip('"').strip("'")
                if not name:
                    raise _unreadable(where, number, raw)
                nested = table.setdefault(name, {})
                if not isinstance(nested, dict):
                    raise _unreadable(where, number, raw)
                table = nested
            continue
        key, sep, value = line.partition("=")
        key, value = key.strip().strip('"').strip("'"), value.strip()
        if not sep or not key or len(value) < 2 or value[0] != value[-1]:
            raise _unreadable(where, number, raw)
        if value[0] == '"':
            table[key] = _unescape(value[1:-1], where, number, raw)
        elif value[0] == "'":
            table[key] = value[1:-1]
        else:
            raise _unreadable(where, number, raw)
    return data


def _unescape(body: str, where: str | os.PathLike[str], number: int, raw: str) -> str:
    """``\\\\`` and ``\\"`` only. Any other escape is refused, not guessed at."""
    out: list[str] = []
    chars = iter(body)
    for char in chars:
        if char != "\\":
            out.append(char)
            continue
        following = next(chars, "")
        if following not in ('"', "\\"):
            raise _unreadable(where, number, raw)
        out.append(following)
    return "".join(out)


def _unreadable(
    where: str | os.PathLike[str], number: int, raw: str
) -> ConfigurationError:
    return ConfigurationError(
        f"{where} line {number} is not something this Python can read: {raw.strip()!r}\n"
        'Only sections and key = "value" lines are understood below Python 3.11. '
        "Fix the line, or delete the file and run: nodus login"
    )


def _load(path: Path) -> dict[str, Any]:
    """The file as a mapping. An absent file is an empty one, not an error."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except OSError as exc:
        raise ConfigurationError(
            f"{path} could not be read: {exc}\n"
            "Fix it, or delete it and run: nodus login"
        ) from exc

    if sys.version_info >= (3, 11):
        import tomllib

        try:
            return tomllib.loads(text)
        except tomllib.TOMLDecodeError as exc:
            raise ConfigurationError(
                f"{path} is not valid TOML: {exc}\n"
                "Fix the file, or delete it and run: nodus login"
            ) from exc
    return _parse_simple_toml(text, path)


def _profile(path: Path) -> dict[str, Any]:
    data = _load(path)
    section = data.get(PROFILE, {})
    if not isinstance(section, dict):
        raise ConfigurationError(
            f"{path} has a [{PROFILE}] entry that is not a section. "
            "Delete the file and run: nodus login"
        )
    return section


def _string(path: Path, section: dict[str, Any], name: str) -> str:
    value = section.get(name)
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ConfigurationError(
            f"{name} in {path} must be text in quotes, not {type(value).__name__}. "
            "Fix the line, or delete the file and run: nodus login"
        )
    return value


def read_credentials() -> tuple[str, str]:
    """``(api_key, base_url)`` from the file. Either is ``""`` when unset."""
    path = config_path()
    section = _profile(path)
    return _string(path, section, "api_key"), _string(path, section, "base_url")


def read_metadata() -> dict[str, str]:
    """What names the stored key: ``key_id``, ``tenant``, ``expires_at``.

    Only the keys the file actually carries, so a file written before these
    existed reads as ``{}`` rather than as three empty strings.
    """
    path = config_path()
    section = _profile(path)
    found = {}
    for name in ("key_id", "tenant", "expires_at"):
        value = _string(path, section, name)
        if value:
            found[name] = value
    return found


# -- writing ---------------------------------------------------------------


def _quote(value: str, where: str) -> str:
    """One TOML basic string, or a refusal.

    A basic string cannot carry a raw control character, so writing one
    produces a file neither this parser nor ``tomllib`` will read back — and
    the tool that wrote it is then the tool that cannot fix it.
    """
    for char in value:
        if char < " " or char == "\x7f":
            raise ConfigurationError(
                f"{where} contains {char!r}, which cannot be written to a TOML "
                "file. A credential or address with a control character in it "
                "is not one this SDK will store."
            )
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _key(name: str, where: str) -> str:
    return name if _BARE_KEY.match(name) else _quote(name, where)


def _dump(data: dict[str, Any], path: Path, prefix: tuple[str, ...] = ()) -> str:
    """Back to TOML, keeping every key the file already had.

    Refuses what it cannot write instead of dropping it: a rewrite that loses
    somebody's setting, or that emits a line the next read rejects, is worse
    than one that stops and says which key. Comments are not carried through —
    the module docstring says so.
    """
    scalars = [(k, v) for k, v in data.items() if not isinstance(v, dict)]
    tables = [(k, v) for k, v in data.items() if isinstance(v, dict)]
    where = ".".join(prefix) if prefix else str(path)
    out: list[str] = []
    if prefix and (scalars or not tables):
        out.append("[" + ".".join(_key(part, where) for part in prefix) + "]")
    for key, value in scalars:
        if not isinstance(value, str):
            raise ConfigurationError(
                f"{path} holds {where}.{key}, which is a "
                f"{type(value).__name__} this SDK cannot rewrite without "
                "changing it. Edit the file by hand, or delete it and run: "
                "nodus login"
            )
        out.append(f"{_key(key, where)} = {_quote(value, f'{where}.{key}')}")
    for key, table in tables:
        if out and out[-1]:
            out.append("")
        out.append(_dump(table, path, prefix + (key,)).rstrip("\n"))
    return "\n".join(out) + "\n"


def _restrict(path: Path) -> None:
    """0600 on the file, 0700 on its directory, where that means anything."""
    if os.name == "posix":
        os.chmod(path, 0o700 if path.is_dir() else 0o600)


def _prepare_directory(path: Path) -> None:
    """Make ``~/.nodus`` exist, be a real directory, and be ours to write in."""
    parent = path.parent
    if parent.is_symlink():
        # chmod and open both follow the link, so what is narrowed to 0700 and
        # what receives the key need not be the same directory.
        raise ConfigurationError(
            f"{parent} is a symbolic link, and the SDK will not write a "
            "credential through one. Replace it with a real directory."
        )
    try:
        parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ConfigurationError(
            f"{parent} could not be created: {exc}\n"
            "Remove whatever is in the way and run: nodus login"
        ) from exc
    _restrict(parent)


def _write_atomic(path: Path, text: str) -> None:
    _prepare_directory(path)
    # Same directory, so the replace is a rename within one filesystem and
    # never a partial file. fsync first: the rename can otherwise land ahead of
    # the bytes it points at, leaving an empty config after a crash.
    handle, temporary = tempfile.mkstemp(
        dir=str(path.parent), prefix=".config-", suffix=".toml"
    )
    try:
        _restrict(Path(temporary))
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(temporary, path)
    except BaseException:
        # The half-written file holds the key, so it goes even while unwinding.
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _rewrite(path: Path, edit: Callable[[dict[str, Any]], None]) -> None:
    data = _load(path)
    section = data.get(PROFILE)
    if section is None:
        section = data[PROFILE] = {}
    if not isinstance(section, dict):
        raise ConfigurationError(
            f"{path} has a [{PROFILE}] entry that is not a section. "
            "Delete the file and run: nodus login"
        )
    edit(section)
    _write_atomic(path, _dump(data, path))


def ensure_writable() -> None:
    """Prove the file can be written, before anything mints a key.

    The console issues the key inside the call that releases it, so a write
    that fails afterwards leaves a live credential nobody has a copy of.
    Everything the directory and the existing file can refuse is refused here
    instead, while there is still nothing to lose. What only the new value can
    carry — a control character in the key itself — is still caught at the
    write, which is why the caller shows the key when that write fails.
    """
    path = config_path()
    _prepare_directory(path)
    if path.exists() and not path.is_file():
        raise ConfigurationError(
            f"{path} is not a file, so the credentials cannot be written "
            "there. Remove it and run: nodus login"
        )
    if path.exists() and not os.access(path, os.W_OK):
        raise ConfigurationError(
            f"{path} is not writable. Fix its permissions, or delete it and "
            "run: nodus login"
        )
    # Parses the file and proves every key in it can be written back.
    _dump(_load(path), path)
    try:
        handle, probe = tempfile.mkstemp(
            dir=str(path.parent), prefix=".probe-", suffix=".toml"
        )
    except OSError as exc:
        raise ConfigurationError(
            f"{path.parent} cannot be written to: {exc}\n"
            "Fix its permissions and run: nodus login"
        ) from exc
    os.close(handle)
    os.unlink(probe)


def save_credentials(
    api_key: str,
    base_url: str,
    *,
    key_id: str = "",
    tenant: str = "",
    expires_at: str = "",
) -> Path:
    """Store the key and what names it, and return the path written.

    A field the server did not send is removed rather than stored empty, so a
    key_id left over from an earlier login never outlives the key it named.
    """
    path = config_path()
    fields = {"key_id": key_id, "tenant": tenant, "expires_at": expires_at}

    def edit(section: dict[str, Any]) -> None:
        section["api_key"] = api_key
        section["base_url"] = base_url
        for name, value in fields.items():
            if value:
                section[name] = value
            else:
                section.pop(name, None)

    _rewrite(path, edit)
    if os.name != "posix":
        warnings.warn(
            f"{path} holds your API key. This platform has no 0600, so the "
            "file inherits the permissions of your profile directory rather "
            "than being narrowed further.",
            stacklevel=2,
        )
    return path


def clear_api_key() -> dict[str, str] | None:
    """Remove the stored key and what named it.

    ``None`` when there was no key to remove. Otherwise the metadata that went
    with it, which is empty for a file written before those fields existed.
    """
    path = config_path()
    section = _profile(path)
    if not section.get("api_key"):
        return None
    removed = read_metadata()

    def edit(profile: dict[str, Any]) -> None:
        for name in CREDENTIAL_FIELDS:
            profile.pop(name, None)

    _rewrite(path, edit)
    return removed
