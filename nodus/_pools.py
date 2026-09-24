"""Manage customer-owned pools and explicit host enrollment."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import math
import re
from typing import Any

import httpx

from ._pool_act_proposals import PoolActProposal, PoolActProposals, act_decision
from ._pool_actions import PoolActionSettings, PoolShadowRun, PoolShadowRuns, action_policy, shadow_query, policy_ack, kill_payload, kill_ack, shadow_ack
from ._pool_proposals import PoolProposal, PoolProposals, proposal_query, proposal_decision
from ._pool_route import route_settings, route_patch, route_result
from ._pool_predict import PoolForecast, PoolRecommendations, RecommendationOutcome, predict_patch, done_payload, horizon_query, recommendation_query
from .errors import APIConnectionError, APIError, APITimeoutError, ValidationError


def _row(value: Any, strings: tuple[str, ...]) -> dict[str, Any]:
    if not isinstance(value, dict) or any(not isinstance(value.get(k), str) or not value[k] for k in strings):
        raise APIError("The API returned an invalid pool response")
    return value


def _predict_result(value: Any, pool_id: str, field: str = "pool_id") -> Any:
    if not isinstance(value, dict) or value.get(field) != pool_id:
        raise APIError("The API returned Predict data for a different pool")
    return value


def _reported_result(value: Any, outcome: str, saving: int | None) -> RecommendationOutcome:
    result = RecommendationOutcome.from_dict(value)
    if result.outcome != outcome or result.reported_saving_micros != saving:
        raise APIError("The API did not confirm the submitted manual outcome. Refresh before trying again")
    return result


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

    wait_policy: str | None = None
    wait_alpha: float | None = None
    waiting_budget_pct: float | None = None
    burst_approval: str | None = None
    burst_threshold_micros: int | None = None
    burst_timeout_behaviour: str | None = None

    @classmethod
    def from_dict(cls, value: Any) -> Pool:
        row = _row(value, ("id", "name", "kind", "state"))
        for key in ("owned_cost_micros_per_hour", "platform_rate_micros"):
            if row.get(key) is not None and (type(row[key]) is not int or not 0 <= row[key] <= 2**63 - 1):
                raise APIError("The API returned an invalid pool cost")
        for key in ("predict_enabled", "route_enabled"):
            if row.get(key) is not None and not isinstance(row[key], bool):
                raise APIError("The API returned an invalid pool capability")
        settings = {key: row.get(key) for key in ("wait_policy", "wait_alpha", "waiting_budget_pct", "burst_approval", "burst_threshold_micros", "burst_timeout_behaviour")}
        try:
            route_settings(**{key: value for key, value in settings.items() if not (key == "wait_policy" and value == "cheaper")})
        except ValidationError:
            raise APIError("The API returned invalid Route settings") from None
        return cls(row["id"], row["name"], row["kind"], row["state"],
                   row.get("predict_enabled"), row.get("route_enabled"),
                   row.get("owned_cost_micros_per_hour"), row.get("platform_rate_micros"), dict(row), **settings)


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
        if row["mode"] not in ("observe", "execute") or not re.fullmatch(r"[A-Za-z0-9_-]+", row["token"]):
            raise APIError("The API returned an invalid enrollment token")
        return cls(row["id"], row["token"], row["mode"], row["expires_at"])


@dataclass(frozen=True)
class UtilizationMetrics:
    """Server-measured device seconds and percentages, with unknown values preserved."""

    capacity_seconds: int
    unknown_seconds: int
    allocated_seconds: int | None
    busy_seconds: int | None
    idle_allocated_seconds: int | None
    stranded_seconds: int | None
    foreign_seconds: int | None
    allocation_pct: float | None
    busy_pct: float | None
    busy_of_allocated_pct: float | None
    fragmentation_seconds: int | None
    queued_seconds: int | None
    burst_seconds: int | None
    data_status: str
    burst_cost_micros: int | None = None

    @classmethod
    def from_dict(cls, value: Any) -> UtilizationMetrics:
        row = dict(_row(value, ("data_status",)))
        row.setdefault("burst_cost_micros", None)
        if row["data_status"] not in ("complete", "partial", "no_data"):
            raise APIError("The API returned an invalid utilization status")
        for key in cls.__dataclass_fields__:
            if key == "data_status":
                continue
            number = row.get(key)
            if key not in row or (number is None and key in ("capacity_seconds", "unknown_seconds")):
                raise APIError("The API returned an invalid utilization metric")
            if number is None:
                continue
            if key.endswith("_pct"):
                valid = type(number) in (int, float) and 0 <= number <= 100
            else:
                valid = type(number) is int and 0 <= number <= 2**63 - 1
            if not valid:
                raise APIError("The API returned an invalid utilization metric")
        capacity, unknown = row["capacity_seconds"], row["unknown_seconds"]
        duration_fields = ("allocated_seconds", "busy_seconds", "idle_allocated_seconds", "stranded_seconds", "foreign_seconds")
        percent_fields = ("allocation_pct", "busy_pct", "busy_of_allocated_pct")
        invalid = unknown > capacity
        if row["data_status"] == "complete":
            invalid |= unknown != 0 or any(row[key] is None for key in duration_fields)
            if not invalid:
                allocated, busy, idle, stranded, foreign = (row[key] for key in duration_fields)
                invalid |= not (busy <= allocated <= capacity and foreign <= allocated and idle == allocated - busy and stranded == capacity - allocated)
                for key, numerator, denominator in (("allocation_pct", allocated, capacity),
                        ("busy_pct", busy, capacity), ("busy_of_allocated_pct", busy, allocated)):
                    actual = row[key]
                    if denominator == 0:
                        invalid |= actual is not None
                    else:
                        invalid |= actual is None or not math.isclose(actual, 100 * numerator / denominator, rel_tol=1e-9, abs_tol=1e-9)
        else:
            invalid |= any(row[key] is not None for key in (*duration_fields, *percent_fields))
            if row["data_status"] == "partial":
                invalid |= unknown == 0
            else:
                invalid |= capacity != 0 or unknown != 0
        if invalid:
            raise APIError("The API returned contradictory utilization metrics")
        return cls(**{key: row[key] for key in cls.__dataclass_fields__})


def _utilization_time(value: Any) -> datetime:
    match = re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.(\d{1,9}))?(?:Z|[+-](?:[01]\d|2[0-3]):[0-5]\d)", value) if isinstance(value, str) else None
    try:
        if not match or (match.group(1) and any(digit != "0" for digit in match.group(1))):
            raise ValueError
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
        if parsed.minute or parsed.second or parsed.microsecond:
            raise ValueError
        return parsed
    except (ValueError, OverflowError):
        raise APIError("The API returned an invalid utilization timestamp") from None


@dataclass(frozen=True)
class UtilizationBucket:
    """One UTC time bucket with observed foreign device identities."""

    start: str
    end: str
    metrics: UtilizationMetrics
    foreign_device_ids: list[str]

    @classmethod
    def from_dict(cls, value: Any) -> UtilizationBucket:
        row = _row(value, ("start", "end"))
        if _utilization_time(row["start"]) >= _utilization_time(row["end"]):
            raise APIError("The API returned an invalid utilization bucket window")
        devices = row.get("foreign_device_ids")
        if not isinstance(devices, list) or any(not isinstance(item, str) or not item for item in devices):
            raise APIError("The API returned invalid foreign device IDs")
        return cls(row["start"], row["end"], UtilizationMetrics.from_dict(row.get("metrics")), list(devices))


@dataclass(frozen=True)
class HostUtilization:
    """One host's summary and time buckets."""

    host_id: str
    name: str
    device_count: int
    summary: UtilizationMetrics
    buckets: list[UtilizationBucket]

    @classmethod
    def from_dict(cls, value: Any) -> HostUtilization:
        row = _row(value, ("host_id", "name"))
        if type(row.get("device_count")) is not int or row["device_count"] < 0:
            raise APIError("The API returned an invalid utilization device count")
        return cls(row["host_id"], row["name"], row["device_count"],
                   UtilizationMetrics.from_dict(row.get("summary")), _rows(row, "buckets", UtilizationBucket))


