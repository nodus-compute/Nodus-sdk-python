"""Saved-file freeze state without inferred storage charges."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ._pool_predict import _time
from .errors import APIError


@dataclass(frozen=True)
class WorkloadFreeze:
    """Retained checkpoint files and exact cleanup state, not process memory."""
    id: str
    workload_id: str
    stage_id: str
    generation: int
    state: str
    requested_at: str
    frozen_at: str | None
    retained_bytes: int
    storage_charge_micros: int | None
    storage_billing_status: str
    manifest_id: str | None = None
    reason: str | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, value: Any, workload_id: str) -> WorkloadFreeze:
        def invalid():
            raise APIError("The API returned an inconsistent workload freeze state")
        required = set(cls.__dataclass_fields__) - {"manifest_id", "reason", "raw"}
        if not isinstance(value, dict) or not required <= value.keys() or value.get("workload_id") != workload_id:
            invalid()
        for key in ("id", "workload_id", "stage_id"):
            if not isinstance(value[key], str) or not value[key]:
                invalid()
        if not isinstance(value["state"], str) or value["state"] not in {"requested", "releasing", "frozen", "resuming", "resumed", "failed"}:
            invalid()
        if type(value["generation"]) is not int or value["generation"] <= 0 or type(value["retained_bytes"]) is not int or not 0 <= value["retained_bytes"] <= 2**63 - 1:
            invalid()
        requested = _time(value["requested_at"])
        frozen = _time(value["frozen_at"]) if value["frozen_at"] is not None else None
        if frozen is not None and frozen < requested:
            invalid()
        if value["state"] in {"frozen", "resuming", "resumed"} and (frozen is None or value["retained_bytes"] <= 0 or not value.get("manifest_id")):
            invalid()
        if value["storage_billing_status"] != "retained_storage_not_separately_metered" or value["storage_charge_micros"] is not None:
            invalid()
        for key in ("manifest_id", "reason"):
            if key in value and (not isinstance(value[key], str) or not value[key]):
                invalid()
        row = {key: value[key] for key in required}
        row.update(manifest_id=value.get("manifest_id"), reason=value.get("reason"))
        return cls(**row, raw={k: v for k, v in row.items() if k not in {"manifest_id", "reason"} or v is not None})
