"""Manage customer-owned pools and observe-only host enrollment."""
from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any

import httpx

from .errors import APIConnectionError, APIError, APITimeoutError, ValidationError


def _row(value: Any, strings: tuple[str, ...]) -> dict[str, Any]:
    if not isinstance(value, dict) or any(not isinstance(value.get(k), str) or not value[k] for k in strings):
        raise APIError("The API returned an invalid pool response")
    return value


@dataclass(frozen=True)
class Pool:
    """A customer-owned pool and its server-reported configuration."""

    id: str
    name: str
    kind: str
    state: str
    predict_enabled: bool | None = None
    route_enabled: bool | None = None
    owned_cost_micros_per_hour: int | None = None
    platform_rate_micros: int | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, value: Any) -> Pool:
        row = _row(value, ("id", "name", "kind", "state"))
        for key in ("owned_cost_micros_per_hour", "platform_rate_micros"):
            if row.get(key) is not None and (type(row[key]) is not int or not 0 <= row[key] <= 2**63 - 1):
                raise APIError("The API returned an invalid pool cost")
        for key in ("predict_enabled", "route_enabled"):
            if row.get(key) is not None and not isinstance(row[key], bool):
                raise APIError("The API returned an invalid pool capability")
        return cls(row["id"], row["name"], row["kind"], row["state"],
                   row.get("predict_enabled"), row.get("route_enabled"),
                   row.get("owned_cost_micros_per_hour"), row.get("platform_rate_micros"), dict(row))


@dataclass(frozen=True)
class HostDevice:
    """One accelerator reported by an enrolled host."""

    id: str
    host_id: str
    pool_id: str
    device_index: int
    model: str
    memory_mb: int
    device_uuid: str
    state: str

    @classmethod
    def from_dict(cls, value: Any) -> HostDevice:
        row = _row(value, ("id", "host_id", "pool_id", "model", "device_uuid", "state"))
        if any(type(row.get(k)) is not int or row[k] < 0 for k in ("device_index", "memory_mb")):
            raise APIError("The API returned an invalid host device")
        return cls(**{k: row[k] for k in cls.__dataclass_fields__})


@dataclass(frozen=True)
class PoolHost:
    """An enrolled host with its health and device inventory."""

    id: str
    pool_id: str
    name: str
    agent_version: str
    agent_mode: str
    state: str
    inventory: dict[str, Any]
    devices: list[HostDevice]
    last_heartbeat_at: str | None = None
    drain_requested_at: str | None = None
    enrolled_at: str | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, value: Any) -> PoolHost:
        row = _row(value, ("id", "pool_id", "name", "agent_mode", "state"))
        if not isinstance(row.get("inventory"), dict) or not isinstance(row.get("devices"), list):
            raise APIError("The API returned an invalid host inventory")
        return cls(row["id"], row["pool_id"], row["name"], row.get("agent_version", ""),
                   row["agent_mode"], row["state"], dict(row["inventory"]),
                   [HostDevice.from_dict(device) for device in row["devices"]],
                   row.get("last_heartbeat_at"), row.get("drain_requested_at"),
                   row.get("enrolled_at"), dict(row))


@dataclass(frozen=True)
class EnrollmentToken:
    """A single-use credential shown only through the explicit token property."""

    id: str
    token: str = field(repr=False)
    mode: str
    expires_at: str

    @classmethod
    def from_dict(cls, value: Any) -> EnrollmentToken:
        row = _row(value, ("id", "token", "mode", "expires_at"))
        if row["mode"] != "observe" or not re.fullmatch(r"[A-Za-z0-9_-]+", row["token"]):
            raise APIError("The API returned an invalid enrollment token")
        return cls(row["id"], row["token"], row["mode"], row["expires_at"])