@dataclass(frozen=True)
class PoolUtilization:
    """A pool's measured ledger over the server-reported time window."""

    pool_id: str
    from_: str
    to: str
    bucket: str
    summary: UtilizationMetrics
    hosts: list[HostUtilization]
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, value: Any) -> PoolUtilization:
        row = _row(value, ("pool_id", "from", "to", "bucket"))
        if row["bucket"] not in ("hour", "day"):
            raise APIError("The API returned an invalid utilization bucket")
        start, end = _utilization_time(row["from"]), _utilization_time(row["to"])
        if not timedelta(0) < end - start <= timedelta(days=31):
            raise APIError("The API returned an invalid utilization window")
        hosts = _rows(row, "hosts", HostUtilization)
        for host in hosts:
            expected = start
            for bucket in host.buckets:
                bucket_start, bucket_end = _utilization_time(bucket.start), _utilization_time(bucket.end)
                interval = timedelta(hours=1 if row["bucket"] == "hour" else 24 - expected.hour)
                expected_end = expected + min(interval, end - expected)
                if bucket_start != expected or bucket_end != expected_end:
                    raise APIError("The API returned noncontiguous utilization buckets")
                expected = bucket_end
            if expected != end:
                raise APIError("The API returned incomplete utilization buckets")
        return cls(row["pool_id"], row["from"], row["to"], row["bucket"],
                   UtilizationMetrics.from_dict(row.get("summary")), hosts, dict(row))


