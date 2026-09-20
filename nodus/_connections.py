"""Verified external connections backed by tenant secret references."""
from __future__ import annotations

import re
from typing import Any
from .errors import ValidationError

_NAME = re.compile(r"\A[A-Za-z][A-Za-z0-9_-]{0,127}\Z")
_REF = re.compile(r"\A[A-Za-z_][A-Za-z0-9_-]{0,131}\Z")
_FIELD = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
_REGION = re.compile(r"\A[a-z0-9][a-z0-9-]{0,62}\Z")
_SECRET = re.compile(r"\A(?:[A-Za-z_][A-Za-z0-9_]{0,127}|sec_[A-Za-z0-9_-]{1,128})\Z")


def _ref(value: str) -> str:
    if not isinstance(value, str) or not _REF.fullmatch(value):
        raise ValidationError("Use a connection name or ID")
    return value


def _live_refs(value: Any) -> list[str] | None:
    if value is None:
        return None
    if not isinstance(value, list) or len(value) > 1 or any(not isinstance(v, str) or not _NAME.fullmatch(v) for v in value):
        raise ValidationError("connections accepts at most one connection name or ID")
    return list(value)


def _group(value: Any) -> str:
    if not isinstance(value, str) or not _FIELD.fullmatch(value):
        raise ValidationError("sweep_id must be a scalar token of at most 128 characters")
    return value


def _create(name: str, kind: str, secret: str, region: str | None, live: bool,
            scope: str | None, branch: str | None, entity: str | None,
            project: str | None) -> dict[str, Any]:
    if not isinstance(name, str) or not _NAME.fullmatch(name) or name == "github" or name.startswith("conn_"):
        raise ValidationError("Connection names use letters, digits, underscores and hyphens")
    if kind not in ("postgres", "neon", "supabase", "wandb"):
        raise ValidationError("Unsupported connection kind")
    if not isinstance(secret, str) or not _SECRET.fullmatch(secret) or secret.startswith("NODUS_"):
        raise ValidationError("secret must reference an existing tenant secret name or ID")
    if scope is not None and scope not in ("read", "write", "readwrite"):
        raise ValidationError("scope must be read, write or readwrite")
    if region is not None and (not isinstance(region, str) or not _REGION.fullmatch(region)):
        raise ValidationError("Use a valid region name")
    if not isinstance(live, bool) or live and (kind != "wandb" or scope == "read"):
        raise ValidationError("Only wandb with write scope supports live connections")
    for field in (branch, entity, project):
        if field is not None and (not isinstance(field, str) or not _FIELD.fullmatch(field)):
            raise ValidationError("Invalid connection metadata")
    if branch is not None and kind != "neon":
        raise ValidationError("branch is supported only for neon")
    if kind == "wandb" and (not entity or not project):
        raise ValidationError("wandb requires entity and project")
    if kind != "wandb" and (entity is not None or project is not None):
        raise ValidationError("entity and project are supported only for wandb")
    body: dict[str, Any] = {"name": name, "kind": kind, "secret": secret, "live_mode": live}
    for key, value in (("scope", scope), ("region", region), ("branch", branch), ("entity", entity), ("project", project)):
        if value is not None:
            body[key] = value
    return body


class Connections:
    """Manage verified, team-owned external connection metadata."""

    def __init__(self, client: Any):
        self._client = client

    def create(self, name: str, kind: str, *, secret: str, region: str | None = None,
               live: bool = False, scope: str | None = None, branch: str | None = None,
               entity: str | None = None, project: str | None = None) -> dict[str, Any]:
        """Verify and save a secret reference. Scope defaults to read, or write for wandb."""
        return self._client._request("POST", "/v1/connections", json=_create(name, kind, secret, region, live, scope, branch, entity, project), max_retries=0)

    def list(self) -> list[dict[str, Any]]:
        """List connection metadata without credential values."""
        return self._client._request("GET", "/v1/connections")["connections"]

    def get(self, connection: str) -> dict[str, Any]:
        """Get one connection by name or ID."""
        return self._client._request("GET", f"/v1/connections/{_ref(connection)}")

    def delete(self, connection: str) -> None:
        """Delete a connection that has no dependent exports or loads."""
        self._client._request("DELETE", f"/v1/connections/{_ref(connection)}")

    def verify(self, connection: str) -> dict[str, Any]:
        """Reverify the pinned credential and return updated metadata."""
        return self._client._request("POST", f"/v1/connections/{_ref(connection)}/verify")


class AsyncConnections:
    """Asynchronously manage verified connection metadata."""

    def __init__(self, client: Any):
        self._client = client

    async def create(self, name: str, kind: str, *, secret: str, region: str | None = None,
                     live: bool = False, scope: str | None = None, branch: str | None = None,
                     entity: str | None = None, project: str | None = None) -> dict[str, Any]:
        """Verify and save a secret reference. Scope defaults to read, or write for wandb."""
        return await self._client._request("POST", "/v1/connections", json=_create(name, kind, secret, region, live, scope, branch, entity, project), max_retries=0)

    async def list(self) -> list[dict[str, Any]]:
        """List connection metadata without credential values."""
        return (await self._client._request("GET", "/v1/connections"))["connections"]

    async def get(self, connection: str) -> dict[str, Any]:
        """Get one connection by name or ID."""
        return await self._client._request("GET", f"/v1/connections/{_ref(connection)}")

    async def delete(self, connection: str) -> None:
        """Delete a connection that has no dependent exports or loads."""
        await self._client._request("DELETE", f"/v1/connections/{_ref(connection)}")

    async def verify(self, connection: str) -> dict[str, Any]:
        """Reverify the pinned credential and return updated metadata."""
        return await self._client._request("POST", f"/v1/connections/{_ref(connection)}/verify")
