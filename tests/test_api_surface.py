"""The shape of the public API, asserted rather than hoped for.

Two clients and two handles are four surfaces that have to say the same thing.
These assertions compare them name by name and parameter by parameter, so a
method added to one half and forgotten on the other is a red suite rather than
a discovery -- which a test that calls one method of each cannot do.
"""

from __future__ import annotations

import inspect

import pytest

import nodus
from nodus import cli


# ``close``/``aclose`` and the context-manager protocol are the only places the
# two clients are allowed to differ: one is the async spelling of the other.
CLIENT_ALIASES = {"close": "aclose"}


def _own_public(cls: type) -> set[str]:
    """Public methods and properties this class defines itself."""
    return {
        name
        for name, value in vars(cls).items()
        if not name.startswith("_") and (callable(value) or isinstance(value, property))
    }


def _params(fn: object) -> list[str]:
    return [p.name for p in inspect.signature(fn).parameters.values() if p.name != "self"]


def test_the_async_client_offers_everything_the_sync_one_does():
    sync = {CLIENT_ALIASES.get(n, n) for n in _own_public(nodus.Client)}
    asyn = _own_public(nodus.AsyncClient)
    assert sorted(sync - asyn) == [], "AsyncClient is missing these"
    assert sorted(asyn - sync) == [], "Client is missing these"


def test_the_two_clients_take_the_same_arguments_for_the_same_call():
    for name in sorted(_own_public(nodus.Client) - set(CLIENT_ALIASES)):
        sync = getattr(nodus.Client, name)
        asyn = getattr(nodus.AsyncClient, name)
        # A property is a name, not a call. Both halves still have to agree on
        # which of the two it is.
        if isinstance(sync, property) or isinstance(asyn, property):
            assert isinstance(sync, property) and isinstance(asyn, property), (
                f"{name} is a property on one client and a method on the other"
            )
            continue
        assert _params(sync) == _params(asyn), f"Client.{name} and AsyncClient.{name} disagree"


def test_the_two_workload_handles_offer_the_same_methods():
    assert _own_public(nodus.Workload) == _own_public(nodus.AsyncWorkload)
    for name in sorted(_own_public(nodus.Workload)):
        sync = getattr(nodus.Workload, name)
        asyn = getattr(nodus.AsyncWorkload, name)
        assert _params(sync) == _params(asyn), f"Workload.{name} and AsyncWorkload.{name} disagree"


# The brief a customer writes, spelled out on ``run()`` itself. Reading these
# names out of a private module is not an API.
BRIEF_PARAMETERS = {
    "command",
    "image",
    "model",
    "peak_memory_gb",
    "expected_runtime_hours",
    "budget",
    "compute_class",
    "continuity",
    "finish_by",
    "data_regions",
    "stages",
    "framework",
    "policy",
    "requirements",
    "idempotency_key",
    "extra",
}


@pytest.mark.parametrize("client", [nodus.Client, nodus.AsyncClient])
def test_run_names_the_brief_it_accepts(client):
    named = set(_params(client.run))
    assert BRIEF_PARAMETERS <= named, f"{client.__name__}.run hides {BRIEF_PARAMETERS - named}"


def test_the_package_ships_its_types():
    """``py.typed`` is what makes the annotations visible to an installed caller."""
    import pathlib

    assert (pathlib.Path(nodus.__file__).parent / "py.typed").is_file()


# -- the command ------------------------------------------------------------