def _utilization_query(from_: str | None, to: str | None, bucket: str | None) -> dict[str, str]:
    params = {}
    for key, value in (("from", from_), ("to", to), ("bucket", bucket)):
        if value is not None:
            if not isinstance(value, str) or not value.strip():
                raise ValidationError("Utilization query values must be nonempty strings")
            params[key] = value
    if bucket is not None and bucket not in ("hour", "day"):
        raise ValidationError("Utilization bucket must be hour or day")
    return params


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


def _enrollment_payload(mode: str, host_id: str | None) -> dict[str, str]:
    if mode == "observe" and host_id is None:
        return {"mode": mode}
    if mode == "execute" and host_id is not None:
        return {"mode": mode, "host_id": _id(host_id, "host")}
    raise ValidationError("Use observe without a host ID or execute with the existing host ID")


def _enrollment_result(value: Any, mode: str) -> EnrollmentToken:
    token = EnrollmentToken.from_dict(value)
    if token.mode != mode:
        raise APIError("The API returned a token for a different enrollment mode")
    return token


class Pools:
    """Manage pools and their enrolled hosts."""

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

    def enrollment_token(self, pool_id: str, *, mode: str = "observe", host_id: str | None = None) -> EnrollmentToken:
        """Issue an observe token or explicitly reenroll one existing host for execution."""
        return _enrollment_result(self._request("POST", _path(pool_id) + "/enrollment-tokens",
            json=_enrollment_payload(mode, host_id)), mode)

    def hosts(self, pool_id: str) -> list[PoolHost]:
        """List a pool's hosts and their device inventory."""
        return _rows(self._request("GET", _path(pool_id) + "/hosts"), "hosts", PoolHost)

    def set_route(self, pool_id: str, enabled: bool, *, accepted_rate_version: str | None = None,
                        accepted_rate_micros: int | None = None, wait_policy: str | None = None,
                        wait_alpha: float | None = None, waiting_budget_pct: float | None = None,
                        burst_approval: str | None = None, burst_threshold_micros: int | None = None,
                        burst_timeout_behaviour: str | None = None) -> Pool:
        """Set Route with explicit rate consent for each enable request. Existing work keeps its terms."""
        settings = route_settings(wait_policy=wait_policy, wait_alpha=wait_alpha, waiting_budget_pct=waiting_budget_pct,
            burst_approval=burst_approval, burst_threshold_micros=burst_threshold_micros, burst_timeout_behaviour=burst_timeout_behaviour)
        payload = route_patch(enabled, accepted_rate_version, accepted_rate_micros, settings)
        return Pool.from_dict(route_result(self._request("PATCH", _path(pool_id), json=payload), pool_id, payload))

    def update_route_settings(self, pool_id: str, *, wait_policy: str | None = None,
                        wait_alpha: float | None = None, waiting_budget_pct: float | None = None,
                        burst_approval: str | None = None, burst_threshold_micros: int | None = None,
                        burst_timeout_behaviour: str | None = None) -> Pool:
        """Update future placement settings separately from Predict, pool name, and owned cost."""
        payload = route_settings(wait_policy=wait_policy, wait_alpha=wait_alpha, waiting_budget_pct=waiting_budget_pct,
            burst_approval=burst_approval, burst_threshold_micros=burst_threshold_micros, burst_timeout_behaviour=burst_timeout_behaviour)
        if not payload:
            raise ValidationError("Provide at least one Route setting")
        return Pool.from_dict(route_result(self._request("PATCH", _path(pool_id), json=payload), pool_id, payload))

    def set_predict(self, pool_id: str, enabled: bool, *, accepted_rate_version: str | None = None,
                          accepted_monthly_micros: int | None = None) -> Pool:
        """Set Predict with explicit consent to the returned subscription rate when enabling."""
        return Pool.from_dict(_predict_result(self._request("PATCH", _path(pool_id),
            json=predict_patch(enabled, accepted_rate_version, accepted_monthly_micros)), pool_id, "id"))

    def forecast(self, pool_id: str, *, horizon: int | None = None) -> PoolForecast:
        """Read cached hourly bands, issued calibration, and subscription refresh state."""
        return PoolForecast.from_dict(_predict_result(self._request("GET", _path(pool_id) + "/forecast", params=horizon_query(horizon)), pool_id))

    def action_proposals(self, pool_id: str, *, limit: int | None = None, cursor: str | None = None, kind: str | None = None) -> PoolActProposals:
        """Read Act intent and outcomes, following next_cursor with the same kind."""
        return PoolActProposals.from_dict(self._request("GET", _path(pool_id) + "/action-proposals", params=shadow_query(limit, cursor, kind)), pool_id)

    def approve_action_proposal(self, pool_id: str, proposal_id: str) -> PoolActProposal:
        """Approve retained evidence. Dispatch independently rechecks current authority."""
        return act_decision(self._request("POST", _path(pool_id) + "/action-proposals/" + _id(proposal_id, "act") + "/approve", json={}), pool_id, proposal_id, True)

    def reject_action_proposal(self, pool_id: str, proposal_id: str) -> PoolActProposal:
        """Reject a pending Act proposal without altering existing cleanup."""
        return act_decision(self._request("POST", _path(pool_id) + "/action-proposals/" + _id(proposal_id, "act") + "/reject", json={}), pool_id, proposal_id, False)

    def action_policies(self, pool_id: str) -> PoolActionSettings:
        """Read configured action levels and matching trusted shadow readiness."""
        return PoolActionSettings.from_dict(self._request("GET", _path(pool_id) + "/action-policies"), pool_id)

    def set_action_policy(self, pool_id: str, *, kind: str, level: str, window_cron: str, parallelism_cap: int) -> PoolActionSettings:
        """Save one complete policy. Approve and auto require funded Predict and Route."""
        body = action_policy(kind, level, window_cron, parallelism_cap)
        return policy_ack(self._request("PUT", _path(pool_id) + "/action-policies", json=body), pool_id, body)

    def set_act_kill_switch(self, pool_id: str, enabled: bool) -> PoolActionSettings:
        """Stop new Act authorization while preserving existing exact cleanup."""
        return kill_ack(self._request("PUT", _path(pool_id) + "/act-kill-switch", json=kill_payload(enabled)), pool_id, enabled)

    def shadow_runs(self, pool_id: str, *, limit: int | None = None, cursor: str | None = None, kind: str | None = None) -> PoolShadowRuns:
        """Read trusted coverage and gaps, following next_cursor with the same kind."""
        return PoolShadowRuns.from_dict(self._request("GET", _path(pool_id) + "/shadow-runs", params=shadow_query(limit, cursor, kind)), pool_id)

    def start_shadow(self, pool_id: str, *, kind: str, level: str, window_cron: str, parallelism_cap: int) -> PoolShadowRun:
        """Start a future 168-hour shadow cycle without granting action authority."""
        body = action_policy(kind, level, window_cron, parallelism_cap)
        return shadow_ack(self._request("POST", _path(pool_id) + "/shadow-runs", json=body), pool_id, body)

    def proposals(self, pool_id: str, *, limit: int | None = None, cursor: str | None = None,
                        state: str | None = None) -> PoolProposals:
        """Read burst intent states. Follow next_cursor with the same pool and state."""
        return PoolProposals.from_dict(self._request("GET", _path(pool_id) + "/proposals",
            params=proposal_query(limit, cursor, state)), pool_id)

    def approve_proposal(self, pool_id: str, proposal_id: str) -> PoolProposal:
        """Approve the immutable proposed amount. Approval does not itself rent capacity."""
        return proposal_decision(self._request("POST", _path(pool_id) + "/proposals/" +
            _id(proposal_id, "prop") + "/approve", json={}), pool_id, proposal_id, True)

    def reject_proposal(self, pool_id: str, proposal_id: str) -> PoolProposal:
        """Reject a pending proposal without changing prior resource cleanup."""
        return proposal_decision(self._request("POST", _path(pool_id) + "/proposals/" +
            _id(proposal_id, "prop") + "/reject", json={}), pool_id, proposal_id, False)

    def recommendations(self, pool_id: str, *, limit: int | None = None, cursor: str | None = None,
                        state: str | None = None) -> PoolRecommendations:
        """Read one page of advice, following next_cursor with the same pool and state."""
        return PoolRecommendations.from_dict(_predict_result(self._request("GET", _path(pool_id) + "/recommendations", params=recommendation_query(limit, cursor, state)), pool_id))

    def recommendation_done(self, pool_id: str, recommendation_id: str, outcome: str, *,
                                   reported_saving_micros: int | None = None) -> RecommendationOutcome:
        """Record a manual action without treating a predicted saving as measured."""
        return _reported_result(self._request("POST", _path(pool_id) + "/recommendations/" +
            _id(recommendation_id, "rec") + "/done", json=done_payload(outcome, reported_saving_micros)), outcome, reported_saving_micros)

    def utilization(self, pool_id: str, *, from_: str | None = None,
                          to: str | None = None, bucket: str | None = None) -> PoolUtilization:
        """Read measured utilization with optional RFC 3339 bounds and hour or day buckets."""
        return PoolUtilization.from_dict(self._request("GET", _path(pool_id) + "/utilization",
            params=_utilization_query(from_, to, bucket)))

    def drain_host(self, pool_id: str, host_id: str) -> PoolHost:
        """Request that a host drain without stopping customer processes."""
        return PoolHost.from_dict(self._request("POST", _path(pool_id, host_id) + "/drain"))

    def remove_host(self, pool_id: str, host_id: str) -> None:
        """Remove a host and revoke its access to the pool."""
        self._request("DELETE", _path(pool_id, host_id))


