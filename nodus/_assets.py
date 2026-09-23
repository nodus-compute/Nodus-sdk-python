"""Import code and datasets through the authenticated asset API."""
from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass, field
from pathlib import Path
import re
import time
from typing import Any
from urllib.parse import urlsplit

import httpx

from ._connections import _ref as _connection_ref
from .errors import APIConnectionError, APIError, APITimeoutError, NodusError, ValidationError

_TIMEOUT = 660.0
_QUERY_WAIT_SECONDS = 720.0
_QUERY_HTTP_SECONDS = 15.0
_QUERY_POLL_SECONDS = 1.0
_ASSET_ID = re.compile(r"asset_[A-Za-z0-9-]{1,64}\Z")


@dataclass(frozen=True)
class Asset:
    """An imported asset with its original server fields."""

    id: str
    state: str
    kind: str = ""
    name: str = ""
    stored_bytes: int | None = None
    imported_bytes: int | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)
    export: dict[str, Any] | None = None
    error: str = ""

    @classmethod
    def from_dict(cls, row: Any) -> Asset:
        if not isinstance(row, dict) or not isinstance(row.get("id"), str):
            raise APIError("The API returned an invalid asset")
        return cls(row["id"], row.get("state", ""), row.get("kind", ""),
                   row.get("name", ""), row.get("stored_bytes"),
                   row.get("imported_bytes"), dict(row), row.get("export"), row.get("error", ""))


def _query_result(asset: Asset) -> Asset:
    if asset.state != "ready":
        raise APIError(f"Query export {asset.id} failed. Inspect the asset for its safe error and retry when corrected")
    return asset


def _id(value: str) -> str:
    if not isinstance(value, str) or not _ASSET_ID.fullmatch(value):
        raise ValidationError("Use an asset ID returned by Nodus")
    return value


def _repo(value: str, kind: str) -> str:
    if not isinstance(value, str):
        raise ValidationError("Repository must be owner/name")
    prefix = "https://github.com/" if kind == "github" else "https://huggingface.co/datasets/"
    value = value.removeprefix(prefix).rstrip("/")
    if kind == "github":
        value = value.removesuffix(".git")
    parts = value.split("/")
    if len(parts) != 2 or any(not p or p in (".", "..") or re.search(r"[?#%\\\s:]", p) for p in parts):
        raise ValidationError("Repository must be owner/name")
    return value


def _import(kind: str, value: str, ref: str | None = None,
            files: list[str] | None = None, token: str | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"kind": kind}
    if kind == "url":
        try:
            u = urlsplit(value)
            valid = u.scheme == "https" and u.hostname and not u.username and not u.password
        except (ValueError, TypeError):
            valid = False
        if not valid or re.search(r"[\x00-\x20\x7f]", value):
            raise ValidationError("Use a public HTTPS download URL without credentials")
        payload["url"] = value
    else:
        payload["repo"] = _repo(value, kind)
    if ref is not None:
        if not isinstance(ref, str) or not ref or len(ref) > 255 or re.search(r"[\x00-\x1f\x7f]", ref):
            raise ValidationError("Revision must be a nonempty string of at most 255 characters")
        payload["ref"] = ref
    if files is not None:
        if not isinstance(files, list) or len(files) > 100 or any(not isinstance(f, str) or not f for f in files):
            raise ValidationError("Files must contain at most 100 nonempty patterns")
        payload["files"] = files
    if token is not None:
        if not isinstance(token, str) or not token or re.search(r"[\x00-\x20\x7f]", token):
            raise ValidationError("Import token must be a nonempty credential without whitespace")
        payload["token"] = token
    return payload


def _query(connection: str, sql: str, format: str, branch: str | None, reuse: bool) -> dict[str, Any]:
    connection = _connection_ref(connection)
    keyword = re.match(r"[A-Za-z_][A-Za-z0-9_$]*", sql.lstrip()) if isinstance(sql, str) else None
    if keyword is None or keyword[0].upper() not in ("SELECT", "WITH"):
        raise ValidationError("Query must start with SELECT or WITH")
    if len(sql.encode("utf-8")) > 60000 or "\x00" in sql:
        raise ValidationError("Invalid connection query")
    if format not in ("parquet", "csv"):
        raise ValidationError("format must be parquet or csv")
    if not isinstance(reuse, bool):
        raise ValidationError("reuse must be a boolean")
    body: dict[str, Any] = {"kind": "connection_query", "connection_id": connection,
                            "sql": sql, "format": format, "reuse": reuse}
    if branch is not None:
        if not isinstance(branch, str) or not branch or len(branch) > 255 or re.search(r"[\x00-\x1f\x7f]", branch):
            raise ValidationError("Invalid query branch")
        body["branch"] = branch
    return body


def _response(client: Any, method: str, path: str, response: httpx.Response) -> Any:
    if response.status_code >= 400:
        client._raise(method, path, response)
    if response.is_redirect:
        raise APIError("Asset requests cannot redirect")
    if not response.content:
        return None
    try:
        return response.json()
    except ValueError:
        raise APIError("The API returned an invalid asset response") from None


