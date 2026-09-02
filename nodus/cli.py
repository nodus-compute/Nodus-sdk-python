"""The ``nodus`` command.

Reads the same environment as the SDK. Everything after ``--`` is the command
executed inside the workload, so shell quoting does not have to survive two
layers of parsing.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import warnings
import webbrowser
from typing import Any

from . import Client, __version__, _redact, _resolve_base_url, config, login
from ._brief import STATUS_FILTERS
from .errors import NodusError, NotFoundError
from .types import ContinuityMode

# C0 and C1 controls, minus tab and newline. Nearly everything printed here was
# written somewhere else, and a terminal acts on whatever escapes it is handed.
# Tab and newline stay because this also cleans a workload's own log, and a log
# with its line breaks stripped is one long unreadable line.
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")

# For values that are one line by definition -- a code, an address, a tenant.
# There a newline is never formatting: it forges a whole extra line of output,
# and a fake "Enter it at: ..." is indistinguishable from the real one.
_CONTROL_LINE = re.compile(r"[\x00-\x1f\x7f-\x9f]")


def _safe(text: Any) -> str:
    """Text from elsewhere, with the characters a terminal acts on removed."""
    return _CONTROL.sub("", str(text))


def _safe_line(text: Any) -> str:
    """A single-line value from elsewhere, with tab and newline gone too."""
    return _CONTROL_LINE.sub("", str(text))


def _fmt_workload(wl: Any) -> str:
    # cost_now_usd, not spend_usd and not the meter: settled charges do not move
    # while a lease is open, and the meter counts only this billing period.
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


def _open_browser(url: str) -> bool:
    """Best effort, and only for a web address that needed no cleaning.

    The address arrives from the console, and ``webbrowser.open`` hands
    whatever it is given to the platform's handler: a ``file:`` or
    ``javascript:`` URL would be acted on locally. An address carrying a
    control character is not opened in a cleaned-up form either -- cleaning it
    makes it a different address, which is not the one anyone approved. Tab
    and newline count here even though :data:`_CONTROL` spares them; a URL is
    one line by definition.
    """
    if any(char < " " or char == "\x7f" for char in url):
        return False
    if not url.lower().startswith(("http://", "https://")):
        return False
    try:
        return bool(webbrowser.open(url))
    except Exception:
        return False


def _env_outranks(*names: str) -> list[str]:
    """Which of these are set, and so beat anything in the config file."""
    return [name for name in names if os.environ.get(name, "").strip()]


def _cmd_login(args: argparse.Namespace) -> int:
    base_url = _resolve_base_url(args.base_url)
    # Before anything is minted: the console issues the key inside the call
    # that releases it, so a file that cannot be written has to fail now.
    config.ensure_writable()
    with login.open_http(base_url) as http:
        device = login.start_device_authorization(http)
        print(f"Your sign-in code is {_safe_line(device.user_code)}")
        print()
        print(f"Enter it at: {_safe_line(device.verification_url)}")
        if not args.no_browser and _open_browser(device.verification_url):
            print("Opened that page in your browser.")
        print()
        print("Waiting for you to approve it...")
        creds = login.poll_for_credentials(http, device, base_url)

    # A caveat about the file belongs in the sentence a person is reading, not
    # in a UserWarning with a source line under it.
    with warnings.catch_warnings(record=True) as caveats:
        warnings.simplefilter("always")
        try:
            path = config.save_credentials(
                creds.api_key,
                creds.base_url,
                key_id=creds.key_id,
                tenant=creds.tenant,
                expires_at=creds.expires_at,
            )
        except BaseException as exc:
            # The key exists on the server whether or not this write worked.
            # Showing it once is the only way it is not lost while still live.
            print(
                f"Could not write {config.config_path()}: {exc}. Your key is: "
                f"{creds.api_key} - it will not be shown again. Store it, or "
                "revoke it in the console.",
                file=sys.stderr,
            )
            if isinstance(exc, Exception):
                return 2
            raise

    who = _safe_line(creds.tenant) if creds.tenant else _redact(creds.api_key)
    print(f"Signed in as {who}.")
    print(f"Wrote {path}")
    for caveat in caveats:
        print(f"Note: {_safe_line(caveat.message)}", file=sys.stderr)
    for name in _env_outranks("NODUS_API_KEY", "NODUS_BASE_URL"):
        print(
            f"Note: {name} is set in this environment and outranks the file, "
            "so it is what this client will use, not what was just written.",
            file=sys.stderr,
        )
    return 0


def _cmd_logout(args: argparse.Namespace) -> int:
    path = config.config_path()
    removed = config.clear_api_key()
    if removed is None:
        print(f"No stored key to remove: {path}")
    else:
        named = f" {_safe_line(removed['key_id'])}" if removed.get("key_id") else ""
        print(f"Removed the stored key{named} from {path}")
        print("That key still works until you revoke it in the console:")
        print("deleting the local copy does not revoke it.")
    for name in _env_outranks("NODUS_API_KEY"):
        print(
            f"Note: {name} is set in this environment and outranks the file, "
            "so this client is still signed in with that key. Unset it to "
            "finish logging out.",
            file=sys.stderr,
        )
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="nodus", description="Submit and observe Nodus workloads.")
    p.add_argument("--version", action="version", version=f"nodus {__version__}")
    p.add_argument("--base-url", default=None, help="override NODUS_BASE_URL")
    sub = p.add_subparsers(dest="cmd", required=True)

    # SUPPRESS, not None: a subparser default is copied over the namespace the
    # top-level parser already filled, so `nodus --base-url X login` would lose
    # its address to the subcommand that also offers the flag.
    i = sub.add_parser("login", help="sign in and store an API key")
    i.add_argument("--base-url", default=argparse.SUPPRESS,
                   help="which deployment to sign in to")
    i.add_argument("--no-browser", action="store_true",
                   help="print the address instead of opening it")

    sub.add_parser("logout", help="delete the stored API key")

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
    # Spelled out rather than free text: the control plane ignores a token it
    # does not know, so a typo here would list every workload on the account.
    l.add_argument("--status", default=None, choices=STATUS_FILTERS,
                   help="active, terminal, or a concrete status")

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
        "login": lambda: _cmd_login(args),
        "logout": lambda: _cmd_logout(args),
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
