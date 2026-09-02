"""Enums and result objects for the Nodus API.

Nothing here names a supplier. The customer surface reports a Nodus catalog
route and the capability class behind it; who ran the work, and on whose
hardware, is a Nodus decision and is not part of this contract.
"""

from __future__ import annotations

import math

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

__all__ = [
    "ComputeClass",
    "ContinuityMode",
    "InterruptTolerance",
    "WorkloadStatus",
    "TERMINAL",
    "TERMINAL_STATUSES",
    "MEDIA_TAR",
    "Route",
    "StageRun",
    "ManifestFile",
    "Artifact",
    "Event",
    "LedgerEntry",
    "Settlement",
    "Ledger",
    "Meter",
    "CUSTOMER_CHARGE",
]


class _WireEnum(str, Enum):
    """A string enum that survives an unknown value from a newer control plane.

    Wire vocabularies are open: the control plane may introduce a status before
    this SDK knows about it. Coercing an unknown value to a plain string keeps
    the client working instead of raising on a field it only meant to display.
    """

    @classmethod
    def coerce(cls, value: Any) -> Any:
        if value is None or isinstance(value, cls):
            return value
        try:
            return cls(str(value))
        except ValueError:
            return str(value)


class ComputeClass(_WireEnum):
    """The kind of infrastructure a route provides."""

    ACCELERATOR = "accelerator"
    VM = "vm"


class ContinuityMode(_WireEnum):
    """What should happen to progress if capacity is reclaimed mid-run."""

    #: Progress is checkpointed and a replacement resumes from the last
    #: committed manifest.
    CHECKPOINTED = "checkpointed"
    #: The work is safe to start over from the beginning.
    RESTARTABLE = "restartable"
    #: Losing the run is acceptable; do not pay for durability.
    EPHEMERAL = "ephemeral"


class InterruptTolerance(_WireEnum):
    """How much interruption risk an execution envelope carries.

    Not part of a brief: the control plane derives this from ``continuity`` and
    models no field a caller could set, so ``run()`` does not accept it. It is
    kept for reading a value the control plane reports.
    """

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class WorkloadStatus(_WireEnum):
    """Lifecycle, in order."""

    ACCEPTED = "accepted"
    PLANNING = "planning"
    RESERVING = "reserving"
    PROVISIONING = "provisioning"
    RUNNING = "running"
    RECOVERING = "recovering"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


#: The states at which a workload stops changing and ``wait()`` returns.
TERMINAL_STATUSES = frozenset(
    {WorkloadStatus.COMPLETED, WorkloadStatus.FAILED, WorkloadStatus.CANCELLED}
)

#: Wire values of the terminal states, for comparing against raw strings.
TERMINAL = frozenset(s.value for s in TERMINAL_STATUSES)


def _num(value: Any, default: float = 0.0) -> float:
    """A float from a field nothing here controls.

    A raw ``float()`` on wire data can raise mid-poll with an error no failure
    policy catches; NaN and infinity pass numeric checks yet compare false
    against every budget, so non-finite is refused along with unparseable.
    """
    if value is None or isinstance(value, bool):
        return default
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if math.isfinite(out) else default


def _int(value: Any, default: int = 0) -> int:
    """A count from the same untrusted place. See :func:`_num`."""
    if value is None or isinstance(value, bool):
        return default
    try:
        return int(_num(value, float(default)))
    except (TypeError, ValueError, OverflowError):
        return default


def _obj(value: Any) -> dict[str, Any]:
    """A mapping, or an empty one: a free-form field is not always an object."""
    return value if isinstance(value, dict) else {}


def _rows(value: Any) -> list[Any]:
    """A list, or an empty one: iterating a string yields its characters."""
    return value if isinstance(value, list) else []


