"""Brief construction: keyword arguments in, wire payload out.

The submission schema is nested (source / requirements / outcome / continuity /
stages) because those are different concerns with different lifetimes. Callers
should not have to assemble that by hand, so ``run()`` takes flat keyword
arguments and this module does the translation in one place, shared by the
sync client, the async client, and the CLI so all three send the same bytes.
"""

from __future__ import annotations

import difflib
import inspect
import os
import re
from pathlib import PurePosixPath
import shlex
import warnings
from datetime import datetime, timezone
from typing import Any

from .types import WorkloadStatus
from ._outputs import portable_output_name

_PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))


def _caller_stacklevel() -> int:
    """How far up the stack the code that wrote the brief is.

    Counted, not hardcoded: ``run()`` and ``build_payload()`` sit at different
    depths, and the default filter shows one warning per location, a warning
    blamed on SDK source silences every submission after the first.
    """
    frame = inspect.currentframe()
    frame = frame.f_back if frame is not None else None  # the warning's own frame
    level = 1
    while frame is not None:
        if os.path.dirname(os.path.abspath(frame.f_code.co_filename)) != _PACKAGE_DIR:
            return level
        frame = frame.f_back
        level += 1
    return level

# The runner installs itself onto the rented host by fetching its artifact with
# curl, then wget, then a stdlib python3. An image carrying none of the three
# cannot start the work, so it bills for a host that never runs anything.
BOOTSTRAP_FETCH_TOOLS = frozenset({"curl", "wget", "python3"})

# Which of those tools the stock image ships, measured 2026-08-29. An empty set
# means the image cannot bootstrap; an image absent from this table has not been
# measured and nothing is claimed about it.
IMAGE_FETCH_TOOLS: dict[str, frozenset[str]] = {
    "ubuntu:22.04": frozenset(),
    "python:3.11-slim": frozenset({"python3"}),
    "pytorch/pytorch:2.6.0-cuda12.4-cudnn9-runtime": frozenset({"python3"}),
}

DEFAULT_IMAGE = "python:3.11-slim"


def _warn_if_it_cannot_bootstrap(image: str) -> None:
    """Warn while the brief is still free, for images measured to ship no fetch tool.

    Only measured images are named. What an arbitrary tag contains is knowable
    from a registry, not from here, so an unrecognised image is left alone
    rather than guessed at, and this warns rather than refuses, because the
    tag may be a local rebuild that added one.
    """
    tools = IMAGE_FETCH_TOOLS.get(image)
    if tools is None or tools & BOOTSTRAP_FETCH_TOOLS:
        return
    warnings.warn(
        f"image {image!r} ships no curl, wget or python3, so the Nodus runner cannot "
        "install itself onto the host: the workload is billed without ever starting. "
        f"Use an image carrying one of them, such as the default {DEFAULT_IMAGE!r}.",
        stacklevel=_caller_stacklevel(),
    )


def _warn_if_it_is_uncapped(outcome: dict[str, Any]) -> None:
    """Warn while the brief is still free, for a submission with no cost ceiling.

    An omitted budget is not a small budget: the run is admitted against the
    account cap alone and bills whatever it takes to finish.
    """
    if "max_cost_usd" in outcome:
        return
    warnings.warn(
        "no budget= given, so this workload is capped only by the account spend "
        "cap and will bill whatever it costs to finish. Pass budget=<usd> to "
        "bound it.",
        stacklevel=_caller_stacklevel(),
    )


def _as_command(command: list[str] | str | None) -> list[str]:
    """Argv for the workload. A string is split the way a shell would split it."""
    if isinstance(command, str):
        return shlex.split(command)
    if command:
        return list(command)
    return []


def _as_timestamp(value: datetime | str | None) -> str | None:
    """RFC3339 for the wire. A naive datetime is read as local time, as Python does."""
    if not value:
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return str(value)


def _enum_value(v: Any) -> Any:
    """Accept an enum member or its wire string interchangeably."""
    return getattr(v, "value", v)


# Brief fields the control plane does not model, and what to reach for instead.
# Sending one costs a caller the constraint they believe they set.
UNSUPPORTED: dict[str, str] = {
    "interrupt_tolerance": (
        "the control plane does not model this yet: it derives the envelope's "
        "tolerance from continuity, so declaring 'low' here yields the opposite. "
        "Use continuity= to say what an interruption should cost you."
    ),
    "env": (
        "the control plane does not model this yet: a workload's source is an "
        "image and a command, so environment never reaches the host. Bake the "
        "values into the image, or pass them in the command."
    ),
}


def _reject_unknown(unknown: dict[str, Any], known: tuple[str, ...]) -> None:
    """Refuse a keyword this SDK does not model, naming what it looked like.

    The control plane ignores fields it does not know, so a forwarded typo is
    accepted and runs: ``budget_usd=400`` submits a workload with no cost
    ceiling at all and answers 202. :data:`UNSUPPORTED` names the fields that
    deserve a better refusal than "unknown".
    """
    if not unknown:
        return
    named = sorted(set(unknown) & set(UNSUPPORTED))
    if named:
        raise TypeError(
            "; ".join(f"{name}=: {UNSUPPORTED[name]}" for name in named)
        )
    parts = []
    for name in sorted(unknown):
        near = difflib.get_close_matches(name, known, n=1, cutoff=0.6)
        parts.append(f"{name!r}" + (f" (did you mean {near[0]!r}?)" if near else ""))
    raise TypeError(
        "unknown brief field: "
        + ", ".join(parts)
        + ". The control plane ignores fields it does not model, so this would "
        "have been submitted and silently dropped. Pass extra={...} to send a "
        "field deliberately."
    )


