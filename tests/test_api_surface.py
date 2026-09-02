"""The shape of the public API, asserted rather than hoped for.

Two clients and two handles are four surfaces that have to say the same thing.
The async half used to be a subset of the sync one — no ``wait()``, no
``stream_events()``, no ``logs()``, no webhooks — and nothing failed, because the
test that claimed to check it called a single method. These assertions compare
the surfaces name by name and parameter by parameter, so a method added to one
half and forgotten on the other is a red suite rather than a discovery.
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
    "interrupt_tolerance",
    "finish_by",
    "data_regions",
    "env",
    "inputs",
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
