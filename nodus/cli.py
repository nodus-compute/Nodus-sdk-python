"""The ``nodus`` command.

Run a workload file and inspect its progress with short, explicit commands.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import math
import os
from pathlib import Path
import re
import sys
import threading
import time
import uuid
import warnings
import webbrowser
from typing import Any

from . import Client, __version__, _is_header_safe, _redact, _resolve_base_url, config, login
from ._brief import STATUS_FILTERS
from .errors import NodusError, NotFoundError
from .types import _num
from ._workload_file import load_workload_file, write_workload_file

# Nearly everything printed here was written somewhere else, and a terminal
# acts on whatever escapes it is handed. The rule between the two cleaners:
# _safe is only for values that may legitimately span lines, and every value
# that is one line by definition goes through _safe_line. A newline in a
# one-line value is not formatting -- it forges a whole row of output that
# reads exactly like the tool's own. The --json dumps are the one exception:
# json.dumps escapes every control character itself.
#
# C0 and C1 controls, minus tab and newline. This keeps them because it also
# cleans a workload's own log, which is nothing but lines.
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")

# The same, sparing nothing: an id, a status, a SKU, a code, an address.
_CONTROL_LINE = re.compile(r"[\x00-\x1f\x7f-\x9f]")


def _positive_seconds(value: str) -> float:
    """Reject invalid observation settings before creating a workload."""
    try:
        seconds = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected a finite positive number of seconds") from exc
    if not math.isfinite(seconds) or seconds <= 0:
        raise argparse.ArgumentTypeError("expected a finite positive number of seconds")
    return seconds


def _safe(text: Any) -> str:
    """Many-line text from elsewhere, with what a terminal acts on removed."""
    return _CONTROL.sub("", str(text))


def _safe_line(text: Any) -> str:
    """A one-line value from elsewhere, with tab and newline gone too."""
    return _CONTROL_LINE.sub("", str(text))


def _fmt_workload(wl: Any) -> str:
    # cost_now_usd, not spend_usd and not the meter: settled charges do not move
    # while a lease is open, and the meter counts only this billing period.
    route = _safe_line(wl.route.sku) if wl.route else "-"
    status = _safe_line(getattr(wl.status, "value", wl.status))
    return f"{_safe_line(wl.id)}  {status:<13} {route:<28} ${wl.cost_now_usd:.2f}"


@contextmanager
def _wait_activity(workload_id: str):
    if not sys.stderr.isatty():
        yield
        return
    stopped = threading.Event()
    started = time.monotonic()
    label = _safe_line(workload_id)

    def render():
        frame = 0
        while True:
            elapsed = int(time.monotonic() - started)
            marker = "|/-\\"[frame % 4]
            print(f"\r{marker} Waiting for {label}  {elapsed}s elapsed", end="", file=sys.stderr, flush=True)
            if stopped.wait(0.2):
                return
            frame += 1

    worker = threading.Thread(target=render, name="nodus-wait", daemon=True)
    worker.start()
    try:
        yield
    finally:
        stopped.set()
        worker.join()
        print(file=sys.stderr)


@contextmanager
def _cancel_on_interrupt(client: Client, workload_id: str):
    try:
        yield
    except KeyboardInterrupt as interrupt:
        display_id = _safe_line(workload_id)
        print(f"\nRequesting cancellation for {display_id}...", file=sys.stderr)
        try:
            prior = getattr(interrupt, "_nodus_cancellation", None)
            if prior is not None and prior[0] == workload_id:
                if prior[1] is not None:
                    raise prior[1]
            else:
                client.cancel(workload_id)
        except (NodusError, KeyboardInterrupt) as exc:
            reason = "Interrupted again" if isinstance(exc, KeyboardInterrupt) else _safe_line(exc)
            print(
                f"Cancellation not confirmed for {display_id}: {reason}. "
                f"Run nodus cancel {display_id} against the same API deployment.",
                file=sys.stderr,
            )
        else:
            print(
                f"Cancellation requested for {display_id}. "
                "Nodus is stopping the workload and releasing its resources.",
                file=sys.stderr,
            )
        raise


def _cmd_init(args: argparse.Namespace) -> int:
    path = write_workload_file(args.file)
    print(f"Created {_safe_line(path)}. Edit it, then run nodus run.")
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    settings = load_workload_file(args.file)
    submission_key = settings.get("idempotency_key") or str(uuid.uuid4())
    settings["idempotency_key"] = submission_key
    with Client(base_url=args.base_url) as client:
        try:
            wl = client.run(**settings)
        except KeyboardInterrupt:
            print(
                "Submission outcome unknown. A workload may still be running. "
                "Before retrying, set the following top-level value in the same workload file: "
                f"idempotency_key = {json.dumps(submission_key)}. "
                "Retry with nodus submit, then cancel the returned workload if needed.",
                file=sys.stderr,
            )
            raise
        print(_safe_line(wl.id), flush=True)
        if args.cmd == "submit":
            return 0
        with _cancel_on_interrupt(client, wl.id), _wait_activity(wl.id):
            wl.wait(poll_seconds=args.poll, timeout_seconds=args.timeout)
        print(_fmt_workload(wl))
        return 0 if wl.succeeded else 1


def _cmd_upload(args: argparse.Namespace) -> int:
    path = Path(args.file)
    if not path.is_file():
        raise ValueError("Upload a file or archive that exists on this computer.")
    with Client(base_url=args.base_url) as client:
        asset = client.assets.upload(path)
    print(_safe_line(asset.id))
    return 0


def _cmd_assets(args: argparse.Namespace) -> int:
    with Client(base_url=args.base_url) as client:
        for asset in client.assets.list():
            print(f"{_safe_line(asset.id)}  {_safe_line(asset.state)}  {_safe_line(asset.name)}")
    return 0


def _cmd_download(args: argparse.Namespace) -> int:
    with Client(base_url=args.base_url) as client:
        paths = client.get(args.workload_id).download()
    if not paths:
        print("No outputs available yet.")
        return 1
    for path in paths:
        print(_safe_line(path))
    return 0


def _cmd_list(args: argparse.Namespace) -> int:
    with Client(base_url=args.base_url) as client:
        filters = {"scope": args.status} if args.status in ("mine", "team") else {"status": args.status}
        for wl in client.list(limit=args.limit, **filters):
            print(_fmt_workload(wl))
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    with Client(base_url=args.base_url) as client:
        if args.cmd == "wait":
            with _cancel_on_interrupt(client, args.workload_id), _wait_activity(args.workload_id):
                wl = client.get(args.workload_id)
                wl.wait(poll_seconds=args.poll, timeout_seconds=args.timeout)
        else:
            wl = client.get(args.workload_id)
        if args.json:
            # ensure_ascii (the default) escapes every control character, so
            # raw wire text cannot reach the terminal through a dump.
            print(json.dumps(wl.raw, indent=2, default=str))
        else:
            print(_fmt_workload(wl))
        return 0 if wl.succeeded or not wl.is_terminal else 1


def _cmd_events(args: argparse.Namespace) -> int:
    with Client(base_url=args.base_url) as client:
        if args.follow:
            with _cancel_on_interrupt(client, args.workload_id):
                for ev in client.stream_events(args.workload_id, poll_seconds=args.poll):
                    print(f"{ev.seq:>5}  {_safe_line(ev.type)}")
        else:
            for ev in client.events(args.workload_id):
                print(f"{ev.seq:>5}  {_safe_line(ev.type)}")
    return 0


def _cmd_artifacts(args: argparse.Namespace) -> int:
    # One line per manifest, then one per object it names. The endpoint returns
    # manifests, and a manifest names several objects, so flattening them into a
    # single line per row would have to pick one digest and drop the rest.
    with Client(base_url=args.base_url) as client:
        for art in client.artifacts(args.workload_id):
            mark = "final" if art.final else "checkpoint"
            print(f"{_safe_line(art.stage_id)}  gen{art.generation}/seq{art.sequence}"
                  f"  {mark}  {_safe_line(art.manifest_id)}")
            for name, out in sorted(art.outputs.items()):
                print(f"    output {_safe_line(name)}  {_safe_line(out.sha256[:12])}  {out.bytes}B")
            for f in art.files:
                print(f"    file   {_safe_line(f.uri)}  {_safe_line(f.sha256[:12])}  {f.bytes}B")
    return 0


def _cmd_cancel(args: argparse.Namespace) -> int:
    with Client(base_url=args.base_url) as client:
        client.cancel(args.workload_id)
    print(f"cancel requested for {_safe_line(args.workload_id)}")
    return 0


def _cmd_ledger(args: argparse.Namespace) -> int:
    with Client(base_url=args.base_url) as client:
        led = client.ledger(args.workload_id)
        if args.json:
            print(json.dumps(led.raw, indent=2, default=str))
            return 0
        for e in led.entries:
            side, amount = ("debit", e.debit_usd) if e.debit_usd else ("credit", e.credit_usd)
            print(f"  {_safe_line(e.entry_type):<18} {side:<7} ${amount:.6f}")
        st = led.settlement
        # Both numbers, always: the charge is what the customer pays, the
        # balance is what closing left — exactly $0.00 when the books are square.
        print(f"  {'charged':<18} {'total':<7} ${led.charged_usd:.6f}")
        print(f"  {'settlement':<18} {_safe_line(st.status):<7} balance ${st.balance_usd:.6f}")
    return 0


_NO_LOG_YET = (
    "no log recorded yet: the log is a committed artifact, so it appears"
    " once Nodus has collected and verified it"
)


def _cmd_logs(args: argparse.Namespace) -> int:
    with Client(base_url=args.base_url) as client:
        try:
            out = client.logs(args.workload_id, stage=args.stage, generation=args.generation)
        except NotFoundError:
            # A missing workload also returns 404 on the log route.
            client.get(args.workload_id)
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
    # resources is a raw wire object: coerced, so a non-numeric value the
    # server chose cannot crash the {mem:g} format below.
    mem = _num((route.resources or {}).get("device_memory_gb")) or route.memory_gb
    lines = [
        f"{'catalog SKU':<22} {_safe_line(route.sku)}",
        f"{'fit':<22} {_safe_line(route.fit_class)}"
        + (f"  |  {mem:g} GB" if mem else "")
        + (f"  |  {_safe_line(route.region)}" if getattr(route, 'region', '') else ""),
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
            print(f"{_safe_line(wl.id)} has no route yet "
                  f"(status {_safe_line(getattr(wl.status, 'value', wl.status))})")
            return 1
        print(f"workload  {_safe_line(wl.id)}")
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
    makes it a different address, which is not the one anyone approved. The
    range is :data:`_CONTROL_LINE`'s -- C0, DEL and C1, tab and newline
    included -- because a URL is a one-line value like any other.
    """
    if _CONTROL_LINE.search(url):
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
        with _wait_activity("browser sign-in"):
            creds = login.poll_for_credentials(http, device, base_url)

    # The same predicate that gates sending a key gates keeping one: a stored
    # key the client cannot put in a header fails every later command, and by
    # then it has only ever been shown redacted. config.save_credentials
    # refuses such a key too; this refusal is the one that can say whose
    # fault it is.
    if not _is_header_safe(creds.api_key):
        which = (
            f"key {_safe_line(creds.key_id)}"
            if creds.key_id
            # No id to revoke by; how it was just minted is the next handle.
            else "the key -- the console lists it as the most recent for this device"
        )
        print(
            "The console sent an API key this client cannot use: a key must "
            "be printable ASCII with no spaces to travel in a request "
            f"header. Nothing was stored. Revoke {which} in the console and "
            "sign in again.",
            file=sys.stderr,
        )
        return 2

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
            # The key exists on the server whether or not this write worked,
            # and the write can fail for reasons that are not the key's --
            # a full disk, permissions, a foreign entry the dump refuses.
            # Showing it once is the only way it is not lost while still
            # live; repr stays copy-pasteable and cannot act on a terminal.
            print(
                f"Could not write {config.config_path()}: {_safe_line(exc)}. "
                f"Your key, shown in quotes that are not part of it: "
                f"{creds.api_key!r} - it will not be shown again. "
                "Store it, or revoke it in the console.",
                file=sys.stderr,
            )
            if isinstance(exc, Exception):
                return 2
            raise

    # The redaction arm cannot carry a control character once the gate above
    # has passed; wrapped anyway so both arms follow the one rule.
    who = _safe_line(creds.tenant) if creds.tenant else _safe_line(_redact(creds.api_key))
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


