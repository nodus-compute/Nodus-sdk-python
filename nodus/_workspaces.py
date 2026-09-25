"""Named workspace metadata and creation."""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Iterator, AsyncIterator

from .errors import APIError, ValidationError


_RESEARCH_WORKSPACE_ID = re.compile(r"[A-Za-z0-9_-]{1,256}", re.ASCII)


def _path(workspace_id: str) -> str:
    if type(workspace_id) is not str or _RESEARCH_WORKSPACE_ID.fullmatch(workspace_id) is None:
        raise ValidationError("Use a workspace ID returned by Nodus")
    return f"/v1/research-workspaces/{workspace_id}"


def _key(value: str) -> str:
    from . import _valid_idempotency_key
    return _valid_idempotency_key(value)


def _page_params(limit, cursor):
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise ValidationError("Workspace page limit must be between 1 and 1000")
    if cursor is not None:
        try:
            if not isinstance(cursor, str) or len(cursor.encode('utf-8')) > 1024:
                raise ValueError('invalid cursor')
        except (ValueError, UnicodeError):
            raise ValidationError("Workspace cursor must be a string of at most 1024 bytes") from None
    return {"limit": limit, **({"cursor": cursor} if cursor else {})}


def _page(response):
    if not isinstance(response, dict):
        raise APIError("Workspace list response is invalid")
    rows, cursor = response.get("workspaces"), response.get("next_cursor", "")
    if not isinstance(rows, list) or len(rows) > 1000 or any(not isinstance(row, dict) for row in rows):
        raise APIError("Workspace list response is invalid")
    try:
        _page_params(1000, cursor)
        if cursor is None or cursor and not rows:
            raise ValidationError("Invalid page progress")
    except ValidationError:
        raise APIError("Workspace list cursor is invalid") from None
    return rows, cursor


class Workspaces:
    def __init__(self, client: Any):
        self._client = client

    def create(self, name: str, *, size_gb: float) -> dict[str, Any]:
        """Create a named workspace within the server capacity limit."""
        return self._client._request("POST", "/v1/workspaces", json={"name": name, "size_gb": size_gb})

    def list(self) -> list[dict[str, Any]]:
        """Read owned workspace metadata and current writer identities."""
        return list(self.iter())

    def list_page(self, *, limit: int = 1000, cursor: str | None = None) -> tuple[list[dict[str, Any]], str]:
        """Read one page and its opaque continuation cursor."""
        return _page(self._client._request("GET", "/v1/workspaces", params=_page_params(limit, cursor)))

    def iter(self, *, limit: int = 1000) -> Iterator[dict[str, Any]]:
        """Yield workspace records one page at a time."""
        cursor = None
        seen = set()
        while True:
            rows, next_cursor = self.list_page(limit=limit, cursor=cursor)
            if next_cursor and next_cursor in seen:
                raise APIError("Workspace pagination repeated a cursor")
            yield from rows
            if not next_cursor:
                return
            seen.add(next_cursor)
            cursor = next_cursor


    def storage(self) -> dict[str, Any]:
        """Read the account's shared saved-file allowance, usage and charges."""
        return self._client._request("GET", "/v1/research-workspaces/storage")

    def delete_files(self, workspace_id: str, *, storage_revision: int) -> dict[str, Any]:
        """Delete stopped saved files once, guarded by the observed revision."""
        from ._workspace_files import revision
        return self._client._request("DELETE", _path(workspace_id) + "/files",
                                           json={"storage_revision": revision(storage_revision)}, max_retries=0)

    def export_files(self, workspace_id: str, destination: str | os.PathLike[str], *,
                           storage_revision: int, overwrite: bool = False) -> Path:
        """Stream saved files to a verified local tar archive without extracting it."""
        from ._workspace_files import export_files
        return export_files(self._client, workspace_id, destination,
                                         storage_revision=storage_revision, overwrite=overwrite)

    def upload_files(self, workspace_id: str, directory: str | os.PathLike[str], *,
                           idempotency_key: str, replace_revision: int | None = None) -> dict[str, Any]:
        """Upload a stopped project once and return its pending verification status."""
        from ._workspace_files import upload_files
        return upload_files(self._client, workspace_id, directory,
                                         idempotency_key=idempotency_key, replace_revision=replace_revision)


class AsyncWorkspaces:
    def __init__(self, client: Any):
        self._client = client

    async def create(self, name: str, *, size_gb: float) -> dict[str, Any]:
        """Create a named workspace within the server capacity limit."""
        return await self._client._request("POST", "/v1/workspaces", json={"name": name, "size_gb": size_gb})

    async def list(self) -> list[dict[str, Any]]:
        """Read owned workspace metadata and current writer identities."""
        return [row async for row in self.iter()]

    async def list_page(self, *, limit: int = 1000, cursor: str | None = None) -> tuple[list[dict[str, Any]], str]:
        """Read one page and its opaque continuation cursor."""
        return _page(await self._client._request("GET", "/v1/workspaces", params=_page_params(limit, cursor)))

    async def iter(self, *, limit: int = 1000) -> AsyncIterator[dict[str, Any]]:
        """Yield workspace records one page at a time."""
        cursor = None
        seen = set()
        while True:
            rows, next_cursor = await self.list_page(limit=limit, cursor=cursor)
            if next_cursor and next_cursor in seen:
                raise APIError("Workspace pagination repeated a cursor")
            for row in rows:
                yield row
            if not next_cursor:
                return
            seen.add(next_cursor)
            cursor = next_cursor


    async def storage(self) -> dict[str, Any]:
        """Read the account's shared saved-file allowance, usage and charges."""
        return await self._client._request("GET", "/v1/research-workspaces/storage")

    async def delete_files(self, workspace_id: str, *, storage_revision: int) -> dict[str, Any]:
        """Delete stopped saved files once, guarded by the observed revision."""
        from ._workspace_files import revision
        return await self._client._request("DELETE", _path(workspace_id) + "/files",
                                           json={"storage_revision": revision(storage_revision)}, max_retries=0)

    async def export_files(self, workspace_id: str, destination: str | os.PathLike[str], *,
                           storage_revision: int, overwrite: bool = False) -> Path:
        """Stream saved files to a verified local tar archive without extracting it."""
        from ._workspace_files import export_files_async
        return await export_files_async(self._client, workspace_id, destination,
                                         storage_revision=storage_revision, overwrite=overwrite)

    async def upload_files(self, workspace_id: str, directory: str | os.PathLike[str], *,
                           idempotency_key: str, replace_revision: int | None = None) -> dict[str, Any]:
        """Upload a stopped project once and return its pending verification status."""
        from ._workspace_files import upload_files_async
        return await upload_files_async(self._client, workspace_id, directory,
                                         idempotency_key=idempotency_key, replace_revision=replace_revision)
