"""The ``nodus`` command.

Reads the same environment as the SDK. Everything after ``--`` is the command
executed inside the workload, so shell quoting does not have to survive two
layers of parsing.
"""

from __future__ import annotations

import argparse
import json
import sys
import textwrap
from typing import Any

from . import Client, __version__
from . import _pricebook
from .errors import NodusError
from .types import ContinuityMode


def _fmt_workload(wl: Any) -> str:
    route = wl.route.sku if wl.route else "—"
    status = getattr(wl.status, "value", wl.status)
    return f"{wl.id}  {status:<13} {route:<28} ${wl.spend_usd:.2f}"


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
            interrupt_tolerance=args.interrupt_tolerance,
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
                print(f"{ev.seq:>5}  {ev.type}")
        else:
            for ev in client.events(args.workload_id):
                print(f"{ev.seq:>5}  {ev.type}")
    return 0


def _cmd_artifacts(args: argparse.Namespace) -> int:
    # One line per manifest, then one per object it names. The endpoint returns
    # manifests, and a manifest names several objects, so flattening them into a
    # single line per row would have to pick one digest and drop the rest.
    with Client(base_url=args.base_url) as client:
        for art in client.artifacts(args.workload_id):
            mark = "final" if art.final else "checkpoint"
            print(f"{art.stage_id}  gen{art.generation}/seq{art.sequence}  {mark}  {art.manifest_id}")
            for name, out in sorted(art.outputs.items()):
                print(f"    output {name}  {out.sha256[:12]}  {out.bytes}B")
            for f in art.files:
                print(f"    file   {f.uri}  {f.sha256[:12]}  {f.bytes}B")
    return 0


def _cmd_cancel(args: argparse.Namespace) -> int:
    with Client(base_url=args.base_url) as client:
        client.cancel(args.workload_id)
    print(f"cancel requested for {args.workload_id}")
    return 0


def _cmd_ledger(args: argparse.Namespace) -> int:
    with Client(base_url=args.base_url) as client:
        led = client.ledger(args.workload_id)
        if args.json:
            print(json.dumps(led.raw, indent=2, default=str))
            return 0
        for e in led.entries:
            side, amount = ("debit", e.debit_usd) if e.debit_usd else ("credit", e.credit_usd)
            print(f"  {e.entry_type:<18} {side:<7} ${amount:.6f}")
        st = led.settlement
        if st:
            print(f"  {'settlement':<18} {st.status}"
                  + (f"   ${st.total_usd:.6f}" if st.total_usd else ""))
    return 0


def _cmd_logs(args: argparse.Namespace) -> int:
    with Client(base_url=args.base_url) as client:
        out = client.logs(args.workload_id, stage=args.stage, generation=args.generation)
    if not out:
        print("no log recorded yet — the log is a committed artifact, so it appears"
              " once a checkpoint carrying it has been verified")
        return 1
    lines = out.splitlines()
    if args.tail and len(lines) > args.tail:
        lines = lines[-args.tail:]
    print("\n".join(lines))
    return 0


def _fmt_route(route: Any) -> list[str]:
    mem = (route.resources or {}).get("device_memory_gb") or route.memory_gb
    lines = [
        f"{'catalog SKU':<22} {route.sku}",
        f"{'fit':<22} {route.fit_class}"
        + (f"  ·  {mem:g} GB" if mem else "")
        + (f"  ·  {route.region}" if getattr(route, 'region', '') else ""),
        f"{'rate':<22} ${route.price_usd_hour:.4f}/h",
        f"{'expected hours':<22} {route.expected_hours:.2f}",
        f"{'expected cost':<22} ${route.expected_cost_usd:.2f}",
        f"{'remaining budget':<22} ${route.remaining_budget_usd:.2f}",
    ]
    return lines


def _cmd_explain(args: argparse.Namespace) -> int:
    """Why this route, and — with --baselines — what the same brief costs elsewhere.

    The two halves come from different places on purpose and the output says so:
    the route is read back from the control plane that made it, the comparison
    from a committed artifact of other people's published prices.
    """
    with Client(base_url=args.base_url) as client:
        wl = client.get(args.workload_id)
        if not wl.route:
            print(f"{wl.id} has no route yet (status {getattr(wl.status, 'value', wl.status)})")
            return 1
        route = wl.route
        print(f"workload  {wl.id}")
        print()
        for line in _fmt_route(route):
            print(f"  {line}")
        print()
        print("  expected cost is cost to completion: the run plus the recovery reserve,")
        print("  not rate x hours. It is the number the budget is checked against.")

        if not args.baselines:
            return 0

        # The declared brief, which is what the comparison has to be priced
        # against: comparing on the route's own hours would quietly hand us the
        # win, since those hours are already the speedup we are arguing for.
        req = ((wl.raw.get("payload") or {}).get("requirements")) or {}
        peak = float(req.get("peak_memory_gb") or req.get("device_memory_gb") or 0.0)
        hours = float(req.get("expected_runtime_hours") or 0.0)
        if not peak or not hours:
            print("\n  brief does not declare peak memory and runtime; no comparison possible")
            return 0

        try:
            book = _pricebook.load(args.pricebook)
        except (_pricebook.PricebookMissing, ValueError) as exc:
            print(f"\n  no price book: {exc}")
            return 0

        quotes = _pricebook.quote(book, peak, hours)
        if not quotes:
            print("\n  no published rate in the book has enough memory for this brief")
            return 0

        print()
        print(f"  same brief on published list prices, as of {book.as_of}:")
        print()
        print(f"    {'seller':<12} {'picks':<10} {'$/h':>9} {'hours':>8} {'cost':>10}")
        for q in sorted(quotes, key=lambda x: x.cost_usd):
            print(f"    {q.seller:<12} {q.picks:<10} {q.rate_usd_hour:>9.4f} {q.hours:>8.2f} {q.cost_usd:>10.2f}")

        best = min(quotes, key=lambda x: x.cost_usd)
        ours = route.expected_cost_usd
        if ours > 0 and best.cost_usd > 0:
            delta = (best.cost_usd - ours) / best.cost_usd * 100.0
            print()
            print(f"    routed here  {route.sku}  ${ours:.2f}"
                  + (f"   {delta:.1f}% under {best.seller}" if delta > 0 else f"   {-delta:.1f}% over {best.seller}"))

        # The line that is not a percentage. A cheaper hourly rate losing on
        # cost to completion is the whole product claim, so it is called out
        # when it is true and silently skipped when it is not.
        cheaper = [q for q in quotes if q.rate_usd_hour < route.price_usd_hour and q.cost_usd > ours > 0]
        if cheaper:
            c = min(cheaper, key=lambda x: x.rate_usd_hour)
            print(f"    {c.seller} lists a lower hourly rate (${c.rate_usd_hour:.4f}/h) and a higher"
                  f" cost to finish (${c.cost_usd:.2f}).")

        print()
        print(f"  published prices and arithmetic on them, not a measurement.  nodus sources")
    return 0