class _CommandHelpFormatter(argparse.RawDescriptionHelpFormatter):
    def _format_action(self, action):
        if isinstance(action, argparse._SubParsersAction):
            descriptions = {choice.dest: choice.help for choice in action._choices_actions}
            groups = (
                ("Setup", ("login", "logout", "init")),
                ("Run", ("run", "submit")),
                ("Monitor", ("list", "status", "wait", "logs", "cancel")),
                ("Results", ("download",)),
                ("Advanced", ("upload", "assets", "events", "artifacts", "ledger", "explain")),
            )
            return "\n".join(
                f"  {title}:\n" + "".join(
                    f"    {name:<10} {descriptions[name]}\n" for name in names
                ) for title, names in groups
            )
        return super()._format_action(action)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="nodus", description="Run GPU workloads from a workload file.",
        formatter_class=_CommandHelpFormatter,
        epilog="""Start with nodus init, edit nodus.toml, then nodus run.
Use nodus COMMAND --help for command options.""",
    )
    p.add_argument("--version", action="version", version=f"nodus {__version__}")
    p.add_argument("--base-url", default=None, help="override NODUS_BASE_URL")
    sub = p.add_subparsers(dest="cmd", required=True, metavar="COMMAND", title="commands")

    # SUPPRESS, not None: a subparser default is copied over the namespace the
    # top-level parser already filled, so `nodus --base-url X login` would lose
    # its address to the subcommand that also offers the flag.
    i = sub.add_parser("login", help="sign in and store an API key")
    i.add_argument("--base-url", default=argparse.SUPPRESS,
                   help="which deployment to sign in to")
    i.add_argument("--no-browser", action="store_true",
                   help="print the address instead of opening it")

    sub.add_parser("logout", help="delete the stored API key")

    i = sub.add_parser("init", help="create a starter workload file")
    i.add_argument("file", nargs="?", default="nodus.toml")

    for name, help_text in (
        ("run", "submit a workload file and wait for completion"),
        ("submit", "submit a workload file and return its ID"),
    ):
        r = sub.add_parser(name, help=help_text)
        r.add_argument("file", nargs="?", default="nodus.toml")
        if name == "run":
            r.add_argument("--timeout", type=_positive_seconds, default=None, help="observation timeout in seconds")
            r.add_argument("--poll", type=_positive_seconds, default=2.0)

    l = sub.add_parser("list", help="list workloads")
    l.add_argument("status", nargs="?", default=None, choices=(*STATUS_FILTERS, "mine", "team"))
    l.add_argument("--limit", type=int, default=50)

    for name, help_text in (
        ("status", "show workload status and cost"),
        ("wait", "wait for completion. Ctrl+C cancels the workload"),
    ):
        g = sub.add_parser(name, help=help_text)
        g.add_argument("workload_id")
        g.add_argument("--json", action="store_true")
        if name == "wait":
            g.add_argument("--timeout", type=_positive_seconds, default=None)
            g.add_argument("--poll", type=_positive_seconds, default=2.0)

    d = sub.add_parser("download", help="download workload outputs")
    d.add_argument("workload_id")

    u = sub.add_parser("upload", help="upload a data file or archive")
    u.add_argument("file")
    sub.add_parser("assets", help="list uploaded and imported data")

    e = sub.add_parser("events", help="lifecycle events")
    e.add_argument("workload_id")
    e.add_argument("--follow", action="store_true", help="follow events. Ctrl+C cancels the workload")
    e.add_argument("--poll", type=_positive_seconds, default=2.0)

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
    args = build_parser().parse_args(argv)

    handlers = {
        "login": lambda: _cmd_login(args),
        "logout": lambda: _cmd_logout(args),
        "init": lambda: _cmd_init(args),
        "run": lambda: _cmd_run(args),
        "submit": lambda: _cmd_run(args),
        "download": lambda: _cmd_download(args),
        "upload": lambda: _cmd_upload(args),
        "assets": lambda: _cmd_assets(args),
        "list": lambda: _cmd_list(args),
        "status": lambda: _cmd_status(args),
        "wait": lambda: _cmd_status(args),
        "events": lambda: _cmd_events(args),
        "artifacts": lambda: _cmd_artifacts(args),
        "cancel": lambda: _cmd_cancel(args),
        "ledger": lambda: _cmd_ledger(args),
        "logs": lambda: _cmd_logs(args),
        "explain": lambda: _cmd_explain(args),
    }
    try:
        return handlers[args.cmd]()
    except (NodusError, ValueError, TypeError, OSError) as exc:
        print(f"error: {_safe(exc)}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
