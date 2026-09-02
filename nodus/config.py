"""``~/.nodus/config.toml`` — the file ``nodus login`` writes and the client reads.

One shape, documented and nothing more::

    [default]
    api_key = "nk_live_..."
    base_url = "https://api.nodus.run"

Anything else in the file is carried through a rewrite untouched, but a value
this module cannot read is refused by name rather than treated as absent: a
mis-set key that reads as "not configured" sends someone looking for the wrong
problem.
"""

from __future__ import annotations

import os
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
    "save_credentials",
    "clear_api_key",
]

#: The one section the SDK reads and writes.
PROFILE = "default"


def config_path() -> Path:
    """Where the credentials live."""
    return Path.home() / ".nodus" / "config.toml"


# -- reading ---------------------------------------------------------------


def _parse_simple_toml(text: str, where: str | os.PathLike[str]) -> dict[str, Any]:
    """Enough TOML for the documented shape, for Pythons without ``tomllib``.

    Sections and ``key = "value"`` lines only. A line outside that is refused
    with its number: reading half a file is how a stale address survives a
    login. ``tomllib`` arrived in 3.11 and the SDK supports 3.10, and a whole
    TOML dependency to read four lines is not worth what it costs a caller.
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
        key, value = key.strip(), value.strip()
        if not sep or not key or len(value) < 2 or value[0] != value[-1]:
            raise _unreadable(where, number, raw)
        if value[0] == '"':
            table[key] = value[1:-1].replace('\\"', '"').replace("\\\\", "\\")
        elif value[0] == "'":
            table[key] = value[1:-1]
        else:
            raise _unreadable(where, number, raw)
    return data


def _unreadable(where: str | os.PathLike[str], number: int, raw: str) -> ConfigurationError:
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
        raise ConfigurationError(f"{path} could not be read: {exc}") from exc

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


# -- writing ---------------------------------------------------------------


def _quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _dump(data: dict[str, Any], path: Path, prefix: str = "") -> str:
    """Back to TOML, keeping every key the file already had.

    Refuses what it cannot write instead of dropping it: a rewrite that loses
    somebody's setting is worse than one that stops and says which line.
    """
    scalars = [(k, v) for k, v in data.items() if not isinstance(v, dict)]
    tables = [(k, v) for k, v in data.items() if isinstance(v, dict)]
    out: list[str] = []
    if prefix and (scalars or not tables):
        out.append(f"[{prefix}]")
    for key, value in scalars:
        if not isinstance(value, str):
            raise ConfigurationError(
                f"{path} holds {prefix + '.' if prefix else ''}{key}, which this SDK "
                "cannot rewrite without changing it. Edit the file by hand, or "
                "delete it and run: nodus login"
            )
        out.append(f"{key} = {_quote(value)}")
    for key, table in tables:
        if out and out[-1]:
            out.append("")
        out.append(_dump(table, path, f"{prefix}.{key}" if prefix else key).rstrip("\n"))
    return "\n".join(out) + "\n"


def _restrict(path: Path) -> None:
    """0600 on the file, 0700 on its directory, where that means anything."""
    if os.name == "posix":
        os.chmod(path, 0o700 if path.is_dir() else 0o600)


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _restrict(path.parent)
    # Same directory, so the replace is a rename and never a partial file: a
    # half-written credential file is one nobody can log in with.
    handle, temporary = tempfile.mkstemp(dir=str(path.parent), prefix=".config-", suffix=".toml")
    try:
        _restrict(Path(temporary))
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
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


def save_credentials(api_key: str, base_url: str) -> Path:
    """Store both settings under ``[default]`` and return the path written."""
    path = config_path()

    def edit(section: dict[str, Any]) -> None:
        section["api_key"] = api_key
        section["base_url"] = base_url

    _rewrite(path, edit)
    if os.name != "posix":
        # Said rather than implied: 0600 has no equivalent here, so the file is
        # as readable as the profile it sits in.
        warnings.warn(
            f"{path} holds your API key, and this platform has no file mode "
            "that keeps other accounts on this machine out of it. Treat it as "
            "readable by anyone who can read your profile.",
            stacklevel=2,
        )
    return path


def clear_api_key() -> bool:
    """Remove the stored key, keeping the address. True if one was there."""
    path = config_path()
    if not _profile(path).get("api_key"):
        return False

    _rewrite(path, lambda section: section.pop("api_key", None))
    return True
