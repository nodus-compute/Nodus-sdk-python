"""Workspace metadata, creation, and explicit connection recovery."""
from __future__ import annotations

import re
from typing import Any

from .errors import ValidationError


_RESEARCH_WORKSPACE_ID = re.compile(r"[A-Za-z0-9_-]{1,256}", re.ASCII)


def _retry_connection_path(workspace_id: str, tool: str) -> str:
    if type(workspace_id) is not str or _RESEARCH_WORKSPACE_ID.fullmatch(workspace_id) is None:
        raise ValidationError("Use a research workspace ID returned by Nodus")
    if type(tool) is not str or tool not in {"editor", "notebook"}:
        raise ValidationError("tool must be editor or notebook")
    return f"/v1/research-workspaces/{workspace_id}/connections/retry"


class Workspaces:
    def __init__(self, client: Any):
        self._client = client

    def create(self, name: str, *, size_gb: float) -> dict[str, Any]:
        """Create a named workspace within the server capacity limit."""
        return self._client._request("POST", "/v1/workspaces", json={"name": name, "size_gb": size_gb})

    def list(self) -> list[dict[str, Any]]:
        """Read owned workspace metadata and current writer identities."""
        return self._client._request("GET", "/v1/workspaces")["workspaces"]

    def retry_connection(self, workspace_id: str, *, tool: str) -> dict[str, Any]:
        """Schedule one explicit retry for a failed research-workspace connection."""
        path = _retry_connection_path(workspace_id, tool)
        return self._client._request("POST", path, json={"tool": tool}, max_retries=0)


class AsyncWorkspaces:
    def __init__(self, client: Any):
        self._client = client

    async def create(self, name: str, *, size_gb: float) -> dict[str, Any]:
        """Create a named workspace within the server capacity limit."""
        return await self._client._request("POST", "/v1/workspaces", json={"name": name, "size_gb": size_gb})

    async def list(self) -> list[dict[str, Any]]:
        """Read owned workspace metadata and current writer identities."""
        return (await self._client._request("GET", "/v1/workspaces"))["workspaces"]

    async def retry_connection(self, workspace_id: str, *, tool: str) -> dict[str, Any]:
        """Schedule one explicit retry for a failed research-workspace connection."""
        path = _retry_connection_path(workspace_id, tool)
        return await self._client._request("POST", path, json={"tool": tool}, max_retries=0)