def build_payload(
    *,
    image: str | None = None,
    source_asset_id: str | None = None,
    inputs: list[dict[str, str]] | None = None,
    outputs: dict[str, str] | None = None,
    command: list[str] | str | None = None,
    requirements: dict[str, Any] | None = None,
    model: str | None = None,
    compute_class: Any = None,
    peak_memory_gb: float | None = None,
    expected_runtime_hours: float | None = None,
    budget: float | None = None,
    finish_by: datetime | str | None = None,
    continuity: Any = None,
    data_regions: list[str] | None = None,
    stages: list[dict[str, Any]] | None = None,
    framework: str | None = None,
    policy: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
    **unknown: Any,
) -> dict[str, Any]:
    """Build the ``POST /v1/workloads`` body from a flat brief.

    ``extra`` is merged last, for a field the control plane models and this SDK
    version does not. Anything else is refused rather than forwarded.
    """
    _reject_unknown(unknown, BRIEF_FIELDS)
    _validate_assets(source_asset_id, inputs)
    _validate_outputs(outputs)
    for stage in stages or []:
        if isinstance(stage, dict) and stage.get("outputs"):
            _validate_outputs(stage["outputs"])
            if not portable_output_name(stage.get("id")):
                raise ValueError("Stages with outputs must use portable file names as IDs.")
    if outputs is not None and (stages or framework):
        raise TypeError("outputs cannot be combined with stages or framework. Declare outputs on each stage.")
    req: dict[str, Any] = dict(requirements or {})
    if model is not None:
        req.setdefault("model", model)
    if compute_class is not None:
        req.setdefault("compute_class", _enum_value(compute_class))
    if peak_memory_gb is not None:
        req.setdefault("peak_memory_gb", peak_memory_gb)
    if expected_runtime_hours is not None:
        req.setdefault("expected_runtime_hours", expected_runtime_hours)

    # Data residency lives in policy: the envelope reads Policy.DataRegions, and
    # Requirements has no such field. An explicit policy= wins over the shortcut.
    pol: dict[str, Any] = dict(policy or {})
    if data_regions:
        pol.setdefault("data_regions", list(data_regions))

    outcome: dict[str, Any] = {}
    if budget is not None:
        outcome["max_cost_usd"] = float(budget)
    deadline = _as_timestamp(finish_by)
    if deadline:
        outcome["complete_by"] = deadline

    # Checkpointed by default: losing a long run to a reclaim is the expensive
    # failure, and the caller has to opt out of durability rather than into it.
    if continuity is None:
        cont: dict[str, Any] = {"mode": "checkpointed", "resume_on_interruption": True}
    elif isinstance(continuity, dict):
        # Same default as the string form: an absent flag reads as true on
        # arrival, handing "ephemeral" durability it declines. A written flag is kept.
        cont = dict(continuity)
        mode = _enum_value(cont.get("mode", "checkpointed"))
        cont["mode"] = mode
        cont.setdefault("resume_on_interruption", mode != "ephemeral")
    else:
        mode = _enum_value(continuity)
        cont = {"mode": mode, "resume_on_interruption": mode != "ephemeral"}

    payload: dict[str, Any] = {
        "requirements": req,
        "outcome": outcome,
        "continuity": cont,
    }

    if stages:
        # A staged brief has a source per stage, so a top-level one has nowhere
        # to go: dropping it would run something other than what the brief says.
        discarded = sorted(
            name
            for name, value in (
                ("image", image), ("command", command), ("source_asset_id", source_asset_id)
            )
            if value
        )
        if discarded:
            raise TypeError(
                "stages= replaces the top-level source, so "
                + ", ".join(f"{n}=" for n in discarded)
                + " would be dropped rather than run. Put them on the stage that "
                "needs them: stages=[{'id': ..., 'source': {'image': ..., "
                "'command': [...]}}]."
            )
        payload["stages"] = [dict(s) for s in stages]
    else:
        src: dict[str, Any] = {"image": image or DEFAULT_IMAGE}
        cmd = _as_command(command)
        if cmd:
            src["command"] = cmd
        if source_asset_id is not None:
            src["asset_id"] = source_asset_id
        if outputs is not None:
            payload["stages"] = [{"id": "main", "source": src, "outputs": dict(outputs)}]
        else:
            payload["source"] = src

    if inputs is not None:
        payload["inputs"] = [dict(value) for value in inputs]

    if framework:
        payload["framework"] = framework
    if pol:
        payload["policy"] = pol

    _merge_extra(payload, extra)
    _warn_about_the_money(payload)
    return payload


