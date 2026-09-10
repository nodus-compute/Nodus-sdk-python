"""Validated workload preview results."""

from __future__ import annotations

import copy
import math
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from .errors import NodusError


_STAGE_ID = re.compile(r'[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}\Z')
_RFC3339 = re.compile(
    r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}'
    r'(?:\.[0-9]+)?(?:Z|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])'
)
_METRICS = ('execution_seconds', 'completion_seconds', 'compute_cost_usd')
_VERSIONS = ('estimator_version', 'catalog_version', 'profile_version', 'router_version',
             'provenance', 'customer_price_version')


def validate_stage_id(stage_id: Any) -> None:
    """Validate an optional stage selector before sending a preview."""
    if stage_id is not None and (not isinstance(stage_id, str) or not _STAGE_ID.fullmatch(stage_id)):
        raise ValueError('stage_id must be a stage ID of 1 to 64 letters, digits, underscores, dots or hyphens')


def _invalid() -> NodusError:
    return NodusError('Malformed estimate response from Nodus')


@dataclass(frozen=True)
class EstimateRange:
    """A finite, nonnegative server range in the enclosing field's units."""

    low: float
    high: float

    @classmethod
    def from_dict(cls, value: Any) -> EstimateRange:
        if not isinstance(value, dict) or not {'low', 'high'} <= value.keys():
            raise _invalid()
        low, high = value['low'], value['high']
        try:
            valid = all(type(number) in (int, float) and math.isfinite(number) for number in (low, high))
        except OverflowError:
            valid = False
        if not valid or low < 0 or high < low:
            raise _invalid()
        return cls(low=low, high=high)


@dataclass(frozen=True)
class EstimateDiagnostic:
    """A server explanation and suggested action for missing estimate evidence."""

    code: str
    message: str
    action: str

    @classmethod
    def from_dict(cls, value: Any) -> EstimateDiagnostic:
        if not isinstance(value, dict) or any(not isinstance(value.get(key), str)
                                              for key in ('code', 'message', 'action')):
            raise _invalid()
        return cls(**{key: value[key] for key in ('code', 'message', 'action')})


@dataclass(frozen=True)
class Estimate:
    """A workload or stage preview. Missing ranges remain None."""

    status: Literal['estimated', 'partial', 'unavailable']
    scope: Literal['workload', 'stage']
    stage_id: str | None
    execution_seconds: EstimateRange | None
    completion_seconds: EstimateRange | None
    compute_cost_usd: EstimateRange | None
    reasons: list[str]
    valid_until: datetime | None
    diagnostics: list[EstimateDiagnostic] = field(default_factory=list)
    stages: list[Estimate] = field(default_factory=list)
    estimator_version: str | None = None
    catalog_version: str | None = None
    profile_version: str | None = None
    router_version: str | None = None
    provenance: str | None = None
    customer_price_version: str | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_dict(cls, value: Any, *, _stage: bool = False) -> Estimate:
        if not isinstance(value, dict) or not {'status', 'reasons', 'valid_until', *_METRICS} <= value.keys():
            raise _invalid()
        status = value['status']
        scope = value.get('scope', 'workload')
        stage_id = value.get('stage_id')
        if status not in ('estimated', 'partial', 'unavailable') or scope not in ('workload', 'stage'):
            raise _invalid()
        if scope == 'stage':
            if stage_id is None:
                raise _invalid()
            try:
                validate_stage_id(stage_id)
            except ValueError:
                raise _invalid() from None
        elif 'stage_id' in value:
            raise _invalid()
        reasons = value['reasons']
        diagnostics = value.get('diagnostics', [])
        stage_rows = value.get('stages', [])
        if (not isinstance(reasons, list) or any(not isinstance(reason, str) for reason in reasons)
                or not isinstance(diagnostics, list) or not isinstance(stage_rows, list)
                or (_stage and (scope != 'stage' or stage_rows))
                or (scope == 'stage' and stage_rows)):
            raise _invalid()
        stages = [cls.from_dict(row, _stage=True) for row in stage_rows]
        if len({stage.stage_id for stage in stages}) != len(stages):
            raise _invalid()
        ranges = {key: None if value[key] is None else EstimateRange.from_dict(value[key]) for key in _METRICS}
        expiry = value['valid_until']
        if expiry is not None:
            if not isinstance(expiry, str) or not _RFC3339.fullmatch(expiry):
                raise _invalid()
            # Python 3.10 requires fractional seconds at millisecond or microsecond precision.
            normalized = re.sub(r'\.([0-9]+)', lambda match: '.' + match[1][:6].ljust(6, '0'), expiry)
            try:
                expiry = datetime.fromisoformat(normalized.replace('Z', '+00:00'))
            except ValueError:
                raise _invalid() from None
            if expiry.tzinfo is None:
                raise _invalid()
        if stages:
            if any(ranges[key] is not None and any(getattr(stage, key) is None for stage in stages)
                   for key in _METRICS):
                raise _invalid()
            if status == 'partial' and all(ranges.values()):
                raise _invalid()
        has_numbers = any(ranges.values())
        has_evidence = has_numbers or any(stage.status != 'unavailable' for stage in stages)
        if ((status == 'estimated' and not all(ranges.values()))
                or (status == 'unavailable' and has_evidence)
                or (status == 'partial' and not has_evidence)
                or (has_evidence and expiry is None)):
            raise _invalid()
        versions = {key: value.get(key) for key in _VERSIONS}
        if any(key in value and not isinstance(value[key], str) for key in _VERSIONS):
            raise _invalid()
        return cls(status=status, scope=scope, stage_id=stage_id, **ranges,
                   reasons=list(reasons), valid_until=expiry,
                   diagnostics=[EstimateDiagnostic.from_dict(row) for row in diagnostics],
                   stages=stages, raw=copy.deepcopy(value), **versions)
