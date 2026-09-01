"""Brief construction: keyword arguments in, wire payload out.

The submission schema is nested (source / requirements / outcome / continuity /
stages) because those are different concerns with different lifetimes. Callers
should not have to assemble that by hand, so ``run()`` takes flat keyword
arguments and this module does the translation in one place — shared by the
sync client, the async client, and the CLI so all three send the same bytes.
"""

from __future__ import annotations

import warnings
from typing import Any

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
        stacklevel=3,
    )


def _as_command(command: list[str] | str | None) -> list[str]:
    if isinstance(command, str):
        return command.split()
    if command:
        return list(command)
    return []


def _enum_value(v: Any) -> Any:
    """Accept an enum member or its wire string interchangeably."""
    return getattr(v, "value", v)


def build_payload(
    *,
    source: str | None = None,
    image: str | None = None,
    command: list[str] | str | None = None,
    requirements: dict[str, Any] | None = None,
    model: str | None = None,
    compute_class: str | None = None,
    peak_memory_gb: float | None = None,
    expected_runtime_hours: float | None = None,
    budget: float | None = None,
    max_cost_usd: float | None = None,
    finish_by: str | None = None,
    continuity: Any = None,
    interrupt_tolerance: Any = None,
    data_regions: list[str] | None = None,
    inputs: list[dict[str, Any]] | dict[str, Any] | None = None,
    env: dict[str, str] | None = None,
    stages: list[dict[str, Any]] | None = None,
    framework: str | None = None,
    policy: dict[str, Any] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """Build the ``POST /v1/workloads`` body from a flat brief."""
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
    ceiling = max_cost_usd if max_cost_usd is not None else budget
    if ceiling is not None:
        outcome["max_cost_usd"] = float(ceiling)
    if finish_by:
        outcome["complete_by"] = finish_by

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
        payload["stages"] = [dict(s) for s in stages]
        for stage in payload["stages"]:
            named = (stage.get("source") or {}).get("image")
            if named:
                _warn_if_it_cannot_bootstrap(named)
    else:
        chosen = image or source or DEFAULT_IMAGE
        _warn_if_it_cannot_bootstrap(chosen)
        src: dict[str, Any] = {"image": chosen}
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

    payload.update(extra)
    return payload


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