def _file(path: str | Path, listing: Any) -> tuple[Path, int]:
    limit = listing.get("max_import_bytes") if isinstance(listing, dict) else None
    if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
        raise APIError("The API did not provide a valid upload limit")
    candidate = Path(path)
    if not candidate.is_file():
        raise ValidationError("Upload a file or archive")
    if candidate.stat().st_size > limit:
        raise ValidationError("File exceeds this server's upload limit")
    return candidate, limit


def _upload_identity_capability(listing: Any, key: str | None) -> None:
    if key is not None and (not isinstance(listing, dict) or listing.get("upload_idempotency") is not True):
        raise APIError("This controller does not support idempotent uploads. Upgrade the controller before uploading this project")


def _upload_headers(path: Path, idempotency_key: str | None) -> dict[str, str]:
    headers = {"Content-Type": "application/octet-stream"}
    if idempotency_key is not None:
        from . import _valid_idempotency_key
        headers["Idempotency-Key"] = _valid_idempotency_key(idempotency_key)
        digest = hashlib.sha256()
        with path.open("rb") as source:
            while chunk := source.read(64 * 1024):
                digest.update(chunk)
        headers["X-Nodus-SHA256"] = digest.hexdigest()
    return headers


def _chunks(path: Path, limit: int):
    with path.open("rb") as source:
        total = 0
        while chunk := source.read(64 * 1024):
            total += len(chunk)
            if total > limit:
                raise ValidationError("File exceeds this server's upload limit")
            yield chunk


async def _async_chunks(path: Path, limit: int):
    with path.open("rb") as source:
        total = 0
        while chunk := await asyncio.to_thread(source.read, 64 * 1024):
            total += len(chunk)
            if total > limit:
                raise ValidationError("File exceeds this server's upload limit")
            yield chunk


def _rows(body: Any) -> list[Asset]:
    rows = body.get("assets") if isinstance(body, dict) else None
    if not isinstance(rows, list):
        raise APIError("The API returned an invalid asset list")
    return [Asset.from_dict(row) for row in rows]


class Assets:
    """Manage the account's imported code and datasets."""

    def __init__(self, client: Any):
        self._client = client

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            response = self._client._http.request(method, path, timeout=kwargs.pop("timeout", _TIMEOUT),
                                                  follow_redirects=False, **kwargs)
        except httpx.TimeoutException:
            raise APITimeoutError("Asset request timed out. Check assets before repeating an import or upload") from None
        except httpx.HTTPError:
            raise APIConnectionError("Asset request failed. Check assets before repeating an import or upload") from None
        return _response(self._client, method, path, response)

    def list(self) -> list[Asset]:
        """List up to the server's 500 most recent assets."""
        return _rows(self._request("GET", "/v1/assets"))

    def upload(self, path: str | Path, *, idempotency_key: str | None = None) -> Asset:
        """Upload one file or archive and return the ready asset."""
        if idempotency_key is not None:
            from . import _valid_idempotency_key
            _valid_idempotency_key(idempotency_key)
        listing = self._request("GET", "/v1/assets")
        _upload_identity_capability(listing, idempotency_key)
        source, limit = _file(path, listing)
        return Asset.from_dict(self._request("POST", "/v1/assets/upload",
            params={"name": source.name}, content=_chunks(source, limit),
            headers=_upload_headers(source, idempotency_key)))

    def import_url(self, url: str) -> Asset:
        """Import a public HTTPS file or archive, including signed links."""
        return Asset.from_dict(self._request("POST", "/v1/assets/import", json=_import("url", url)))

    def import_github(self, repo: str, *, ref: str | None = None, token: str | None = None) -> Asset:
        """Import a GitHub repository at the requested revision."""
        return Asset.from_dict(self._request("POST", "/v1/assets/import", json=_import("github", repo, ref, token=token)))

    def import_huggingface(self, repo: str, *, ref: str | None = None,
                           files: list[str] | None = None, token: str | None = None) -> Asset:
        """Import selected files from a Hugging Face dataset repository."""
        return Asset.from_dict(self._request("POST", "/v1/assets/import", json=_import("huggingface", repo, ref, files, token)))

    def import_query(self, connection: str, sql: str, *, format: str = "parquet",
                     branch: str | None = None, reuse: bool = False) -> Asset:
        """Export a read-only database query as a normal input asset."""
        started = time.monotonic()
        asset = Asset.from_dict(self._request("POST", "/v1/assets/import", timeout=_QUERY_HTTP_SECONDS,
            json=_query(connection, sql, format, branch, reuse)))
        asset_id = _id(asset.id)
        try:
            while asset.state == "importing":
                remaining = _QUERY_WAIT_SECONDS - (time.monotonic() - started)
                if remaining <= 0:
                    raise APITimeoutError(f"Query export {asset_id} is still pending. Check this asset before repeating the import")
                time.sleep(min(_QUERY_POLL_SECONDS, remaining))
                remaining = _QUERY_WAIT_SECONDS - (time.monotonic() - started)
                if remaining <= 0:
                    raise APITimeoutError(f"Query export {asset_id} is still pending. Check this asset before repeating the import")
                try:
                    asset = Asset.from_dict(self._request("GET", f"/v1/assets/{asset_id}", timeout=min(_QUERY_HTTP_SECONDS, remaining)))
                except (APIConnectionError, APITimeoutError) as exc:
                    raise type(exc)(f"Could not observe query export {asset_id}. Inspect this asset before repeating the import") from None
            return _query_result(asset)
        except (NodusError, KeyboardInterrupt, asyncio.CancelledError) as exc:
            exc.asset_id = asset_id
            raise

    def get(self, asset_id: str) -> Asset:
        """Get an asset and its optional query export metadata."""
        return Asset.from_dict(self._request("GET", f"/v1/assets/{_id(asset_id)}"))

    def delete(self, asset_id: str) -> None:
        """Delete an asset that no active workload uses."""
        self._request("DELETE", f"/v1/assets/{_id(asset_id)}")


