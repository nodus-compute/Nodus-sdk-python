"""Action policy intent and trusted shadow coverage without execution authority."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
import re
from typing import Any

from ._pool_predict import _time
from ._pool_proposals import proposal_query
from .errors import APIError, ValidationError

ACTION_KINDS = {"idle_reclaim", "defragment", "drain_window", "wait_tuning"}
ACTION_LEVELS = {"off", "recommend", "approve", "auto"}


def action_policy(kind: str, level: str, window_cron: str, parallelism_cap: int) -> dict[str, Any]:
    if not isinstance(kind, str) or kind not in ACTION_KINDS or not isinstance(level, str) or level not in ACTION_LEVELS:
        raise ValidationError("Use a supported action kind and level")
    if type(parallelism_cap) is not int or not 1 <= parallelism_cap <= 32:
        raise ValidationError("Action parallelism_cap must be an integer from 1 to 32")
    valid = isinstance(window_cron, str) and len(window_cron) <= 256
    parts = window_cron.split(" ") if valid else []
    if len(parts) != 5 or parts[2:4] != ["*", "*"]:
        raise ValidationError("Use a five-field weekly UTC window with day-of-month and month set to *")
    for part, maximum in zip((parts[0], parts[1], parts[4]), (59, 23, 6)):
        if part == "*":
            continue
        seen: set[int] = set()
        for item in part.split(","):
            if not re.fullmatch(r"(?:0|[1-9][0-9]?)(?:-(?:0|[1-9][0-9]?))?", item):
                raise ValidationError("UTC windows accept integer lists and ranges, without steps or names")
            bounds = [int(n) for n in item.split("-")]
            lo, hi = bounds[0], bounds[-1]
            selected = set(range(lo, hi + 1))
            if hi < lo or hi > maximum or seen & selected:
                raise ValidationError("UTC window values must be in range without duplicates")
            seen |= selected
    return {"kind": kind, "level": level, "window_cron": window_cron, "parallelism_cap": parallelism_cap}


def shadow_query(limit: int | None, cursor: str | None, kind: str | None) -> dict[str, Any]:
    out = proposal_query(limit, cursor, None)
    if kind is not None:
        if not isinstance(kind, str) or kind not in ACTION_KINDS:
            raise ValidationError("Use a supported shadow action kind")
        out["kind"] = kind
    return out


def _wire_policy(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise APIError("The API returned an invalid action policy")
    try:
        return action_policy(*(value.get(k) for k in ("kind", "level", "window_cron", "parallelism_cap")))
    except ValidationError as exc:
        raise APIError("The API returned an invalid action policy") from exc


@dataclass(frozen=True)
class PoolActionPolicy:
    """One configured action level and its matching shadow qualification."""
    kind: str
    level: str
    window_cron: str
    parallelism_cap: int
    updated_at: str
    shadow_qualified: bool
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, value: Any) -> PoolActionPolicy:
        row = _wire_policy(value)
        _time(value.get("updated_at"))
        if type(value.get("shadow_qualified")) is not bool:
            raise APIError("The API returned invalid shadow qualification")
        row.update(updated_at=value["updated_at"], shadow_qualified=value["shadow_qualified"])
        return cls(**row, raw=row.copy())


@dataclass(frozen=True)
class PoolActionSettings:
    """Pool-wide kill switch and per-kind policies. Settings do not grant execution."""
    pool_id: str
    kill_switch: bool
    policies: list[PoolActionPolicy]
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, value: Any, pool_id: str) -> PoolActionSettings:
        if not isinstance(value, dict) or value.get("pool_id") != pool_id or type(value.get("kill_switch")) is not bool or not isinstance(value.get("policies"), list):
            raise APIError("The API returned invalid action settings")
        policies = [PoolActionPolicy.from_dict(v) for v in value["policies"]]
        if len(policies) != len(ACTION_KINDS) or {p.kind for p in policies} != ACTION_KINDS:
            raise APIError("The API returned incomplete or duplicate action policies")
        return cls(pool_id, value["kill_switch"], policies, {"pool_id": pool_id, "kill_switch": value["kill_switch"], "policies": [p.raw for p in policies]})


def policy_ack(value: Any, pool_id: str, expected: dict[str, Any]) -> PoolActionSettings:
    settings = PoolActionSettings.from_dict(value, pool_id)
    row = next(p for p in settings.policies if p.kind == expected["kind"])
    if any(row.raw[k] != v for k, v in expected.items()):
        raise APIError("The API did not confirm the requested action policy. Refresh before trying again")
    return settings


def kill_payload(enabled: bool) -> dict[str, bool]:
    if type(enabled) is not bool:
        raise ValidationError("The Act kill switch must be a boolean")
    return {"enabled": enabled}


def kill_ack(value: Any, pool_id: str, enabled: bool) -> PoolActionSettings:
    settings = PoolActionSettings.from_dict(value, pool_id)
    if settings.kill_switch != enabled:
        raise APIError("The API did not confirm the Act kill switch. Refresh before trying again")
    return settings


@dataclass(frozen=True)
class PoolShadowRun:
    """Trusted observed coverage and counterfactual counts, never measured savings."""
    id: str
    pool_id: str
    kind: str
    window_cron: str
    parallelism_cap: int
    cycle_start: str
    cycle_end: str
    state: str
    ended_at: str | None
    trusted_hours: int
    elapsed_hours: int
    gap_hours: int
    would_have_acted: int
    producer_available: bool
    qualifies_auto: bool
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, value: Any, pool_id: str) -> PoolShadowRun:
        def invalid():
            raise APIError("The API returned inconsistent shadow coverage")
        required = set(cls.__dataclass_fields__) - {"raw"}
        if not isinstance(value, dict) or not required <= value.keys() or value["pool_id"] != pool_id or not isinstance(value["id"], str) or not value["id"]:
            invalid()
        _wire_policy({**value, "level": "recommend"})
        start, end = _time(value["cycle_start"]), _time(value["cycle_end"])
        if end - start != timedelta(hours=168) or start.minute or start.second or start.microsecond:
            invalid()
        ended = _time(value["ended_at"]) if value["ended_at"] is not None else None
        if value["state"] not in {"running", "completed"} or (value["state"] == "completed") != (ended is not None) or (ended is not None and ended < end):
            invalid()
        for key in ("trusted_hours", "elapsed_hours", "gap_hours"):
            if type(value[key]) is not int or not 0 <= value[key] <= 168:
                invalid()
        if value["trusted_hours"] > value["elapsed_hours"] or value["gap_hours"] != value["elapsed_hours"] - value["trusted_hours"]:
            invalid()
        if type(value["would_have_acted"]) is not int or not 0 <= value["would_have_acted"] <= value["trusted_hours"] * 1000:
            invalid()
        if type(value["producer_available"]) is not bool or type(value["qualifies_auto"]) is not bool:
            invalid()
        if not value["producer_available"] and (value["trusted_hours"] or value["qualifies_auto"]):
            invalid()
        if value["qualifies_auto"] and (value["state"] != "completed" or value["trusted_hours"] != 168):
            invalid()
        row = {key: value[key] for key in required}
        return cls(**row, raw=row.copy())


@dataclass(frozen=True)
class PoolShadowRuns:
    """One bounded page of shadow runs with an opaque continuation cursor."""
    pool_id: str
    runs: list[PoolShadowRun]
    next_cursor: str | None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, value: Any, pool_id: str) -> PoolShadowRuns:
        if not isinstance(value, dict) or value.get("pool_id") != pool_id or not isinstance(value.get("runs"), list) or "next_cursor" not in value:
            raise APIError("The API returned an invalid shadow page")
        cursor = value["next_cursor"]
        if cursor is not None and (not isinstance(cursor, str) or not 1 <= len(cursor) <= 2048):
            raise APIError("The API returned an invalid shadow cursor")
        runs = [PoolShadowRun.from_dict(v, pool_id) for v in value["runs"]]
        if len(runs) > 100 or len({r.id for r in runs}) != len(runs) or (cursor is not None and not runs):
            raise APIError("The API returned an inconsistent shadow page")
        return cls(pool_id, runs, cursor, {"pool_id": pool_id, "runs": [r.raw for r in runs], "next_cursor": cursor})


def shadow_ack(value: Any, pool_id: str, expected: dict[str, Any]) -> PoolShadowRun:
    run = PoolShadowRun.from_dict(value, pool_id)
    if any(run.raw[k] != expected[k] for k in ("kind", "window_cron", "parallelism_cap")):
        raise APIError("The API returned a shadow run for a different policy")
    return run