def _id(value: str, prefix: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(prefix + r"_[A-Za-z0-9_-]{1,128}", value):
        raise ValidationError(f"Use a {prefix} ID returned by Nodus")
    return value


def _path(pool_id: str, host_id: str | None = None) -> str:
    path = f"/v1/pools/{_id(pool_id, 'pool')}"
    return path if host_id is None else path + f"/hosts/{_id(host_id, 'host')}"


def _name(value: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip().encode("utf-8")) > 200 or re.search(r"[\x00-\x1f\x7f]", value):
        raise ValidationError("Pool name must be nonempty and at most 200 UTF-8 bytes")
    return value.strip()


def _patch(name: str | None, cost: int | None) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    if name is not None:
        payload["name"] = _name(name)
    if cost is not None:
        if type(cost) is not int or not 0 <= cost <= 2**63 - 1:
            raise ValidationError("Owned cost must be a nonnegative signed 64-bit integer in USD micros per hour")
        payload["owned_cost_micros_per_hour"] = cost
    if not payload:
        raise ValidationError("Supply a pool name or owned cost to update")
    return payload


def _rows(body: Any, key: str, model: Any) -> list[Any]:
    rows = body.get(key) if isinstance(body, dict) else None
    if not isinstance(rows, list):
        raise APIError(f"The API returned an invalid {key} list")
    return [model.from_dict(row) for row in rows]


def _response(client: Any, method: str, path: str, response: httpx.Response) -> Any:
    if response.status_code >= 400:
        client._raise(method, path, response)
    if response.is_redirect:
        raise APIError("Pool requests cannot redirect")
    if not response.content:
        return None
    try:
        return response.json()
    except ValueError:
        raise APIError("The API returned an invalid pool response") from None


class Pools:
    """Manage pools and their observe-only hosts."""

    def __init__(self, client: Any):
        self._client = client

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            response = self._client._http.request(method, path, follow_redirects=False, **kwargs)
        except httpx.TimeoutException:
            raise APITimeoutError("Pool request timed out. Check pool state before repeating a change") from None
        except httpx.HTTPError:
            raise APIConnectionError("Pool request failed. Check pool state before repeating a change") from None
        return _response(self._client, method, path, response)

    def create(self, name: str) -> Pool:
        """Create a pool for read-only measurement of customer hardware."""
        return Pool.from_dict(self._request("POST", "/v1/pools", json={"name": _name(name)}))

    def list(self) -> list[Pool]:
        """List pools owned by the authenticated team."""
        return _rows(self._request("GET", "/v1/pools"), "pools", Pool)

    def get(self, pool_id: str) -> Pool:
        """Read one pool's configuration."""
        return Pool.from_dict(self._request("GET", _path(pool_id)))

    def update(self, pool_id: str, *, name: str | None = None,
               owned_cost_micros_per_hour: int | None = None) -> Pool:
        """Update a pool name or customer-supplied owned cost."""
        return Pool.from_dict(self._request("PATCH", _path(pool_id),
            json=_patch(name, owned_cost_micros_per_hour)))

    def enrollment_token(self, pool_id: str) -> EnrollmentToken:
        """Issue a single-use observe token that expires after 24 hours."""
        return EnrollmentToken.from_dict(self._request("POST", _path(pool_id) + "/enrollment-tokens",
            json={"mode": "observe"}))

    def hosts(self, pool_id: str) -> list[PoolHost]:
        """List a pool's hosts and their device inventory."""
        return _rows(self._request("GET", _path(pool_id) + "/hosts"), "hosts", PoolHost)

    def drain_host(self, pool_id: str, host_id: str) -> PoolHost:
        """Request that a host drain without stopping customer processes."""
        return PoolHost.from_dict(self._request("POST", _path(pool_id, host_id) + "/drain"))

    def remove_host(self, pool_id: str, host_id: str) -> None:
        """Remove a host and revoke its access to the pool."""
        self._request("DELETE", _path(pool_id, host_id))


class AsyncPools:
    """Manage pools and their observe-only hosts."""

    def __init__(self, client: Any):
        self._client = client

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            response = await self._client._http.request(method, path, follow_redirects=False, **kwargs)
        except httpx.TimeoutException:
            raise APITimeoutError("Pool request timed out. Check pool state before repeating a change") from None
        except httpx.HTTPError:
            raise APIConnectionError("Pool request failed. Check pool state before repeating a change") from None
        return _response(self._client, method, path, response)

    async def create(self, name: str) -> Pool:
        """Create a pool for read-only measurement of customer hardware."""
        return Pool.from_dict(await self._request("POST", "/v1/pools", json={"name": _name(name)}))

    async def list(self) -> list[Pool]:
        """List pools owned by the authenticated team."""
        return _rows(await self._request("GET", "/v1/pools"), "pools", Pool)

    async def get(self, pool_id: str) -> Pool:
        """Read one pool's configuration."""
        return Pool.from_dict(await self._request("GET", _path(pool_id)))

    async def update(self, pool_id: str, *, name: str | None = None,
               owned_cost_micros_per_hour: int | None = None) -> Pool:
        """Update a pool name or customer-supplied owned cost."""
        return Pool.from_dict(await self._request("PATCH", _path(pool_id),
            json=_patch(name, owned_cost_micros_per_hour)))

    async def enrollment_token(self, pool_id: str) -> EnrollmentToken:
        """Issue a single-use observe token that expires after 24 hours."""
        return EnrollmentToken.from_dict(await self._request("POST", _path(pool_id) + "/enrollment-tokens",
            json={"mode": "observe"}))

    async def hosts(self, pool_id: str) -> list[PoolHost]:
        """List a pool's hosts and their device inventory."""
        return _rows(await self._request("GET", _path(pool_id) + "/hosts"), "hosts", PoolHost)

    async def drain_host(self, pool_id: str, host_id: str) -> PoolHost:
        """Request that a host drain without stopping customer processes."""
        return PoolHost.from_dict(await self._request("POST", _path(pool_id, host_id) + "/drain"))

    async def remove_host(self, pool_id: str, host_id: str) -> None:
        """Remove a host and revoke its access to the pool."""
        await self._request("DELETE", _path(pool_id, host_id))