def test_the_command_prints_only_ascii():
    """A console on a legacy code page cannot encode a typographic dash.

    Only what reaches a screen matters: constants are checked, docstrings are
    exempt because nothing prints them.
    """
    import ast
    import pathlib

    tree = ast.parse(pathlib.Path(cli.__file__).read_text(encoding="utf-8"))
    documented = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            first = node.body[0] if node.body else None
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                documented.add(id(first.value))

    offenders = [
        (node.lineno, node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in documented
        and not node.value.isascii()
    ]
    assert not offenders, f"non-ASCII text the command can print: {offenders}"


def _subcommands() -> set[str]:
    parser = cli.build_parser()
    actions = [a for a in parser._actions if hasattr(a, "choices") and a.choices]
    return set(actions[0].choices)


def test_the_price_book_subcommands_are_gone():
    """They resolved a path inside the monorepo, so they were dead once installed."""
    assert "quote" not in _subcommands()
    assert "sources" not in _subcommands()


def test_explain_survives_and_no_longer_takes_baselines():
    assert "explain" in _subcommands()
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["explain", "wl_abc", "--baselines"])
    args = cli.build_parser().parse_args(["explain", "wl_abc"])
    assert args.workload_id == "wl_abc"


def test_the_command_answers_a_missing_log_with_a_sentence(monkeypatch, capsys):
    """The server 404s until a manifest carries a log. That is a normal answer."""

    class _Fake:
        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return None

        def logs(self, workload_id, *, stage=None, generation=None):
            raise nodus.NotFoundError("no log recorded for this workload yet", status_code=404)

    monkeypatch.setattr(cli, "Client", _Fake)
    assert cli.main(["logs", "wl_abc"]) == 1
    out = capsys.readouterr()
    assert "committed artifact" in out.out
    assert "Traceback" not in out.err


def test_the_ledger_command_prints_what_the_run_charged(monkeypatch, capsys):
    """A healthy settlement balances to $0.00; the charge still reaches the screen."""
    from nodus.types import Ledger

    led = Ledger.from_dict(
        {
            "entries": [
                {"id": "led_1", "entry_type": "customer_charge", "credit_usd": 5.0},
                {"id": "led_2", "entry_type": "settle_close", "debit_usd": 5.0},
            ],
            "settlement": {"status": "closed", "balance_usd": 0.0},
        }
    )

    class _Fake:
        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return None

        def ledger(self, workload_id):
            return led

    monkeypatch.setattr(cli, "Client", _Fake)
    assert cli.main(["ledger", "wl_abc"]) == 0
    out = capsys.readouterr().out
    assert "5.00" in out, f"the charge is not on the screen: {out!r}"
    assert "closed" in out
    assert "0.00" in out, "a zero balance is a fact, not a reason to print nothing"


HOSTILE = "\x1b[2J\x1b]0;pwned\x07wl_abc\rerror: everything is fine"

#: A newline needs no escape sequence to lie. Every row these commands print
#: is one line, so a value carrying one forges a whole extra row that reads
#: exactly like a real one -- a charge that was never made, a run that does
#: not exist.
FORGING = "wl_ok\nwl_evil        completed     nodus:a100-80-us-east   $0.00"


def _has_control_characters(text: str) -> bool:
    return any(ch in text for ch in "\x1b\x07\r\x00")


def _row_printing_client(value: str):
    """A client whose every one-line field is ``value``."""
    from nodus.types import Artifact, Ledger

    class _Fake:
        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return None

        def _workload(self):
            wl = nodus.Workload.__new__(nodus.Workload)
            nodus._WorkloadState.__init__(wl)
            wl._client = self
            wl._absorb(
                {
                    "id": value,
                    "status": value,
                    "route": {
                        "offer_id": value,
                        "fit_class": value,
                        "region": value,
                        "price_usd_hour": 1.0,
                    },
                }
            )
            return wl

        def get(self, workload_id):
            return self._workload()

        def list(self, **kw):
            return [self._workload()]

        def events(self, workload_id, **kw):
            return [nodus.Event.from_dict({"id": 1, "event_type": value})]

        def cancel(self, workload_id):
            return None

        def artifacts(self, workload_id):
            return [
                Artifact.from_dict(
                    {
                        "stage_id": value,
                        "manifest_id": value,
                        "generation": 1,
                        "sequence": 1,
                        "outputs": {value: {"sha256": value, "bytes": 1}},
                        "files": [{"uri": value, "sha256": value, "bytes": 1}],
                    }
                )
            ]

        def ledger(self, workload_id):
            return Ledger.from_dict(
                {
                    "entries": [{"id": "led_1", "entry_type": value, "credit_usd": 5.0}],
                    "settlement": {"status": value, "balance_usd": 0.0},
                }
            )

    return _Fake


