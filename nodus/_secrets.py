"""Tenant secret writes and value-free metadata reads."""
from __future__ import annotations

import re
from typing import Any
from .errors import ValidationError

_NAME = re.compile(r"\A[A-Za-z_][A-Za-z0-9_]{0,127}\Z")


def _name(name: str) -> str:
    if not isinstance(name, str) or not _NAME.fullmatch(name) or name.startswith("NODUS_"):
        raise ValidationError("Secret names must be environment variable names outside the NODUS_ prefix")
    return name


def _names(names: list[str] | None) -> list[str] | None:
    if names is None:
        return None
    if not isinstance(names, list) or len(names) > 32:
        raise ValidationError("secrets must be a list of at most 32 unique names")
    result = [_name(name) for name in names]
    if len(set(result)) != len(result):
        raise ValidationError("secrets must contain unique names")
    return result


def _write(name: str, value: str) -> dict[str, str]:
    _name(name)
    if not isinstance(value, str) or not value or "\x00" in value or len(value.encode("utf-8")) > 4096:
        raise ValidationError("Secret values must contain 1 through 4096 UTF-8 bytes without NUL characters")
    return {"name": name, "value": value}


class Secrets:
    """Write tenant secrets and read their names and versions."""
    def __init__(self, client: Any):
        self._client = client

    def put(self, name: str, value: str) -> dict[str, Any]:
        """Store a new version and return its metadata."""
        return self._client._request("POST", "/v1/secrets", json=_write(name, value))

    def list(self) -> list[dict[str, Any]]:
        """Return current secret names and versions without values."""
        return self._client._request("GET", "/v1/secrets")["secrets"]

    def delete(self, name: str) -> None:
        """Retire a secret from future bindings."""
        self._client._request("DELETE", f"/v1/secrets/{_name(name)}")


class AsyncSecrets:
    """Asynchronously manage tenant secret metadata and writes."""
    def __init__(self, client: Any):
        self._client = client

    async def put(self, name: str, value: str) -> dict[str, Any]:
        """Store a new version and return its metadata."""
        return await self._client._request("POST", "/v1/secrets", json=_write(name, value))

    async def list(self) -> list[dict[str, Any]]:
        """Return current secret names and versions without values."""
        return (await self._client._request("GET", "/v1/secrets"))["secrets"]

    async def delete(self, name: str) -> None:
        """Retire a secret from future bindings."""
        await self._client._request("DELETE", f"/v1/secrets/{_name(name)}")