class AsyncAssets:
    """Manage imported code and datasets asynchronously."""

    def __init__(self, client: Any):
        self._client = client

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            response = await self._client._http.request(method, path, timeout=kwargs.pop("timeout", _TIMEOUT),
                                                        follow_redirects=False, **kwargs)
        except httpx.TimeoutException:
            raise APITimeoutError("Asset request timed out. Check assets before repeating an import or upload") from None
        except httpx.HTTPError:
            raise APIConnectionError("Asset request failed. Check assets before repeating an import or upload") from None
        return _response(self._client, method, path, response)

    async def list(self) -> list[Asset]:
        """List up to the server's 500 most recent assets."""
        return _rows(await self._request("GET", "/v1/assets"))

    async def upload(self, path: str | Path, *, idempotency_key: str | None = None) -> Asset:
        """Upload one file or archive and return the ready asset."""
        if idempotency_key is not None:
            from . import _valid_idempotency_key
            _valid_idempotency_key(idempotency_key)
        listing = await self._request("GET", "/v1/assets")
        _upload_identity_capability(listing, idempotency_key)
        source, limit = _file(path, listing)
        return Asset.from_dict(await self._request("POST", "/v1/assets/upload",
            params={"name": source.name}, content=_async_chunks(source, limit),
            headers=_upload_headers(source, idempotency_key)))

    async def import_url(self, url: str) -> Asset:
        """Import a public HTTPS file or archive, including signed links."""
        return Asset.from_dict(await self._request("POST", "/v1/assets/import", json=_import("url", url)))

    async def import_github(self, repo: str, *, ref: str | None = None, token: str | None = None) -> Asset:
        """Import a GitHub repository at the requested revision."""
        return Asset.from_dict(await self._request("POST", "/v1/assets/import", json=_import("github", repo, ref, token=token)))

    async def import_huggingface(self, repo: str, *, ref: str | None = None,
                                 files: list[str] | None = None, token: str | None = None) -> Asset:
        """Import selected files from a Hugging Face dataset repository."""
        return Asset.from_dict(await self._request("POST", "/v1/assets/import", json=_import("huggingface", repo, ref, files, token)))

    async def import_query(self, connection: str, sql: str, *, format: str = "parquet",
                           branch: str | None = None, reuse: bool = False) -> Asset:
        """Export a read-only database query as a normal input asset."""
        started = time.monotonic()
        asset = Asset.from_dict(await self._request("POST", "/v1/assets/import", timeout=_QUERY_HTTP_SECONDS,
            json=_query(connection, sql, format, branch, reuse)))
        asset_id = _id(asset.id)
        try:
            while asset.state == "importing":
                remaining = _QUERY_WAIT_SECONDS - (time.monotonic() - started)
                if remaining <= 0:
                    raise APITimeoutError(f"Query export {asset_id} is still pending. Check this asset before repeating the import")
                await asyncio.sleep(min(_QUERY_POLL_SECONDS, remaining))
                remaining = _QUERY_WAIT_SECONDS - (time.monotonic() - started)
                if remaining <= 0:
                    raise APITimeoutError(f"Query export {asset_id} is still pending. Check this asset before repeating the import")
                try:
                    asset = Asset.from_dict(await self._request("GET", f"/v1/assets/{asset_id}", timeout=min(_QUERY_HTTP_SECONDS, remaining)))
                except (APIConnectionError, APITimeoutError) as exc:
                    raise type(exc)(f"Could not observe query export {asset_id}. Inspect this asset before repeating the import") from None
            return _query_result(asset)
        except (NodusError, KeyboardInterrupt, asyncio.CancelledError) as exc:
            exc.asset_id = asset_id
            raise

    async def get(self, asset_id: str) -> Asset:
        """Get an asset and its optional query export metadata."""
        return Asset.from_dict(await self._request("GET", f"/v1/assets/{_id(asset_id)}"))

    async def delete(self, asset_id: str) -> None:
        """Delete an asset that no active workload uses."""
        await self._request("DELETE", f"/v1/assets/{_id(asset_id)}")