def _validate_assets(source_asset_id: str | None, inputs: list[dict[str, str]] | None) -> None:
    def valid_id(value: Any) -> bool:
        return isinstance(value, str) and re.fullmatch(r"asset_[A-Za-z0-9-]{1,64}", value) is not None
    if source_asset_id is not None and not valid_id(source_asset_id):
        raise ValueError("source_asset_id must be an asset ID returned by Nodus.")
    if inputs is None:
        return
    if not isinstance(inputs, list) or len(inputs) > 8:
        raise ValueError("inputs must be a list of at most eight named assets.")
    names: set[str] = set()
    for value in inputs:
        if not isinstance(value, dict) or set(value) != {"name", "asset_id"}:
            raise ValueError("Each input requires name and asset_id. Import or upload the data first.")
        name = value["name"]
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", name) or name in names:
            raise ValueError("Input names must be unique identifiers starting with a letter.")
        if not valid_id(value["asset_id"]):
            raise ValueError("Input asset_id must be an asset ID returned by Nodus.")
        names.add(name)


def _validate_outputs(outputs: dict[str, str] | None) -> None:
    if outputs is None:
        return
    if not isinstance(outputs, dict):
        raise TypeError("outputs must map output names to relative file paths.")
    if len({name.casefold() for name in outputs if isinstance(name, str)}) != len(outputs):
        raise ValueError("Output names must be distinct on case-insensitive filesystems.")
    for name, path in outputs.items():
        if not portable_output_name(name):
            raise ValueError("Output names must be portable file names using letters, digits, dots, underscores or hyphens.")
        if (not isinstance(path, str) or not path or "\\" in path or ":" in path
                or any(ord(c) < 32 for c in path) or PurePosixPath(path).is_absolute()
                or any(part in ("", ".", "..") for part in path.split("/"))):
            raise ValueError("Output paths must name files inside the workload working directory.")


def _merge_extra(payload: dict[str, Any], extra: dict[str, Any] | None) -> None:
    """Add fields this SDK version does not model. Never replace one it does.

    A key that collides with the built brief would overwrite it, ``outcome``
    included, which is where the cost ceiling lives.
    """
    if not extra:
        return
    clashes = sorted(set(extra) & set(payload))
    if clashes:
        raise TypeError(
            "extra= would replace "
            + ", ".join(repr(k) for k in clashes)
            + ", which this brief already built"
            + (", including the cost ceiling in 'outcome'" if "outcome" in clashes else "")
            + ". Pass the value through the keyword that builds it, or drop it "
            "from extra=; extra is for fields the control plane models and this "
            "SDK version does not."
        )
    payload.update(extra)


def _warn_about_the_money(payload: dict[str, Any]) -> None:
    """Money warnings, read off the payload as it will be sent.

    After the merge, not before: a warning drawn from a draft can describe a
    submission that never happens.
    """
    _warn_if_it_is_uncapped(payload.get("outcome") or {})
    source = payload.get("source") or {}
    if source.get("image"):
        _warn_if_it_cannot_bootstrap(source["image"])
    for stage in payload.get("stages") or []:
        named = (stage.get("source") or {}).get("image")
        if named:
            _warn_if_it_cannot_bootstrap(named)


#: The keywords a brief may name, read off the translator so the two cannot drift.
BRIEF_FIELDS: tuple[str, ...] = tuple(
    name
    for name, p in inspect.signature(build_payload).parameters.items()
    if p.kind is inspect.Parameter.KEYWORD_ONLY
)


#: The presets the control plane expands into concrete statuses server-side.
STATUS_PRESETS: tuple[str, ...] = ("active", "terminal")

#: Every token a status filter may name.
STATUS_FILTERS: tuple[str, ...] = tuple(
    sorted({s.value for s in WorkloadStatus}.union(STATUS_PRESETS))
)


def _one_status(value: Any) -> str:
    """One filter token, checked against the vocabulary the server expands.

    Unrecognised tokens are dropped on arrival, and a filter that expands to
    nothing lists the whole account.
    """
    wire = str(_enum_value(value)).strip()
    if wire in STATUS_FILTERS:
        return wire
    near = difflib.get_close_matches(wire, STATUS_FILTERS, n=1, cutoff=0.6)
    raise ValueError(
        f"{wire!r} is not a workload status"
        + (f" (did you mean {near[0]!r}?)" if near else "")
        + ". The control plane ignores a filter token it does not know, and a "
        "filter that matches nothing is no filter, so this would have listed "
        "every workload on the account. Statuses: " + ", ".join(STATUS_FILTERS) + "."
    )


def status_filter(status: Any) -> str | None:
    """Normalise a status filter into the wire form.

    Accepts a member, a wire string, a comma-joined string, a list of either,
    or the presets ``"active"`` and ``"terminal"``. A token the server could
    not expand is a ValueError here rather than a listing of everything.
    """
    if status is None:
        return None
    if isinstance(status, (list, tuple, set, frozenset)):
        tokens = [s for s in status if s is not None]
    elif isinstance(status, str):
        tokens = [t for t in status.split(",") if t.strip()]
    else:
        tokens = [status]
    return ",".join(_one_status(t) for t in tokens) or None