class AsyncPools:
    """Manage pools and their enrolled hosts."""

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

    async def enrollment_token(self, pool_id: str, *, mode: str = "observe", host_id: str | None = None) -> EnrollmentToken:
        """Issue an observe token or explicitly reenroll one existing host for execution."""
        return _enrollment_result(await self._request("POST", _path(pool_id) + "/enrollment-tokens",
            json=_enrollment_payload(mode, host_id)), mode)

    async def hosts(self, pool_id: str) -> list[PoolHost]:
        """List a pool's hosts and their device inventory."""
        return _rows(await self._request("GET", _path(pool_id) + "/hosts"), "hosts", PoolHost)

    async def set_route(self, pool_id: str, enabled: bool, *, accepted_rate_version: str | None = None,
                        accepted_rate_micros: int | None = None, wait_policy: str | None = None,
                        wait_alpha: float | None = None, waiting_budget_pct: float | None = None,
                        burst_approval: str | None = None, burst_threshold_micros: int | None = None,
                        burst_timeout_behaviour: str | None = None) -> Pool:
        """Set Route with explicit rate consent for each enable request. Existing work keeps its terms."""
        settings = route_settings(wait_policy=wait_policy, wait_alpha=wait_alpha, waiting_budget_pct=waiting_budget_pct,
            burst_approval=burst_approval, burst_threshold_micros=burst_threshold_micros, burst_timeout_behaviour=burst_timeout_behaviour)
        payload = route_patch(enabled, accepted_rate_version, accepted_rate_micros, settings)
        return Pool.from_dict(route_result(await self._request("PATCH", _path(pool_id), json=payload), pool_id, payload))

    async def update_route_settings(self, pool_id: str, *, wait_policy: str | None = None,
                        wait_alpha: float | None = None, waiting_budget_pct: float | None = None,
                        burst_approval: str | None = None, burst_threshold_micros: int | None = None,
                        burst_timeout_behaviour: str | None = None) -> Pool:
        """Update future placement settings separately from Predict, pool name, and owned cost."""
        payload = route_settings(wait_policy=wait_policy, wait_alpha=wait_alpha, waiting_budget_pct=waiting_budget_pct,
            burst_approval=burst_approval, burst_threshold_micros=burst_threshold_micros, burst_timeout_behaviour=burst_timeout_behaviour)
        if not payload:
            raise ValidationError("Provide at least one Route setting")
        return Pool.from_dict(route_result(await self._request("PATCH", _path(pool_id), json=payload), pool_id, payload))

    async def set_predict(self, pool_id: str, enabled: bool, *, accepted_rate_version: str | None = None,
                          accepted_monthly_micros: int | None = None) -> Pool:
        """Set Predict with explicit consent to the returned subscription rate when enabling."""
        return Pool.from_dict(_predict_result(await self._request("PATCH", _path(pool_id),
            json=predict_patch(enabled, accepted_rate_version, accepted_monthly_micros)), pool_id, "id"))

    async def forecast(self, pool_id: str, *, horizon: int | None = None) -> PoolForecast:
        """Read cached hourly bands, issued calibration, and subscription refresh state."""
        return PoolForecast.from_dict(_predict_result(await self._request("GET", _path(pool_id) + "/forecast", params=horizon_query(horizon)), pool_id))

    async def action_proposals(self, pool_id: str, *, limit: int | None = None, cursor: str | None = None, kind: str | None = None) -> PoolActProposals:
        """Read Act intent and outcomes, following next_cursor with the same kind."""
        return PoolActProposals.from_dict(await self._request("GET", _path(pool_id) + "/action-proposals", params=shadow_query(limit, cursor, kind)), pool_id)

    async def approve_action_proposal(self, pool_id: str, proposal_id: str) -> PoolActProposal:
        """Approve retained evidence. Dispatch independently rechecks current authority."""
        return act_decision(await self._request("POST", _path(pool_id) + "/action-proposals/" + _id(proposal_id, "act") + "/approve", json={}), pool_id, proposal_id, True)

    async def reject_action_proposal(self, pool_id: str, proposal_id: str) -> PoolActProposal:
        """Reject a pending Act proposal without altering existing cleanup."""
        return act_decision(await self._request("POST", _path(pool_id) + "/action-proposals/" + _id(proposal_id, "act") + "/reject", json={}), pool_id, proposal_id, False)

    async def action_policies(self, pool_id: str) -> PoolActionSettings:
        """Read configured action levels and matching trusted shadow readiness."""
        return PoolActionSettings.from_dict(await self._request("GET", _path(pool_id) + "/action-policies"), pool_id)

    async def set_action_policy(self, pool_id: str, *, kind: str, level: str, window_cron: str, parallelism_cap: int) -> PoolActionSettings:
        """Save one complete policy. Approve and auto require funded Predict and Route."""
        body = action_policy(kind, level, window_cron, parallelism_cap)
        return policy_ack(await self._request("PUT", _path(pool_id) + "/action-policies", json=body), pool_id, body)

    async def set_act_kill_switch(self, pool_id: str, enabled: bool) -> PoolActionSettings:
        """Stop new Act authorization while preserving existing exact cleanup."""
        return kill_ack(await self._request("PUT", _path(pool_id) + "/act-kill-switch", json=kill_payload(enabled)), pool_id, enabled)

    async def shadow_runs(self, pool_id: str, *, limit: int | None = None, cursor: str | None = None, kind: str | None = None) -> PoolShadowRuns:
        """Read trusted coverage and gaps, following next_cursor with the same kind."""
        return PoolShadowRuns.from_dict(await self._request("GET", _path(pool_id) + "/shadow-runs", params=shadow_query(limit, cursor, kind)), pool_id)

    async def start_shadow(self, pool_id: str, *, kind: str, level: str, window_cron: str, parallelism_cap: int) -> PoolShadowRun:
        """Start a future 168-hour shadow cycle without granting action authority."""
        body = action_policy(kind, level, window_cron, parallelism_cap)
        return shadow_ack(await self._request("POST", _path(pool_id) + "/shadow-runs", json=body), pool_id, body)

    async def proposals(self, pool_id: str, *, limit: int | None = None, cursor: str | None = None,
                        state: str | None = None) -> PoolProposals:
        """Read burst intent states. Follow next_cursor with the same pool and state."""
        return PoolProposals.from_dict(await self._request("GET", _path(pool_id) + "/proposals",
            params=proposal_query(limit, cursor, state)), pool_id)

    async def approve_proposal(self, pool_id: str, proposal_id: str) -> PoolProposal:
        """Approve the immutable proposed amount. Approval does not itself rent capacity."""
        return proposal_decision(await self._request("POST", _path(pool_id) + "/proposals/" +
            _id(proposal_id, "prop") + "/approve", json={}), pool_id, proposal_id, True)

    async def reject_proposal(self, pool_id: str, proposal_id: str) -> PoolProposal:
        """Reject a pending proposal without changing prior resource cleanup."""
        return proposal_decision(await self._request("POST", _path(pool_id) + "/proposals/" +
            _id(proposal_id, "prop") + "/reject", json={}), pool_id, proposal_id, False)

    async def recommendations(self, pool_id: str, *, limit: int | None = None, cursor: str | None = None,
                        state: str | None = None) -> PoolRecommendations:
        """Read one page of advice, following next_cursor with the same pool and state."""
        return PoolRecommendations.from_dict(_predict_result(await self._request("GET", _path(pool_id) + "/recommendations", params=recommendation_query(limit, cursor, state)), pool_id))

    async def recommendation_done(self, pool_id: str, recommendation_id: str, outcome: str, *,
                                   reported_saving_micros: int | None = None) -> RecommendationOutcome:
        """Record a manual action without treating a predicted saving as measured."""
        return _reported_result(await self._request("POST", _path(pool_id) + "/recommendations/" +
            _id(recommendation_id, "rec") + "/done", json=done_payload(outcome, reported_saving_micros)), outcome, reported_saving_micros)

    async def utilization(self, pool_id: str, *, from_: str | None = None,
                          to: str | None = None, bucket: str | None = None) -> PoolUtilization:
        """Read measured utilization with optional RFC 3339 bounds and hour or day buckets."""
        return PoolUtilization.from_dict(await self._request("GET", _path(pool_id) + "/utilization",
            params=_utilization_query(from_, to, bucket)))

    async def drain_host(self, pool_id: str, host_id: str) -> PoolHost:
        """Request that a host drain without stopping customer processes."""
        return PoolHost.from_dict(await self._request("POST", _path(pool_id, host_id) + "/drain"))

    async def remove_host(self, pool_id: str, host_id: str) -> None:
        """Remove a host and revoke its access to the pool."""
        await self._request("DELETE", _path(pool_id, host_id))
