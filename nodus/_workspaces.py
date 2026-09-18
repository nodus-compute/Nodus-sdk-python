"""Named workspace metadata and creation."""
from __future__ import annotations

from typing import Any


class Workspaces:
    def __init__(self, client: Any):
        self._client = client

    def create(self, name: str, *, size_gb: float) -> dict[str, Any]:
        """Create a named workspace within the server capacity limit."""
        return self._client._request("POST", "/v1/workspaces", json={"name": name, "size_gb": size_gb})

    def list(self) -> list[dict[str, Any]]:
        """Read owned workspace metadata and current writer identities."""
        return self._client._request("GET", "/v1/workspaces")["workspaces"]


class AsyncWorkspaces:
    def __init__(self, client: Any):
        self._client = client

    async def create(self, name: str, *, size_gb: float) -> dict[str, Any]:
        """Create a named workspace within the server capacity limit."""
        return await self._client._request("POST", "/v1/workspaces", json={"name": name, "size_gb": size_gb})

    async def list(self) -> list[dict[str, Any]]:
        """Read owned workspace metadata and current writer identities."""
        return (await self._client._request("GET", "/v1/workspaces"))["workspaces"]
