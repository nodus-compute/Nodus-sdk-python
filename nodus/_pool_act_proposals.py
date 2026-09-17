"""Retained Act intent and observed outcomes without internal execution authority."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ._pool_actions import ACTION_KINDS
from ._pool_predict import _time
from .errors import APIError

ACT_STATES = {"pending", "approved", "rejected", "expired", "no_op", "applying", "uncertain", "applied", "failed"}


@dataclass(frozen=True)
class PoolActOutcome:
    """Server measurement and customer reporting remain separate nullable amounts."""
    source: str
    result: str
    recorded_at: str
    measured_saving_micros: int | None
    reported_saving_micros: int | None
    measurement_basis: str | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, value: Any) -> PoolActOutcome:
        fields = set(cls.__dataclass_fields__) - {"raw", "measurement_basis"}
        if not isinstance(value, dict) or not fields <= value.keys() or not isinstance(value["source"], str) or not isinstance(value["result"], str) or value["source"] not in {"server_observed", "customer_reported"} or value["result"] not in {"applied", "no_op", "failed"}:
            raise APIError("The API returned an invalid Act outcome")
        _time(value["recorded_at"])
        for name in ("measured_saving_micros", "reported_saving_micros"):
            amount = value[name]
            if amount is not None and (type(amount) is not int or not 0 <= amount <= 2**63 - 1):
                raise APIError("The API returned an invalid Act saving")
        if value["source"] == "server_observed" and value["reported_saving_micros"] is not None or value["source"] == "customer_reported" and value["measured_saving_micros"] is not None:
            raise APIError("The API mixed measured and customer-reported outcomes")
        basis = value.get("measurement_basis")
        if basis is not None and basis != "observed_platform_fee_reduction_30m_v1" or value["measured_saving_micros"] is not None and basis is None:
            raise APIError("The API did not identify the observed platform-fee reduction basis")
        row = {k: value[k] for k in fields}
        row["measurement_basis"] = basis
        return cls(**row, raw=row.copy())


@dataclass(frozen=True)
class PoolActProposal:
    """An immutable action proposal. Approval alone is never application evidence."""
    id: str
    pool_id: str
    recommendation_id: str
    kind: str
    level: str
    recommendation: dict[str, Any]
    created_at: str
    expires_at: str
    state: str
    reason: str
    decided_at: str | None
    outcome: PoolActOutcome | None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, value: Any, pool_id: str) -> PoolActProposal:
        fields = set(cls.__dataclass_fields__) - {"raw"}
        if not isinstance(value, dict) or not fields <= value.keys() or value["pool_id"] != pool_id:
            raise APIError("The API returned an invalid Act proposal")
        for key in ("id", "pool_id", "recommendation_id"):
            if not isinstance(value[key], str) or not value[key]:
                raise APIError("The API returned an invalid Act identity")
        rec = value["recommendation"]
        if any(not isinstance(value[k], str) for k in ("kind", "level", "state")) or value["kind"] not in ACTION_KINDS or value["level"] not in {"approve", "auto"} or value["state"] not in ACT_STATES or not isinstance(value["reason"], str):
            raise APIError("The API returned an unsupported Act proposal")
        if not isinstance(rec, dict) or rec.get("kind") != value["kind"] or not isinstance(rec.get("evidence"), dict):
            raise APIError("The API returned inconsistent Act evidence")
        created, expires = _time(value["created_at"]), _time(value["expires_at"])
        if expires <= created:
            raise APIError("The API returned invalid Act expiry")
        decided = _time(value["decided_at"]) if value["decided_at"] is not None else None
        if decided is not None and decided < created:
            raise APIError("The API returned invalid Act decision time")
        if value["state"] == "pending" and decided is not None or value["state"] in {"approved", "rejected"} and decided is None:
            raise APIError("The API returned inconsistent Act decision state")
        outcome = None if value["outcome"] is None else PoolActOutcome.from_dict(value["outcome"])
        if value["state"] in {"applied", "failed"} and (outcome is None or outcome.source != "server_observed" or outcome.result != value["state"]):
            raise APIError("The API did not provide observed Act outcome evidence")
        if outcome is not None and (outcome.result != value["state"] or _time(outcome.recorded_at) < created):
            raise APIError("The API returned an inconsistent Act outcome")
        row = {k: value[k] for k in fields}
        row["outcome"] = outcome
        raw = {**row, "outcome": None if outcome is None else outcome.raw}
        return cls(**row, raw=raw)


@dataclass(frozen=True)
class PoolActProposals:
    """A scoped page of action intent and retained observed results."""
    pool_id: str
    proposals: list[PoolActProposal]
    next_cursor: str | None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, value: Any, pool_id: str) -> PoolActProposals:
        if not isinstance(value, dict) or value.get("pool_id") != pool_id or not isinstance(value.get("proposals"), list) or "next_cursor" not in value:
            raise APIError("The API returned an invalid Act inbox")
        cursor = value["next_cursor"]
        if cursor is not None and (not isinstance(cursor, str) or not 1 <= len(cursor) <= 2048):
            raise APIError("The API returned an invalid Act cursor")
        rows = [PoolActProposal.from_dict(v, pool_id) for v in value["proposals"]]
        if len(rows) > 100 or len({r.id for r in rows}) != len(rows) or (cursor is not None and not rows):
            raise APIError("The API returned an inconsistent Act inbox")
        return cls(pool_id, rows, cursor, {"pool_id": pool_id, "proposals": [r.raw for r in rows], "next_cursor": cursor})


def act_decision(value: Any, pool_id: str, proposal_id: str, approve: bool) -> PoolActProposal:
    row = PoolActProposal.from_dict(value, pool_id)
    allowed = {"approved", "applying", "uncertain", "applied"} if approve else {"rejected"}
    if row.id != proposal_id or row.state not in allowed:
        raise APIError("The API did not confirm that Act decision. Refresh before trying again")
    return row
