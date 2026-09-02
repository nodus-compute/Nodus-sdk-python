"""The ``nodus`` command.

Reads the same environment as the SDK. Everything after ``--`` is the command
executed inside the workload, so shell quoting does not have to survive two
layers of parsing.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from typing import Any

from . import Client, __version__
from .errors import NodusError, NotFoundError
from .types import ContinuityMode

# C0 and C1 controls, minus tab and newline. Nearly everything printed here was
# written somewhere else, and a terminal acts on whatever escapes it is handed.
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


def _safe(text: Any) -> str:
    """Text from elsewhere, with the characters a terminal acts on removed."""
    return _CONTROL.sub("", str(text))


def _fmt_workload(wl: Any) -> str:
    # cost_now_usd, not spend_usd: a charge is booked when a lease closes, so a
    # list of running workloads would otherwise show $0.00 for all of them.
    route = _safe(wl.route.sku) if wl.route else "-"
    status = _safe(getattr(wl.status, "value", wl.status))
    return f"{_safe(wl.id)}  {status:<13} {route:<28} ${wl.cost_now_usd:.2f}"


def _split_command(argv: list[str]) -> tuple[list[str], list[str]]:
    """Split on the first bare ``--``."""
    if "--" in argv:
        i = argv.index("--")
        return argv[:i], argv[i + 1 :]
    return argv, []


def _cmd_run(args: argparse.Namespace, command: list[str]) -> int:
    with Client(base_url=args.base_url) as client:
        wl = client.run(
            model=args.model,
            image=args.image,
            command=command or None,
            peak_memory_gb=args.peak_memory_gb,
            expected_runtime_hours=args.hours,
            budget=args.budget,
            finish_by=args.finish_by,
            continuity=args.continuity,
            data_regions=args.data_region or None,
            idempotency_key=args.idempotency_key,
        )
        print(wl.id)
        if not args.wait:
            return 0
        wl.wait(poll_seconds=args.poll, timeout_seconds=args.timeout)
        print(_fmt_workload(wl))
        # Non-zero on a failed or cancelled workload so this composes in CI.
        return 0 if wl.succeeded else 1


def _cmd_list(args: argparse.Namespace) -> int:
    with Client(base_url=args.base_url) as client:
        for wl in client.list(limit=args.limit, status=args.status):
            print(_fmt_workload(wl))
    return 0


def _cmd_get(args: argparse.Namespace) -> int:
    with Client(base_url=args.base_url) as client:
        wl = client.get(args.workload_id)
        if args.wait:
            wl.wait(poll_seconds=args.poll, timeout_seconds=args.timeout)
        if args.json:
            print(json.dumps(wl.raw, indent=2, default=str))
        else:
            print(_fmt_workload(wl))
        return 0 if wl.succeeded or not wl.is_terminal else 1


def _cmd_events(args: argparse.Namespace) -> int:
    with Client(base_url=args.base_url) as client:
        if args.follow:
            for ev in client.stream_events(args.workload_id, poll_seconds=args.poll):
                print(f"{ev.seq:>5}  {_safe(ev.type)}")
        else:
            for ev in client.events(args.workload_id):
                print(f"{ev.seq:>5}  {_safe(ev.type)}")
    return 0


def _cmd_artifacts(args: argparse.Namespace) -> int:
    # One line per manifest, then one per object it names. The endpoint returns
    # manifests, and a manifest names several objects, so flattening them into a
    # single line per row would have to pick one digest and drop the rest.
    with Client(base_url=args.base_url) as client:
        for art in client.artifacts(args.workload_id):
            mark = "final" if art.final else "checkpoint"
            print(f"{_safe(art.stage_id)}  gen{art.generation}/seq{art.sequence}"
                  f"  {mark}  {_safe(art.manifest_id)}")
            for name, out in sorted(art.outputs.items()):
                print(f"    output {_safe(name)}  {_safe(out.sha256[:12])}  {out.bytes}B")
            for f in art.files:
                print(f"    file   {_safe(f.uri)}  {_safe(f.sha256[:12])}  {f.bytes}B")
    return 0


def _cmd_cancel(args: argparse.Namespace) -> int:
    with Client(base_url=args.base_url) as client:
        client.cancel(args.workload_id)
    print(f"cancel requested for {_safe(args.workload_id)}")
    return 0


def _cmd_ledger(args: argparse.Namespace) -> int:
    with Client(base_url=args.base_url) as client:
        led = client.ledger(args.workload_id)
        if args.json:
            print(json.dumps(led.raw, indent=2, default=str))
            return 0
        for e in led.entries:
            side, amount = ("debit", e.debit_usd) if e.debit_usd else ("credit", e.credit_usd)
            print(f"  {_safe(e.entry_type):<18} {side:<7} ${amount:.6f}")
        st = led.settlement
        # Both numbers, always: the charge is what the customer pays, the
        # balance is what closing left — exactly $0.00 when the books are square.
        print(f"  {'charged':<18} {'total':<7} ${led.charged_usd:.6f}")
        print(f"  {'settlement':<18} {_safe(st.status):<7} balance ${st.balance_usd:.6f}")
    return 0


_NO_LOG_YET = (
    "no log recorded yet: the log is a committed artifact, so it appears"
    " once a checkpoint carrying it has been verified"
)


def _cmd_logs(args: argparse.Namespace) -> int:
    with Client(base_url=args.base_url) as client:
        try:
            out = client.logs(args.workload_id, stage=args.stage, generation=args.generation)
        except NotFoundError:
            # The server 404s until a manifest carries a log. That is the normal
            # answer for a workload that has not checkpointed yet, not a fault.
            print(_NO_LOG_YET)
            return 1
    if not out:
        print(_NO_LOG_YET)
        return 1
    lines = _safe(out).splitlines()
    if args.tail and len(lines) > args.tail:
        lines = lines[-args.tail:]
    print("\n".join(lines))
    return 0


def _fmt_route(route: Any) -> list[str]:
    mem = (route.resources or {}).get("device_memory_gb") or route.memory_gb
    lines = [
        f"{'catalog SKU':<22} {_safe(route.sku)}",
        f"{'fit':<22} {_safe(route.fit_class)}"
        + (f"  |  {mem:g} GB" if mem else "")
        + (f"  |  {_safe(route.region)}" if getattr(route, 'region', '') else ""),
        f"{'rate':<22} ${route.price_usd_hour:.4f}/h",
        f"{'expected hours':<22} {route.expected_hours:.2f}",
        f"{'expected cost':<22} ${route.expected_cost_usd:.2f}",
        f"{'remaining budget':<22} ${route.remaining_budget_usd:.2f}",
    ]
    return lines


def _cmd_explain(args: argparse.Namespace) -> int:
    """Why this route: what was chosen, and the arithmetic it was chosen on.

    Read back from the control plane that made the decision, so what is printed
    is the routing that happened rather than a re-derivation of it.
    """
    with Client(base_url=args.base_url) as client:
        wl = client.get(args.workload_id)
        if not wl.route:
            print(f"{_safe(wl.id)} has no route yet "
                  f"(status {_safe(getattr(wl.status, 'value', wl.status))})")
            return 1
        print(f"workload  {_safe(wl.id)}")
        print()
        for line in _fmt_route(wl.route):
            print(f"  {line}")
        print()
        print("  expected cost is cost to completion: the run plus the recovery reserve,")
        print("  not rate x hours. It is the number the budget is checked against.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="nodus", description="Submit and observe Nodus workloads.")
    p.add_argument("--version", action="version", version=f"nodus {__version__}")
    p.add_argument("--base-url", default=None, help="override NODUS_BASE_URL")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="submit a brief")
    r.add_argument("--model", default=None, help="what the work is, e.g. '7B fine-tune'")
    r.add_argument("--image", default=None)
    r.add_argument("--peak-memory-gb", type=float, default=None)
    r.add_argument("--hours", type=float, default=None, help="expected runtime")
    r.add_argument("--budget", type=float, default=None, help="max cost to completion, USD")
    r.add_argument("--finish-by", default=None, help="RFC3339 deadline")
    r.add_argument(
        "--continuity",
        default=None,
        choices=[m.value for m in ContinuityMode],
    )
    r.add_argument("--data-region", action="append", default=None)
    r.add_argument("--idempotency-key", default=None)
    r.add_argument("--wait", action="store_true")
    r.add_argument("--timeout", type=float, default=None, help="seconds to wait")
    r.add_argument("--poll", type=float, default=2.0)

    l = sub.add_parser("list", help="list workloads")
    l.add_argument("--limit", type=int, default=50)
    l.add_argument("--status", default=None, help="active, terminal, or a concrete status")

    g = sub.add_parser("get", help="show one workload")
    g.add_argument("workload_id")
    g.add_argument("--wait", action="store_true")
    g.add_argument("--timeout", type=float, default=None)
    g.add_argument("--poll", type=float, default=2.0)
    g.add_argument("--json", action="store_true")

    e = sub.add_parser("events", help="lifecycle events")
    e.add_argument("workload_id")
    e.add_argument("--follow", action="store_true")
    e.add_argument("--poll", type=float, default=2.0)

    a = sub.add_parser("artifacts", help="verified manifests")
    a.add_argument("workload_id")

    c = sub.add_parser("cancel", help="request a safe stop")
    c.add_argument("workload_id")

    d = sub.add_parser("ledger", help="what the run settled")
    d.add_argument("workload_id")
    d.add_argument("--json", action="store_true")

    lg = sub.add_parser("logs", help="what the program printed")
    lg.add_argument("workload_id")
    lg.add_argument("--stage", default=None)
    lg.add_argument("--generation", type=int, default=None, help="which attempt, after a reclaim")
    lg.add_argument("--tail", type=int, default=0, help="last N lines only")

    x = sub.add_parser("explain", help="why this route")
    x.add_argument("workload_id")
    return p


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    argv, command = _split_command(argv)
    args = build_parser().parse_args(argv)

    handlers = {
        "run": lambda: _cmd_run(args, command),
        "list": lambda: _cmd_list(args),
        "get": lambda: _cmd_get(args),
        "events": lambda: _cmd_events(args),
        "artifacts": lambda: _cmd_artifacts(args),
        "cancel": lambda: _cmd_cancel(args),
        "ledger": lambda: _cmd_ledger(args),
        "logs": lambda: _cmd_logs(args),
        "explain": lambda: _cmd_explain(args),
    }
    try:
        return handlers[args.cmd]()
    except NodusError as exc:
        print(f"error: {_safe(exc)}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
