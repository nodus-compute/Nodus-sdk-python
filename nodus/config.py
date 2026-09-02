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
``expires_at`` is kept as text and never parsed here, because no two runtimes
agree on what a timestamp may contain.

Every key already in the file survives a rewrite, in any section. **Comments
and layout do not** — the file is rewritten from what was parsed, so a comment
someone added is gone after the next login. A value this module cannot read,
or cannot write back unchanged, is refused by name rather than dropped or
treated as absent.
"""

from __future__ import annotations

import contextlib
import os
import re
import stat
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


def _is_header_safe(value: str) -> bool:
    """Whether this can travel verbatim in a header or a URL.

    Printable ASCII, no spaces: a control character is a header injection, and
    non-ASCII raises from inside the transport long after the setting was read.
    Lives here so storing a credential and sending one share the one predicate.
    """
    return bool(value) and value.isascii() and value.isprintable() and " " not in value


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
            for part in _split_outside_quotes(line[1:-1], ".", where, number, raw):
                name = _read_key(part.strip(), where, number, raw)
                nested = table.setdefault(name, {})
                if not isinstance(nested, dict):
                    raise _unreadable(where, number, raw)
                table = nested
            continue
        parts = _split_outside_quotes(line, "=", where, number, raw, limit=1)
        if len(parts) != 2:
            raise _unreadable(where, number, raw)
        value = parts[1].strip()
        if len(value) < 2 or value[0] != value[-1]:
            raise _unreadable(where, number, raw)
        key = _read_key(parts[0].strip(), where, number, raw)
        if value[0] == '"':
            table[key] = _unescape(value[1:-1], where, number, raw)
        elif value[0] == "'":
            table[key] = value[1:-1]
        else:
            raise _unreadable(where, number, raw)
    return data


def _split_outside_quotes(
    text: str,
    on: str,
    where: str | os.PathLike[str],
    number: int,
    raw: str,
    limit: int | None = None,
) -> list[str]:
    """Split on a separator, ignoring the ones inside a quoted key.

    ``"a=b"`` and ``[other."a.b"]`` are both shapes :func:`_dump` itself emits,
    and splitting before unquoting tears them in half.
    """
    parts: list[str] = []
    current: list[str] = []
    quote = ""
    escaped = False
    for char in text:
        if escaped:
            current.append(char)
            escaped = False
        elif quote and char == "\\" and quote == '"':
            current.append(char)
            escaped = True
        elif quote:
            current.append(char)
            if char == quote:
                quote = ""
        elif char in ('"', "'"):
            current.append(char)
            quote = char
        elif char == on and (limit is None or len(parts) < limit):
            parts.append("".join(current))
            current = []
        else:
            current.append(char)
    if quote or escaped:
        raise _unreadable(where, number, raw)
    parts.append("".join(current))
    return parts


def _read_key(text: str, where: str | os.PathLike[str], number: int, raw: str) -> str:
    """One key, bare or quoted the way :func:`_key` writes it.

    A quoted key is unescaped like a value. Reading it verbatim instead is how
    a foreign key gains a backslash on every rewrite until nothing can read it.
    """
    if len(text) >= 2 and text[0] == text[-1] == '"':
        return _unescape(text[1:-1], where, number, raw)
    if len(text) >= 2 and text[0] == text[-1] == "'":
        return text[1:-1]
    if not _BARE_KEY.match(text):
        raise _unreadable(where, number, raw)
    return text


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


def _not_a_section(path: Path) -> ConfigurationError:
    return ConfigurationError(
        f"{path} has a [{PROFILE}] entry that is not a section, so there is "
        "nowhere to keep the credentials. Delete the file and run: nodus login"
    )


def _profile(path: Path) -> dict[str, Any]:
    data = _load(path)
    section = data.get(PROFILE, {})
    if not isinstance(section, dict):
        raise _not_a_section(path)
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


def _quote(value: str, where: str, path: str | os.PathLike[str]) -> str:
    """One TOML basic string, or a refusal naming the file and a way out.

    A raw C0 control or DEL makes a file neither this parser nor ``tomllib``
    reads back. The C1 range (0x80–0x9f) is legal TOML, but a control
    character is never a legitimate part of a credential, an address, or a
    name — and a stored one resurfaces on a terminal that honours 8-bit
    escapes. Non-ASCII text, a tenant's own name say, is fine. This refusal
    can meet a foreign value the rewrite is only carrying through, so it
    hard-stops login and logout — the message must leave the reader able to
    fix it, not just tell them which key offended.
    """
    for char in value:
        if char < " " or "\x7f" <= char <= "\x9f":
            raise ConfigurationError(
                f"{where} in {path} contains {char!r}, a control character, "
                "which is never part of a real credential, address, or name. "
                "Edit the file by hand, or delete it and run: nodus login"
            )
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _key(name: str, where: str, path: str | os.PathLike[str]) -> str:
    return name if _BARE_KEY.match(name) else _quote(name, where, path)


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
        out.append("[" + ".".join(_key(part, where, path) for part in prefix) + "]")
    for key, value in scalars:
        if not isinstance(value, str):
            raise ConfigurationError(
                f"{path} holds {where}.{key}, which is a "
                f"{type(value).__name__} this SDK cannot rewrite without "
                "changing it. Edit the file by hand, or delete it and run: "
                "nodus login"
            )
        out.append(f"{_key(key, where, path)} = {_quote(value, f'{where}.{key}', path)}")
    for key, table in tables:
        if out and out[-1]:
            out.append("")
        out.append(_dump(table, path, prefix + (key,)).rstrip("\n"))
    return "\n".join(out) + "\n"


def _restrict(path: Path) -> None:
    """0600 on the file, 0700 on its directory, where that means anything."""
    if os.name == "posix":
        os.chmod(path, 0o700 if path.is_dir() else 0o600)


def _redirects(path: Path) -> bool:
    """Whether this entry sends writes somewhere other than where it sits.

    ``is_symlink`` is False for an NTFS junction, and ``mklink /J`` builds one
    with no privilege at all — so on Windows the reparse-point attribute is
    what has to be asked, not the symlink question.
    """
    if path.is_symlink():
        return True
    if os.name != "nt":
        return False
    try:
        attributes = os.lstat(path).st_file_attributes
    except (AttributeError, OSError):
        return False
    return bool(attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def _prepare_directory(path: Path) -> None:
    """Make ``~/.nodus`` exist, be a real directory, and be ours to write in."""
    parent = path.parent
    if _redirects(parent):
        # chmod and open both follow the redirect, so what is narrowed to 0700
        # and what receives the key need not be the same directory.
        raise ConfigurationError(
            f"{parent} redirects elsewhere (a link or a junction), and the SDK "
            "will not write a credential through one. Replace it with a real "
            "directory."
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


def _writable_profile(data: dict[str, Any], path: Path) -> dict[str, Any]:
    """The ``[default]`` table, or a refusal if it cannot hold a credential.

    Two shapes get past everything upstream. ``default = "hello"`` is a valid
    entry that re-serialises cleanly, and ``[default.api_key]`` is a table
    sitting where a string goes — assigning over it would delete somebody's
    section without a word, against this module's own promise that every key
    survives or is refused by name. Both have to be found before a key exists.
    """
    section = data.get(PROFILE)
    if section is None:
        section = data[PROFILE] = {}
    if not isinstance(section, dict):
        raise _not_a_section(path)
    for name in CREDENTIAL_FIELDS + ("base_url",):
        if isinstance(section.get(name), dict):
            raise ConfigurationError(
                f"{path} has [{PROFILE}.{name}] as a section, and the "
                f"credentials need {name} to be text. Storing one would "
                "delete it. Edit the file by hand, or delete it and run: "
                "nodus login"
            )
    return section


def _rewrite(path: Path, edit: Callable[[dict[str, Any]], None]) -> None:
    data = _load(path)
    edit(_writable_profile(data, path))
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
    # Anything a previous run could not tidy away. One probe per login is a
    # slow leak into the directory holding the credential. os.unlink, like
    # every other delete in this module: on 3.10 Path.unlink binds the os
    # function early, and two spellings of delete are two layers to fake.
    for stale in path.parent.glob(".probe-*"):
        with contextlib.suppress(OSError):
            os.unlink(stale)
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
    # Parses the file, proves [default] can hold a credential, and proves
    # every key already in it can be written back.
    data = _load(path)
    _writable_profile(data, path)
    _dump(data, path)
    try:
        handle, probe = tempfile.mkstemp(
            dir=str(path.parent), prefix=".probe-", suffix=".toml"
        )
    except OSError as exc:
        raise ConfigurationError(
            f"{path.parent} cannot be written to: {exc}\n"
            "Fix its permissions and run: nodus login"
        ) from exc
    # The probe proved the point the moment it was created; failing to tidy it
    # away is not a reason to refuse a login. A survivor is swept by the next
    # call that can unlink — while unlinking keeps failing, each login does
    # leave one more probe behind, the cost of never letting cleanup block a
    # credential.
    with contextlib.suppress(OSError):
        os.close(handle)
    with contextlib.suppress(OSError):
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
    # A key that cannot travel in a request header can never be sent, and a
    # stored key that cannot be sent fails every later command after it has
    # only ever been shown redacted. Refused here, whoever the caller is.
    if not _is_header_safe(api_key):
        raise ConfigurationError(
            "api_key contains a character that cannot travel in a request "
            "header, so no client could ever send it. A key that cannot be "
            "sent is worse than no key, so it is refused rather than stored."
        )
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
