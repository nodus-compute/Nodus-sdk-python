"""Brief construction: keyword arguments in, wire payload out.

The submission schema is nested (source / requirements / outcome / continuity /
stages) because those are different concerns with different lifetimes. Callers
should not have to assemble that by hand, so ``run()`` takes flat keyword
arguments and this module does the translation in one place — shared by the
sync client, the async client, and the CLI so all three send the same bytes.
"""

from __future__ import annotations

import difflib
import inspect
import os
import shlex
import warnings
from datetime import datetime, timezone
from typing import Any

_PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))


def _caller_stacklevel() -> int:
    """How far up the stack the code that wrote the brief is.

    Counted rather than written down, because there is no one right number:
    ``client.run()`` and ``build_payload()`` are different depths, and every
    helper added between the brief and the warning moves them both. A warning
    reported against the SDK's own source is worse than a wrong file name --
    the default filter shows one warning per location, so the first uncapped
    submission in a process is told and every one after it is silent.
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
    rather than guessed at — and this warns rather than refuses, because the
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


def _reject_unknown(unknown: dict[str, Any], known: tuple[str, ...]) -> None:
    """Refuse a keyword this SDK does not model, naming what it looked like.

    The control plane ignores fields it does not know, so a forwarded typo is
    accepted and runs: ``budget_usd=400`` submits a workload with no cost
    ceiling at all and answers 202.
    """
    if not unknown:
        return
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
    source: str | None = None,
    image: str | None = None,
    command: list[str] | str | None = None,
    requirements: dict[str, Any] | None = None,
    model: str | None = None,
    compute_class: Any = None,
    peak_memory_gb: float | None = None,
    expected_runtime_hours: float | None = None,
    budget: float | None = None,
    finish_by: datetime | str | None = None,
    continuity: Any = None,
    interrupt_tolerance: Any = None,
    data_regions: list[str] | None = None,
    inputs: list[dict[str, Any]] | dict[str, Any] | None = None,
    env: dict[str, str] | None = None,
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
    req: dict[str, Any] = dict(requirements or {})
    if model is not None:
        req.setdefault("model", model)
    if compute_class is not None:
        req.setdefault("compute_class", _enum_value(compute_class))
    if peak_memory_gb is not None:
        req.setdefault("peak_memory_gb", peak_memory_gb)
    if expected_runtime_hours is not None:
        req.setdefault("expected_runtime_hours", expected_runtime_hours)
    if interrupt_tolerance is not None:
        req.setdefault("interrupt_tolerance", _enum_value(interrupt_tolerance))
    if data_regions:
        req.setdefault("data_regions", list(data_regions))

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
        cont = dict(continuity)
        cont["mode"] = _enum_value(cont.get("mode", "checkpointed"))
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
        # to go. Silently dropping it submitted a pipeline that ran something
        # other than what the caller wrote down.
        discarded = sorted(
            name
            for name, value in (
                ("image", image), ("command", command), ("env", env), ("source", source)
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
        src: dict[str, Any] = {"image": image or source or DEFAULT_IMAGE}
        cmd = _as_command(command)
        if cmd:
            src["command"] = cmd
        if env:
            src["env"] = dict(env)
        payload["source"] = src

    if inputs:
        payload["inputs"] = [inputs] if isinstance(inputs, dict) else list(inputs)
    if framework:
        payload["framework"] = framework
    if policy:
        payload["policy"] = dict(policy)

    _merge_extra(payload, extra)
    _warn_about_the_money(payload)
    return payload


def _merge_extra(payload: dict[str, Any], extra: dict[str, Any] | None) -> None:
    """Add fields this SDK version does not model. Never replace one it does.

    A dict.update let ``extra={"outcome": ...}`` overwrite the outcome object
    holding the cost ceiling, so a brief passing budget=400 submitted uncapped.
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
    """The free warnings, read off the payload as it will be sent.

    After the merge, not before: a warning drawn from a draft of the brief can
    describe a submission that never happened.
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


def status_filter(status: Any) -> str | None:
    """Normalise a status filter into the wire form.

    Accepts a member, a wire string, a list of either, or the presets
    ``"active"`` and ``"terminal"`` that the control plane expands server-side.
    """
    if status is None:
        return None
    if isinstance(status, (list, tuple, set, frozenset)):
        parts = [str(_enum_value(s)) for s in status if s is not None]
        return ",".join(parts) or None
    return str(_enum_value(status))
