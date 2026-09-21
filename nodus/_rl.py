"""Reviewed reinforcement learning recipe runs."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .errors import APIError, NodusError, ValidationError

if TYPE_CHECKING:
    from . import AsyncWorkload, Workload


__all__ = ["RL", "AsyncRL", "RLRecipe", "RLRunPreview"]


def _mapping(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise APIError(f"RL response field {name!r} must be an object", body=value)
    return deepcopy(dict(value))


def _rows(value: Any, name: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not all(isinstance(item, Mapping) for item in value):
        raise APIError(f"RL response field {name!r} must be a list of objects", body=value)
    return [deepcopy(dict(item)) for item in value]


def _strings(value: Any, name: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise APIError(f"RL response field {name!r} must be a list of strings", body=value)
    return list(value)


def _required_text(row: Mapping[str, Any], name: str) -> str:
    value = row.get(name)
    if not isinstance(value, str) or not value:
        raise APIError(f"RL response field {name!r} must be nonempty text", body=dict(row))
    return value


def _optional_text(row: Mapping[str, Any], name: str) -> str | None:
    value = row.get(name)
    if value is None:
        return None
    if not isinstance(value, str):
        raise APIError(f"RL response field {name!r} must be text", body=dict(row))
    return value


def _required_bool(row: Mapping[str, Any], name: str) -> bool:
    value = row.get(name)
    if not isinstance(value, bool):
        raise APIError(f"RL response field {name!r} must be true or false", body=dict(row))
    return value


def _replayed(headers: Mapping[str, str]) -> bool:
    for name, value in headers.items():
        if name.lower() == "idempotent-replayed":
            return value.strip().lower() == "true"
    return False


def _configuration(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValidationError("configuration must be an explicit mapping")
    return deepcopy(dict(value))


def _launch_body(
    configuration: Any,
    review_token: Any,
    idempotency_key: Any,
) -> tuple[dict[str, Any], str]:
    body = _configuration(configuration)
    if "review_token" in body:
        raise ValidationError(
            "configuration must not contain review_token, pass the reviewed token separately"
        )
    if not isinstance(review_token, str) or not review_token:
        raise ValidationError("review_token must be the nonempty token returned by preview")
    if not isinstance(idempotency_key, str) or not idempotency_key:
        raise ValidationError("idempotency_key must be a nonempty stable key for this run")
    body["review_token"] = review_token
    return body, idempotency_key


@dataclass(frozen=True)
class RLRecipe:
    """One server-advertised recipe and its current availability."""

    id: str
    version: str
    environment_id: str
    name: str
    model: str
    model_revision: str
    taskset: str
    modes: list[str]
    available: bool
    unavailable_reason: str | None
    defaults: dict[str, Any]
    limits: dict[str, Any]
    outputs: list[dict[str, Any]]
    raw: dict[str, Any]

    @classmethod
    def from_dict(cls, value: Any) -> "RLRecipe":
        row = _mapping(value, "recipe")
        return cls(
            id=_required_text(row, "id"),
            version=_required_text(row, "version"),
            environment_id=_required_text(row, "environment_id"),
            name=_required_text(row, "name"),
            model=_required_text(row, "model"),
            model_revision=_required_text(row, "model_revision"),
            taskset=_required_text(row, "taskset"),
            modes=_strings(row.get("modes"), "modes"),
            available=_required_bool(row, "available"),
            unavailable_reason=_optional_text(row, "unavailable_reason"),
            defaults=_mapping(row.get("defaults"), "defaults"),
            limits=_mapping(row.get("limits"), "limits"),
            outputs=_rows(row.get("outputs"), "outputs"),
            raw=row,
        )


@dataclass(frozen=True)
class RLRunPreview:
    """Server-normalized configuration and the evidence needed to review it."""

    recipe: RLRecipe
    configuration: dict[str, Any]
    review_token: str
    phases: list[dict[str, Any]]
    outputs: list[dict[str, Any]]
    estimate: dict[str, Any]
    launchable: bool
    blocking_reasons: list[str]
    raw: dict[str, Any]

    @classmethod
    def from_dict(cls, value: Any) -> "RLRunPreview":
        row = _mapping(value, "preview")
        token = row.get("review_token")
        if not isinstance(token, str):
            raise APIError("RL response field 'review_token' must be text", body=row)
        return cls(
            recipe=RLRecipe.from_dict(row.get("recipe")),
            configuration=_mapping(row.get("configuration"), "configuration"),
            review_token=token,
            phases=_rows(row.get("phases"), "phases"),
            outputs=_rows(row.get("outputs"), "outputs"),
            estimate=_mapping(row.get("estimate"), "estimate"),
            launchable=_required_bool(row, "launchable"),
            blocking_reasons=_strings(row.get("blocking_reasons"), "blocking_reasons"),
            raw=row,
        )


class RL:
    """Synchronous discovery, preview, and reviewed launch operations."""

    def __init__(self, client: Any):
        self._client = client

    def list_recipes(self) -> list[RLRecipe]:
        path = "/v1/rl-recipes"
        body = self._client._one(self._client._request("GET", path), "GET", path)
        recipes = body.get("recipes")
        if not isinstance(recipes, list):
            raise APIError("RL recipe response must contain a recipes list", body=body)
        return [RLRecipe.from_dict(row) for row in recipes]

    def preview(self, configuration: Mapping[str, Any]) -> RLRunPreview:
        path = "/v1/rl-runs/preview"
        body = self._client._request("POST", path, json=_configuration(configuration))
        return RLRunPreview.from_dict(self._client._one(body, "POST", path))

    def launch(
        self,
        *,
        configuration: Mapping[str, Any],
        review_token: str,
        idempotency_key: str,
    ) -> "Workload":
        from . import Workload

        path = "/v1/rl-runs"
        body, key = _launch_body(configuration, review_token, idempotency_key)
        answered: dict[str, str] = {}
        response = self._client._request(
            "POST",
            path,
            json=body,
            idempotency_key=key,
            headers_out=answered,
        )
        workload = Workload(self._client)
        workload._absorb(self._client._one(response, "POST", path))
        workload.replayed = _replayed(answered)
        if not workload.id:
            raise NodusError("RL launch returned no workload id", body=response)
        return workload


class AsyncRL:
    """Asynchronous discovery, preview, and reviewed launch operations."""

    def __init__(self, client: Any):
        self._client = client

    async def list_recipes(self) -> list[RLRecipe]:
        path = "/v1/rl-recipes"
        response = await self._client._request("GET", path)
        body = self._client._one(response, "GET", path)
        recipes = body.get("recipes")
        if not isinstance(recipes, list):
            raise APIError("RL recipe response must contain a recipes list", body=body)
        return [RLRecipe.from_dict(row) for row in recipes]

    async def preview(self, configuration: Mapping[str, Any]) -> RLRunPreview:
        path = "/v1/rl-runs/preview"
        response = await self._client._request(
            "POST", path, json=_configuration(configuration)
        )
        return RLRunPreview.from_dict(self._client._one(response, "POST", path))

    async def launch(
        self,
        *,
        configuration: Mapping[str, Any],
        review_token: str,
        idempotency_key: str,
    ) -> "AsyncWorkload":
        from . import AsyncWorkload

        path = "/v1/rl-runs"
        body, key = _launch_body(configuration, review_token, idempotency_key)
        answered: dict[str, str] = {}
        response = await self._client._request(
            "POST",
            path,
            json=body,
            idempotency_key=key,
            headers_out=answered,
        )
        workload = AsyncWorkload(self._client)
        workload._absorb(self._client._one(response, "POST", path))
        workload.replayed = _replayed(answered)
        if not workload.id:
            raise NodusError("RL launch returned no workload id", body=response)
        return workload