def _dt(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


@dataclass
class Route:
    """The Nodus catalog route chosen for a workload.

    ``expected_cost_usd`` is cost to completion: the run plus the recovery the
    router expects to pay for on this route. That is why it can exceed
    ``price_usd_hour * expected_hours``, and it is the number the budget is
    checked against.
    """

    sku: str = ""
    compute_class: Any = None
    fit_class: str = ""
    region: str = ""
    memory_gb: float = 0.0
    price_usd_hour: float = 0.0
    expected_cost_usd: float = 0.0
    expected_hours: float = 0.0
    remaining_budget_usd: float = 0.0
    interruptible: bool = False
    resources: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> "Route | None":
        if not isinstance(d, dict) or not d:
            return None
        return cls(
            sku=d.get("offer_id") or d.get("sku") or "",
            compute_class=ComputeClass.coerce(d.get("compute_class")),
            fit_class=d.get("fit_class") or "",
            region=d.get("region") or "",
            memory_gb=_num(d.get("memory_gb")),
            price_usd_hour=_num(d.get("price_usd_hour")),
            expected_cost_usd=_num(d.get("expected_cost_usd")),
            expected_hours=_num(d.get("expected_hours")),
            remaining_budget_usd=_num(d.get("remaining_budget_usd")),
            interruptible=bool(d.get("interruptible")),
            resources=_obj(d.get("resources")),
            raw=d,
        )


@dataclass
class StageRun:
    """Progress for one stage of a multi-stage workload."""

    id: str = ""
    status: Any = None
    continuity_mode: Any = None
    completed_units: int = 0
    total_units: int = 0
    latest_manifest: dict[str, Any] | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "StageRun":
        d = _obj(d)
        return cls(
            id=d.get("id") or "",
            status=WorkloadStatus.coerce(d.get("status")),
            continuity_mode=ContinuityMode.coerce(d.get("continuity_mode")),
            completed_units=_int(d.get("completed_units")),
            total_units=_int(d.get("total_units")),
            latest_manifest=d.get("latest_manifest"),
            raw=d,
        )


#: An object holding a tar of a checkpoint subtree rather than a single file.
MEDIA_TAR = "application/x-tar"


@dataclass
class ManifestFile:
    """One object named by a committed manifest.

    ``uri`` is a key in Nodus-held storage, never a supplier URL: checkpoints
    outlive the capacity that produced them, which is the whole point of them
    being manifests. ``sha256`` is the digest the control plane recomputed
    before it agreed to write the manifest.
    """

    uri: str = ""
    sha256: str = ""
    bytes: int = 0
    #: Empty means one raw file, restored by copying it back.
    media: str = ""

    @property
    def is_tar(self) -> bool:
        """True when this object must be extracted rather than copied."""
        return self.media == MEDIA_TAR

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> "ManifestFile":
        d = _obj(d)
        return cls(
            uri=d.get("uri") or "",
            sha256=d.get("sha256") or "",
            bytes=_int(d.get("bytes")),
            media=d.get("media") or "",
        )


@dataclass
class Artifact:
    """One committed manifest: a checkpoint, or a stage's final outputs.

    This is the row shape ``GET /v1/workloads/{id}/artifacts`` actually returns.
    The endpoint lists *manifests*, not files — a manifest names many objects,
    which is why the digest and the bytes live on :class:`ManifestFile` under
    :attr:`files` and :attr:`outputs` rather than on the artifact itself.

    There is deliberately no ``verified`` flag. A manifest is written only after
    the control plane recomputes the SHA-256 of every object it names, so a row
    appearing here means those digests matched at commit time; but the response
    carries no per-row verification state, and an SDK that reported one would be
    asserting a check it never saw. Compare :attr:`ManifestFile.sha256` against
    bytes you have fetched if you need verification you performed yourself.

    :attr:`final` marks the manifest that completed the stage. Intermediate
    checkpoints exist to be restored from, not to be consumed downstream.
    """

    manifest_id: str = ""
    stage_id: str = ""
    generation: int = 0
    sequence: int = 0
    final: bool = False
    created_at: datetime | None = None
    #: Checkpoint objects, in the order the manifest names them.
    files: list[ManifestFile] = field(default_factory=list)
    #: Declared stage outputs by name — what a downstream stage reads.
    outputs: dict[str, ManifestFile] = field(default_factory=dict)
    #: The manifest body, for fields this SDK version does not model yet.
    manifest: dict[str, Any] = field(default_factory=dict, repr=False)
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    # An output is a file a manifest names, not a manifest, so the outputs and
    # logs endpoints are a separate type; neither is modelled here yet.

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Artifact":
        # stage_id, generation and sequence appear both on the row and inside
        # the manifest. Prefer the row: it is what the store indexed on, so it is
        # what a filter or a sort on this list would have used.
        d = _obj(d)
        man = _obj(d.get("manifest"))
        return cls(
            manifest_id=d.get("manifest_id") or "",
            stage_id=d.get("stage_id") or man.get("stage_id") or "",
            generation=_int(d.get("generation") or man.get("generation")),
            sequence=_int(d.get("sequence") or man.get("sequence")),
            final=bool(man.get("final")),
            created_at=_dt(d.get("created_at")),
            files=[ManifestFile.from_dict(f) for f in _rows(man.get("files"))],
            outputs={
                name: ManifestFile.from_dict(f)
                for name, f in _obj(man.get("outputs")).items()
            },
            manifest=man,
            raw=d,
        )


@dataclass
class Event:
    """One lifecycle event."""

    seq: int = 0
    id: str = ""
    type: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    created_at: datetime | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Event":
        d = _obj(d)
        return cls(
            seq=_int(d.get("id")),
            id=d.get("event_id") or "",
            type=d.get("event_type") or d.get("type") or "",
            payload=_obj(d.get("payload")),
            created_at=_dt(d.get("created_at")),
            raw=d,
        )


@dataclass
class LedgerEntry:
    id: str = ""
    entry_type: str = ""
    debit_usd: float = 0.0
    credit_usd: float = 0.0
    currency: str = "USD"
    evidence: dict[str, Any] = field(default_factory=dict)
    created_at: datetime | None = None

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "LedgerEntry":
        d = _obj(d)
        return cls(
            id=d.get("id") or "",
            entry_type=d.get("entry_type") or "",
            debit_usd=_num(d.get("debit_usd")),
            credit_usd=_num(d.get("credit_usd")),
            currency=d.get("currency") or "USD",
            evidence=_obj(d.get("evidence")),
            created_at=_dt(d.get("created_at")),
        )


#: The entry type that moves a customer's money. Everything else on a ledger is
#: bookkeeping between Nodus and the run.
CUSTOMER_CHARGE = "customer_charge"


@dataclass
class Settlement:
    """Whether the books for one workload are closed, and what is left on them.

    ``balance_usd`` is a residual, not a price: a settlement that closed
    cleanly balances at $0.00. What the run cost is :attr:`Ledger.charged_usd`.
    """

    status: str = "none"
    balance_usd: float = 0.0
    correlation_id: str = ""
    closed_at: datetime | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> "Settlement":
        d = _obj(d)
        return cls(
            status=d.get("status") or "none",
            balance_usd=_num(d.get("balance_usd")),
            correlation_id=d.get("correlation_id") or "",
            closed_at=_dt(d.get("closed_at")),
            raw=d,
        )


@dataclass
class Ledger:
    """Billing evidence for one workload.

    Entries are scrubbed of supply-plane detail before they reach this surface.
    """

    entries: list[LedgerEntry] = field(default_factory=list)
    settlement: Settlement = field(default_factory=Settlement)
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def charged_usd(self) -> float:
        """What the customer was charged for this workload.

        The sum of the customer_charge credits — the same arithmetic the
        control plane projects ``spend_usd`` from, so the two reconcile.
        """
        return sum(e.credit_usd for e in self.entries if e.entry_type == CUSTOMER_CHARGE)

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> "Ledger":
        d = _obj(d)
        return cls(
            entries=[LedgerEntry.from_dict(e) for e in _rows(d.get("entries"))],
            settlement=Settlement.from_dict(d.get("settlement")),
            raw=d,
        )


@dataclass
class Meter:
    """What a workload costs at one instant: what is settled plus what is accruing.

    A charge is booked when a lease closes, so ``settled_usd`` does not move
    while the work runs; ``total_now_usd`` is what answers "what is this
    costing me right now". ``as_of`` is part of that number — a live figure
    without the instant it was true cannot be read — and
    ``accruing_rate_usd_hour`` is what ticks it forward between polls.
    """

    settled_usd: float = 0.0
    accruing_usd: float = 0.0
    accruing_rate_usd_hour: float = 0.0
    total_now_usd: float = 0.0
    as_of: datetime | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> "Meter | None":
        if not isinstance(d, dict) or not d:
            return None
        return cls(
            settled_usd=_num(d.get("settled_usd")),
            accruing_usd=_num(d.get("accruing_usd")),
            accruing_rate_usd_hour=_num(d.get("accruing_rate_usd_hour")),
            total_now_usd=_num(d.get("total_now_usd")),
            as_of=_dt(d.get("as_of")),
            raw=d,
        )
