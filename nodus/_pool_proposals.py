"""Typed burst approval intent with immutable server-reported prices."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from ._pool_predict import _time
from .errors import APIError, ValidationError

PROPOSAL_STATES = {"pending", "approved", "rejected", "expired", "no_op", "applying", "applied"}


def proposal_query(limit: int | None, cursor: str | None, state: str | None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if limit is not None:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValidationError("Proposal limit must be an integer from 1 to 100")
        out["limit"] = limit
    if cursor is not None:
        if not isinstance(cursor, str) or not 1 <= len(cursor) <= 2048:
            raise ValidationError("Use the next_cursor returned by the proposal page")
        out["cursor"] = cursor
    if state is not None:
        if not isinstance(state, str) or state not in PROPOSAL_STATES:
            raise ValidationError("Use a supported proposal state")
        out["state"] = state
    return out


@dataclass(frozen=True)
class PoolProposal:
    """A retained burst intent. Only applied records an observed winning execution."""

    id: str
    pool_id: str
    kind: str
    workload_id: str
    stage_id: str
    envelope_version: int
    generation: int
    device_count: int
    expected_cost_micros: int
    approved_cost_micros: int | None
    state: str
    reason: str
    created_at: str
    expires_at: str
    decided_at: str | None
    applied_at: str | None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, value: Any, pool_id: str) -> PoolProposal:
        def invalid() -> None:
            raise APIError("The API returned an inconsistent pool proposal")
        if not isinstance(value, dict):
            invalid()
        required = set(cls.__dataclass_fields__) - {"raw"}
        if not required <= value.keys():
            invalid()
        for key in ("id", "pool_id", "kind", "workload_id", "stage_id", "state", "reason"):
            if not isinstance(value[key], str) or not value[key]:
                invalid()
        if value["pool_id"] != pool_id or value["kind"] != "burst" or value["state"] not in PROPOSAL_STATES:
            invalid()
        for key in ("envelope_version", "generation", "device_count", "expected_cost_micros"):
            if type(value[key]) is not int or not 1 <= value[key] <= 2**63 - 1:
                invalid()
        amount = value["approved_cost_micros"]
        if amount is not None and (type(amount) is not int or amount != value["expected_cost_micros"]):
            invalid()
        created, expires = _time(value["created_at"]), _time(value["expires_at"])
        if not created < expires <= created + timedelta(minutes=30):
            invalid()
        decided = _time(value["decided_at"]) if value["decided_at"] is not None else None
        applied = _time(value["applied_at"]) if value["applied_at"] is not None else None
        if decided is not None and not created <= decided < expires:
            invalid()
        state = value["state"]
        if state == "pending" and (decided is not None or amount is not None):
            invalid()
        if state == "rejected" and (decided is None or amount is not None):
            invalid()
        if state in {"approved", "applying", "applied"} and (decided is None or amount is None):
            invalid()
        if amount is not None and decided is None:
            invalid()
        if (state == "applied") != (applied is not None) or (applied is not None and (decided is None or applied < decided)):
            invalid()
        row = {key: value[key] for key in required}
        return cls(**row, raw=row.copy())


@dataclass(frozen=True)
class PoolProposals:
    """One page of retained proposal states and an opaque continuation cursor."""

    pool_id: str
    proposals: list[PoolProposal]
    next_cursor: str | None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, value: Any, pool_id: str) -> PoolProposals:
        if not isinstance(value, dict) or value.get("pool_id") != pool_id or not isinstance(value.get("proposals"), list) or "next_cursor" not in value:
            raise APIError("The API returned an invalid proposal page")
        cursor = value["next_cursor"]
        if cursor is not None and (not isinstance(cursor, str) or not 1 <= len(cursor) <= 2048):
            raise APIError("The API returned an invalid proposal cursor")
        proposals = [PoolProposal.from_dict(row, pool_id) for row in value["proposals"]]
        if len(proposals) > 100 or len({p.id for p in proposals}) != len(proposals) or (cursor is not None and not proposals):
            raise APIError("The API returned an inconsistent proposal page")
        return cls(pool_id, proposals, cursor, {"pool_id": pool_id, "proposals": [p.raw for p in proposals], "next_cursor": cursor})


def proposal_decision(value: Any, pool_id: str, proposal_id: str, approve: bool) -> PoolProposal:
    proposal = PoolProposal.from_dict(value, pool_id)
    expected = {"approved", "applying", "applied"} if approve else {"rejected"}
    if proposal.id != proposal_id or proposal.state not in expected:
        raise APIError("The API did not confirm that proposal decision. Refresh before trying again")
    return proposal