def _cmd_quote(args: argparse.Namespace) -> int:
    """What a brief costs on published price lists. No control plane, no network."""
    try:
        book = _pricebook.load(args.pricebook)
    except (_pricebook.PricebookMissing, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    quotes = _pricebook.quote(book, args.peak_memory_gb, args.hours)
    if not quotes:
        print("no published rate in the book has enough memory for this brief")
        return 1

    print(f"brief    {args.peak_memory_gb:g} GB peak  ·  {args.hours:g} h declared")
    print()
    print(f"  {'seller':<12} {'picks':<10} {'$/h':>9} {'hours':>8} {'cost':>10} {'flips at':>10}")
    for q in sorted(quotes, key=lambda x: x.cost_usd):
        flip = f"{q.breakeven_speedup_x:.2f}x" if q.breakeven_speedup_x else "—"
        print(f"  {q.seller:<12} {q.picks:<10} {q.rate_usd_hour:>9.4f} {q.hours:>8.2f}"
              f" {q.cost_usd:>10.2f} {flip:>10}")

    span = book.breakeven_span
    if span and span.get("low_x") and span.get("high_x"):
        print()
        print(f"  These lists disagree about which accelerator is cheaper to finish on.")
        print(f"  The speedup where each flips runs {span['low_x']:.2f}x ({span.get('low_seller','')})"
              f" to {span['high_x']:.2f}x ({span.get('high_seller','')}).")
        print(f"  That span is one published price over another and assumes nothing of ours.")

    assumed = book.assumed_keys()
    if assumed:
        print()
        for a in assumed:
            print(f"  [assumed] {a.get('key')}  {a.get('speedup_x', 0):.4f}x — our judgement, not a measurement")

    print()
    print(f"  published list prices as of {book.as_of}, cost = rate x hours x performance factor.")
    print(f"  no control plane was consulted for these.  nodus sources")
    return 0


def _cmd_sources(args: argparse.Namespace) -> int:
    """Where every number in a quote came from. This is the footnote made checkable."""
    try:
        book = _pricebook.load(args.pricebook)
    except (_pricebook.PricebookMissing, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(f"price book  {book.source_path.name}  ·  as of {book.as_of}  ·  measured: {book.measured}")
    print(f"generated by {book.generated_by}")
    print()
    for r in sorted(book.rates, key=lambda x: (x.seller, x.fit_class, x.tier)):
        tier = "" if r.tier == "on_demand" else f"  [{r.tier}]"
        print(f"  {r.seller} {r.sku}  {r.fit_class} {r.device_memory_gb:g}GB{tier}")
        print(f"    ${r.usd_per_gpu_hour:.6f} per GPU-hour   ({r.derivation})")
        print(f"    {r.source_url}")
    if book.assumptions:
        print()
        print("  assumptions — judgements, not citations:")
        for a in book.assumptions:
            if str(a.get("basis")) != "assumed":
                continue
            print(f"    [{a.get('basis')}] {a.get('key')}  {a.get('value')}  ({a.get('speedup_x', 0):.4f}x)")
            note = str(a.get("note") or "").strip()
            if note and args.verbose:
                for line in textwrap.wrap(note, 74):
                    print(f"        {line}")
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
    r.add_argument("--interrupt-tolerance", default=None, choices=["low", "medium", "high"])
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

    x = sub.add_parser("explain", help="why this route, and what it beat")
    x.add_argument("workload_id")
    x.add_argument("--baselines", action="store_true", help="price the same brief on published lists")
    x.add_argument("--pricebook", default=None, help=f"path to the price book (or ${_pricebook.ENV_VAR})")

    q = sub.add_parser("quote", help="what a brief costs on published price lists")
    q.add_argument("--peak-memory-gb", type=float, required=True)
    q.add_argument("--hours", type=float, required=True, help="declared runtime")
    q.add_argument("--pricebook", default=None, help=f"path to the price book (or ${_pricebook.ENV_VAR})")

    s = sub.add_parser("sources", help="where every quoted number came from")
    s.add_argument("--pricebook", default=None, help=f"path to the price book (or ${_pricebook.ENV_VAR})")
    s.add_argument("-v", "--verbose", action="store_true", help="include assumption notes in full")
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
        "quote": lambda: _cmd_quote(args),
        "sources": lambda: _cmd_sources(args),
    }
    try:
        return handlers[args.cmd]()
    except NodusError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