ROW_COMMANDS = [
    ["list"],
    ["get", "wl_abc"],
    ["events", "wl_abc"],
    ["artifacts", "wl_abc"],
    ["ledger", "wl_abc"],
    ["explain", "wl_abc"],
    ["cancel", "wl_abc"],
]


@pytest.mark.parametrize("argv", ROW_COMMANDS, ids=lambda a: a[0])
def test_a_server_value_cannot_add_a_row_to_any_listing(argv, monkeypatch, capsys):
    """One line per row, whatever the server called things.

    Counted against the same command on a clean value: a newline that survives
    anywhere shows up as a row the control plane never sent.
    """
    monkeypatch.setattr(cli, "Client", _row_printing_client("wl_ok"))
    cli.main(list(argv))
    clean = len(capsys.readouterr().out.splitlines())

    monkeypatch.setattr(cli, "Client", _row_printing_client(FORGING))
    cli.main(list(argv))
    forged = capsys.readouterr().out
    assert len(forged.splitlines()) == clean, f"{argv} gained a row: {forged!r}"
    assert not _has_control_characters(forged)


def test_text_from_the_server_cannot_repaint_the_terminal(monkeypatch, capsys):
    """Ids, statuses, routes and logs are written by something other than us,
    and a terminal obeys whatever escape sequences it is handed."""

    class _Fake:
        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return None

        def get(self, workload_id):
            wl = nodus.Workload.__new__(nodus.Workload)
            nodus._WorkloadState.__init__(wl)
            wl._client = self
            wl._absorb(
                {
                    "id": HOSTILE,
                    "status": HOSTILE,
                    "route": {"offer_id": HOSTILE, "fit_class": HOSTILE, "region": HOSTILE},
                }
            )
            return wl

        def events(self, workload_id, **kw):
            return [nodus.Event.from_dict({"id": 1, "event_type": HOSTILE})]

        def logs(self, workload_id, *, stage=None, generation=None):
            return f"step-1\n{HOSTILE}\nstep-2"

    monkeypatch.setattr(cli, "Client", _Fake)
    for argv in (["get", "wl_abc"], ["events", "wl_abc"], ["logs", "wl_abc"],
                 ["explain", "wl_abc"]):
        cli.main(argv)
        out = capsys.readouterr().out
        assert not _has_control_characters(out), f"{argv} passed an escape through: {out!r}"


def test_an_error_message_from_the_server_is_cleaned_too(monkeypatch, capsys):
    class _Fake:
        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return None

        def get(self, workload_id):
            raise nodus.NotFoundError(f"no such workload {HOSTILE}", status_code=404)

    monkeypatch.setattr(cli, "Client", _Fake)
    assert cli.main(["get", "wl_abc"]) == 2
    assert not _has_control_characters(capsys.readouterr().err)


def test_explain_still_reads_the_route_out_of_the_control_plane(monkeypatch, capsys):
    """The half that was never the price book: why this route, from the plane that chose it."""

    class _Fake:
        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return None

        def get(self, workload_id):
            wl = nodus.Workload.__new__(nodus.Workload)
            nodus._WorkloadState.__init__(wl)
            wl._client = self
            wl._absorb(
                {
                    "id": workload_id,
                    "status": "running",
                    "route": {
                        "offer_id": "nodus:a100-80-us-east",
                        "fit_class": "a100-80",
                        "price_usd_hour": 2.5,
                        "expected_hours": 18.0,
                        "expected_cost_usd": 300.0,
                    },
                }
            )
            return wl

    monkeypatch.setattr(cli, "Client", _Fake)
    assert cli.main(["explain", "wl_abc"]) == 0
    out = capsys.readouterr().out
    assert "nodus:a100-80-us-east" in out
    assert "cost to completion" in out
