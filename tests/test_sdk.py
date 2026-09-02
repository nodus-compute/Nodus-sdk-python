"""SDK tests against a stub control plane.

Every assertion here backs a statement made in the published docs, so a change
that silently breaks the documented contract fails the suite rather than the
reader.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import warnings

import httpx
import pytest

import nodus
from nodus import types
from nodus._brief import (
    BOOTSTRAP_FETCH_TOOLS,
    DEFAULT_IMAGE,
    IMAGE_FETCH_TOOLS,
    build_payload,
    status_filter,
)


# .invalid is the reserved TLD that can never resolve, so a request these tests
# forgot to intercept fails as a name lookup instead of leaving the machine.
def client_with(handler, **kw) -> nodus.Client:
    c = nodus.Client(api_key="nk_live_test", base_url="https://nodus.invalid", **kw)
    c._http = httpx.Client(
        base_url="https://nodus.invalid",
        transport=httpx.MockTransport(handler),
        headers={"Authorization": "Bearer nk_live_test"},
    )
    return c


# Exactly what POST /v1/workloads returns: both identifier spellings, and none
# of the fields that only exist on a read. Using the read shape here is what let
# a broken run() ship green.
SUBMIT_ACCEPTED = {
    "workload_id": "wl_abc",
    "id": "wl_abc",
    "status": "accepted",
    "revision": 1,
}

# GET /v1/workloads/{id} as internal/api marshals it — every read carries a
# meter. This run finished in a past billing period, so the meter is all zeros
# while spend_usd holds every charge the workload ever took.
WORKLOAD = {
    "id": "wl_abc",
    "tenant_id": "tn_acme",
    "status": "completed",
    "spend_usd": 291.4,
    "revision": 1,
    "created_at": "2026-07-27T10:00:00Z",
    "updated_at": "2026-07-27T11:00:00Z",
    "payload": {
        "source": {"image": "python:3.11-slim", "command": ["python", "train.py"]},
        "requirements": {"model": "7B fine-tune", "peak_memory_gb": 80},
        "outcome": {"max_cost_usd": 400},
        "continuity": {"mode": "checkpointed", "resume_on_interruption": True},
    },
    "route": {
        "offer_id": "nodus:a100-80-us-east",
        "compute_class": "accelerator",
        "fit_class": "a100-80",
        "region": "us-east",
        "price_usd_hour": 2.5,
        "expected_cost_usd": 300.0,
        "expected_hours": 18.0,
        "remaining_budget_usd": 100.0,
        "interruptible": True,
    },
    "stages": [
        {"id": "main", "status": "completed", "continuity_mode": "checkpointed",
         "completed_units": 4, "total_units": 4}
    ],
    "meter": {
        "settled_usd": 0.0,
        "accruing_usd": 0.0,
        "accruing_rate_usd_hour": 0.0,
        "total_now_usd": 0.0,
        "as_of": "2026-09-01T12:00:00Z",
    },
}


# -- brief construction ----------------------------------------------------


def test_build_payload_is_the_nested_wire_shape():
    """run() takes flat kwargs; the wire schema is nested. Docs show both."""
    p = build_payload(
        model="7B fine-tune",
        command=["python", "train.py"],
        peak_memory_gb=80,
        expected_runtime_hours=18,
        budget=400,
        finish_by="2026-08-01T09:00:00Z",
    )
    assert p["source"]["command"] == ["python", "train.py"]
    assert p["requirements"]["peak_memory_gb"] == 80
    assert p["outcome"] == {"max_cost_usd": 400.0, "complete_by": "2026-08-01T09:00:00Z"}
    assert p["continuity"] == {"mode": "checkpointed", "resume_on_interruption": True}


def test_continuity_defaults_to_checkpointed_and_ephemeral_opts_out():
    assert build_payload(budget=1)["continuity"]["mode"] == "checkpointed"
    eph = build_payload(budget=1, continuity="ephemeral")["continuity"]
    assert eph == {"mode": "ephemeral", "resume_on_interruption": False}


def test_enums_and_strings_are_interchangeable():
    a = build_payload(budget=1, continuity=nodus.ContinuityMode.RESTARTABLE,
                      interrupt_tolerance=nodus.InterruptTolerance.HIGH)
    b = build_payload(budget=1, continuity="restartable", interrupt_tolerance="high")
    assert a == b


def test_status_filter_accepts_presets_members_and_lists():
    assert status_filter("active") == "active"
    assert status_filter(nodus.WorkloadStatus.RUNNING) == "running"
    assert status_filter([nodus.WorkloadStatus.FAILED, "cancelled"]) == "failed,cancelled"
    assert status_filter(None) is None


def test_stages_replace_the_single_source():
    p = build_payload(budget=1,
                      stages=[{"id": "prepare"}, {"id": "train", "depends_on": ["prepare"]}])
    assert "source" not in p
    assert [s["id"] for s in p["stages"]] == ["prepare", "train"]


# -- the default image -----------------------------------------------------


def test_the_default_image_can_install_the_runner():
    """A default nobody asked for has to be one the runner can start in.

    The bootstrap fetches the runner with curl, then wget, then a stdlib
    python3; an image shipping none of them rents a host that can never run the
    work and bills for it anyway. The assertion is that requirement rather than
    a name, so changing the default is allowed and defaulting to an image whose
    fetch tools were never measured is not.
    """
    tools = IMAGE_FETCH_TOOLS.get(DEFAULT_IMAGE)
    assert tools is not None, f"{DEFAULT_IMAGE} has never been measured for a fetch tool"
    assert tools & BOOTSTRAP_FETCH_TOOLS, f"{DEFAULT_IMAGE} ships no tool the bootstrap can fetch with"
    assert build_payload(budget=1, command=["python", "train.py"])["source"]["image"] == DEFAULT_IMAGE


def test_an_image_measured_to_ship_no_fetch_tool_warns_before_submission():
    """The warning has to arrive before a host is rented, not after it bills."""
    with pytest.warns(UserWarning, match="curl"):
        p = build_payload(budget=1, image="ubuntu:22.04", command=["python", "train.py"])
    assert p["source"]["image"] == "ubuntu:22.04", "the brief is the caller's, not ours to rewrite"


def test_a_stage_naming_that_image_warns_too():
    """A staged brief rents the same hosts; the image lives one level down."""
    with pytest.warns(UserWarning, match="curl"):
        build_payload(budget=1, stages=[{"id": "train", "source": {"image": "ubuntu:22.04"}}])


def test_an_unmeasured_image_is_not_second_guessed():
    """Silence about images nobody measured: a guess would train callers to ignore it."""
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        build_payload(
            image="registry.example.com/team/trainer:2026-09", command=["./go"], budget=10
        )


# -- configuration ---------------------------------------------------------


def test_missing_api_key_raises_before_any_network_call(monkeypatch):
    monkeypatch.setenv("NODUS_BASE_URL", "https://nodus.invalid")
    monkeypatch.delenv("NODUS_API_KEY", raising=False)
    with pytest.raises(nodus.ConfigurationError) as exc:
        nodus.Client()
    assert "NODUS_API_KEY" in str(exc.value)


def test_base_url_must_be_http(monkeypatch):
    monkeypatch.setenv("NODUS_API_KEY", "nk_live_test")
    with pytest.raises(nodus.ConfigurationError):
        nodus.Client(base_url="ftp://nope")


# There is no built-in address, and this is the test that keeps it that way. Any
# address the SDK could pick is either a domain that does not resolve or an
# account this caller is not on, so it would answer a setup mistake with a
# network error. The client must refuse before it opens a socket, naming the
# setting and where its value comes from.
def test_missing_base_url_says_what_to_set_and_where_to_get_it(monkeypatch):
    monkeypatch.setenv("NODUS_API_KEY", "nk_live_test")
    monkeypatch.delenv("NODUS_BASE_URL", raising=False)
    with pytest.raises(nodus.ConfigurationError) as exc:
        nodus.Client()
    message = str(exc.value)
    assert "NODUS_BASE_URL" in message
    assert "https://nodus.run/console/" in message


def test_both_missing_are_reported_together(monkeypatch):
    monkeypatch.delenv("NODUS_API_KEY", raising=False)
    monkeypatch.delenv("NODUS_BASE_URL", raising=False)
    with pytest.raises(nodus.ConfigurationError) as exc:
        nodus.Client()
    message = str(exc.value)
    assert "NODUS_BASE_URL" in message and "NODUS_API_KEY" in message


# -- submission and handles ------------------------------------------------


def test_run_sends_idempotency_key_and_returns_a_handle():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["key"] = request.headers.get("Idempotency-Key")
        seen["body"] = json.loads(request.content)
        return httpx.Response(202, json=SUBMIT_ACCEPTED)

    with client_with(handler) as c:
        wl = c.run(model="7B fine-tune", peak_memory_gb=80, budget=400)

    assert seen["key"]
    assert seen["body"]["requirements"]["model"] == "7B fine-tune"
    assert isinstance(wl, nodus.Workload)
    assert wl.id == "wl_abc"


def test_run_reads_the_legacy_submit_shape():
    """A 202 carrying only ``workload_id`` still yields a usable handle.

    This is the bug this fixture exists for. POST /v1/workloads answers with
    ``workload_id`` while every read endpoint answers with ``id``, and the SDK
    read only ``id`` — so run() raised "submit returned no workload id" against
    the real control plane while every test passed, because the fixture used a
    shape submit has never returned. Replayed idempotency records stored before
    the fix still carry only ``workload_id``, so this shape has to keep working.
    """
    legacy = {"workload_id": "wl_abc", "status": "accepted", "revision": 1}
    with client_with(lambda r: httpx.Response(202, json=legacy)) as c:
        wl = c.run(model="7B fine-tune", peak_memory_gb=80, budget=400)
    assert wl.id == "wl_abc"
    assert wl.status == nodus.WorkloadStatus.ACCEPTED


def test_a_replayed_submission_says_it_was_replayed():
    """A replay answers 202 with the original body; only the header tells them apart."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            202, json=SUBMIT_ACCEPTED, headers={"Idempotent-Replayed": "true"}
        )

    with client_with(handler) as c:
        wl = c.run(model="x", budget=1, idempotency_key="nightly-2026-09-01")
    assert wl.replayed is True


def test_a_fresh_submission_is_not_marked_replayed():
    with client_with(lambda r: httpx.Response(202, json=SUBMIT_ACCEPTED)) as c:
        wl = c.run(model="x", budget=1)
    assert wl.replayed is False


def test_the_async_client_reports_a_replay_too():
    async def go():
        c = nodus.AsyncClient(api_key="nk_live_test", base_url="https://nodus.invalid")
        c._http = httpx.AsyncClient(
            base_url="https://nodus.invalid",
            transport=httpx.MockTransport(
                lambda r: httpx.Response(
                    202, json=SUBMIT_ACCEPTED, headers={"Idempotent-Replayed": "true"}
                )
            ),
        )
        async with c:
            return (await c.run(model="x", budget=1, idempotency_key="k")).replayed

    assert asyncio.run(go()) is True


def test_caller_supplied_idempotency_key_is_used_verbatim():
    """The docs tell people to pass a stable key for cross-call retries."""
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["key"] = request.headers.get("Idempotency-Key")
        return httpx.Response(202, json=WORKLOAD)

    with client_with(handler) as c:
        c.run(model="x", budget=1, idempotency_key="nightly-eval-2026-07-27")
    assert seen["key"] == "nightly-eval-2026-07-27"


def _submitted_outcome(**brief) -> dict:
    """The ``outcome`` object as it actually reaches the wire."""
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(202, json=SUBMIT_ACCEPTED)

    with client_with(handler) as c:
        c.run(**brief)
    return seen["body"]["outcome"]


def test_run_without_a_budget_sends_no_cost_ceiling():
    """A brief with no budget is uncapped, and the SDK invents no number.

    The mirror of Go's TestRunWithoutABudgetSendsNoCostCeiling: the two clients
    submit the same bytes for the same brief, so neither can drift back into a
    private ceiling on somebody else's money. The empty outcome object still
    ships, because that is what the Go client sends too. It is warned about
    rather than filled in.
    """
    with pytest.warns(UserWarning, match="budget="):
        outcome = _submitted_outcome(model="7B fine-tune", command=["python", "train.py"])
    assert "max_cost_usd" not in outcome, f"invented a ceiling: {outcome}"
    assert outcome == {}


def test_run_sends_an_explicit_budget_as_the_cost_ceiling():
    """``budget`` is the one name for the ceiling, and it reaches the wire."""
    assert _submitted_outcome(model="x", budget=25)["max_cost_usd"] == 25.0
    with pytest.raises(TypeError):
        _submitted_outcome(model="x", max_cost_usd=40)


def test_workload_exposes_the_documented_attributes():
    with client_with(lambda r: httpx.Response(200, json=WORKLOAD)) as c:
        wl = c.get("wl_abc")

    assert wl.status is nodus.WorkloadStatus.COMPLETED
    assert wl.is_terminal and wl.succeeded
    assert wl.spend_usd == 291.4
    assert wl.budget_usd == 400.0
    assert wl.route.sku == "nodus:a100-80-us-east"
    assert wl.route.compute_class is nodus.ComputeClass.ACCELERATOR
    # sku is the only name for it. `offer_id` is the wire spelling read in
    # Route.from_dict, not a second attribute to write code against.
    assert not hasattr(wl.route, "offer_id")
    assert wl.stages[0].completed_units == 4
    assert wl.created_at is not None


def test_route_never_exposes_a_supplier():
    """Product contract: placement is a Nodus decision."""
    with client_with(lambda r: httpx.Response(200, json=WORKLOAD)) as c:
        route = c.get("wl_abc").route
    for banned in ("supplier", "provider", "vendor"):
        assert not hasattr(route, banned)


def test_refresh_mutates_in_place_and_returns_self():
    with client_with(lambda r: httpx.Response(200, json=WORKLOAD)) as c:
        wl = c.get("wl_abc")
        assert wl.refresh() is wl


def test_wait_polls_until_terminal():
    states = ["running", "running", "completed"]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={**WORKLOAD, "status": states.pop(0)})

    with client_with(handler) as c:
        wl = c.get("wl_abc")
        wl.wait(poll_seconds=0)
    assert wl.status is nodus.WorkloadStatus.COMPLETED
    assert not states


def test_wait_has_no_deadline_of_its_own():
    """The mirror of Go's TestWaitHasNoDeadlineOfItsOwn.

    Neither SDK invents a bound for a wait. A run outlives any interval a client
    could pick, and giving up does not stop it — the workload keeps going and
    keeps billing while the caller believes it failed. ``None`` is the whole
    contract: no number, in either language.
    """
    for wait in (nodus.Client.wait, nodus.Workload.wait, nodus.AsyncWorkload.wait):
        default = inspect.signature(wait).parameters["timeout_seconds"].default
        assert default is None, f"{wait.__qualname__} invented a deadline: {default}"

    states = ["running"] * 20 + ["completed"]
    with client_with(lambda r: httpx.Response(200, json={**WORKLOAD, "status": states.pop(0)})) as c:
        wl = c.get("wl_abc")
        assert wl.wait(poll_seconds=0).status is nodus.WorkloadStatus.COMPLETED


def test_wait_timeout_raises_and_does_not_cancel():
    """The mirror of Go's TestWaitBoundEndsTheWaitingNotTheRun.

    The caller's own bound ends the waiting, never the run: nothing is
    cancelled, so a workload still spending is left for the caller to decide
    about.
    """
    paths = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        return httpx.Response(200, json={**WORKLOAD, "status": "running"})

    with client_with(handler) as c:
        wl = c.get("wl_abc")
        with pytest.raises(nodus.APITimeoutError):
            wl.wait(poll_seconds=0, timeout_seconds=0)
    assert not [p for p in paths if p.endswith("/cancel")], paths


# -- pagination, events, artifacts ----------------------------------------


def test_iter_workloads_follows_next_offset():
    pages = [
        {"workloads": [{"id": "wl_1", "status": "running"}], "next_offset": 1},
        {"workloads": [{"id": "wl_2", "status": "completed"}]},
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=pages.pop(0))

    with client_with(handler) as c:
        assert [w.id for w in c.iter_workloads(page_size=1)] == ["wl_1", "wl_2"]


def test_list_sends_the_status_filter():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["status"] = request.url.params.get("status")
        return httpx.Response(200, json={"workloads": []})

    with client_with(handler) as c:
        c.list(status="active")
    assert seen["status"] == "active"


def test_events_map_the_wire_shape_onto_the_documented_fields():
    payload = {"events": [
        {"id": 7, "event_id": "ev_1", "event_type": "workload.running",
         "payload": {"x": 1}, "created_at": "2026-07-27T10:00:00Z"}
    ]}
    with client_with(lambda r: httpx.Response(200, json=payload)) as c:
        ev = c.events("wl_abc")[0]
    assert (ev.seq, ev.id, ev.type) == (7, "ev_1", "workload.running")
    assert ev.created_at is not None


# The payload below is the shape internal/store.ListArtifacts really returns:
# manifest rows, with the digests nested inside the manifest. The SDK used to
# read a flat name/uri/sha256/bytes object that this endpoint has never sent, so
# every field came back empty and `verified` came back True regardless.
def test_artifacts_map_the_manifest_row_shape():
    payload = {
        "artifacts": [
            {
                "manifest_id": "cm_1",
                "stage_id": "stg_train",
                "generation": 2,
                "sequence": 7,
                "created_at": "2026-07-27T10:00:00Z",
                "manifest": {
                    "stage_id": "stg_train",
                    "generation": 2,
                    "sequence": 7,
                    "final": True,
                    "files": [
                        {"uri": "key:wl_1/stg_train/gen2/seq7/checkpoint.bin",
                         "sha256": "ab" * 32, "bytes": 10,
                         "media": "application/x-tar"}
                    ],
                    "outputs": {
                        "model": {"uri": "key:wl_1/stg_train/model.bin",
                                  "sha256": "cd" * 32, "bytes": 20}
                    },
                },
            }
        ]
    }
    with client_with(lambda r: httpx.Response(200, json=payload)) as c:
        art = c.artifacts("wl_abc")[0]
    assert (art.manifest_id, art.stage_id, art.generation, art.sequence) == (
        "cm_1", "stg_train", 2, 7)
    assert art.final and art.created_at is not None
    assert art.files[0].sha256 == "ab" * 32 and art.files[0].bytes == 10
    assert art.files[0].is_tar
    assert art.outputs["model"].sha256 == "cd" * 32
    assert not art.outputs["model"].is_tar
    # No verification claim on a payload that carries no verification state.
    assert not hasattr(art, "verified")


# GET /v1/workloads/{id}/ledger as internal/api/pilot.go writes it: a
# customer_charge is a CREDIT, and settlement carries the balance settle_close
# leaves — exactly $0.00 when healthy.
LEDGER = {
    "workload_id": "wl_abc",
    "entries": [
        {
            "id": "led_1",
            "correlation_id": "cor_1",
            "entry_type": "customer_charge",
            "debit_usd": 0.0,
            "credit_usd": 5.0,
            "currency": "USD",
            "evidence": {"hours": 2.0},
            "created_at": "2026-07-27T11:00:00Z",
        },
        {
            "id": "led_2",
            "correlation_id": "settle_1",
            "entry_type": "settle_close",
            "debit_usd": 5.0,
            "credit_usd": 0.0,
            "currency": "USD",
            "evidence": {"reason": "settle_close_balance"},
            "created_at": "2026-07-27T11:05:00Z",
        },
    ],
    "settlement": {
        "status": "closed",
        "balance_usd": 0.0,
        "correlation_id": "settle_1",
        "closed_at": "2026-07-27T11:05:00Z",
    },
}


def test_ledger_parses_entries_and_settlement():
    with client_with(lambda r: httpx.Response(200, json=LEDGER)) as c:
        led = c.ledger("wl_abc")
    assert led.entries[0].credit_usd == 5.0
    assert led.settlement.status == "closed"
    assert led.settlement.balance_usd == 0.0
    assert led.settlement.closed_at is not None


def test_the_ledger_totals_what_the_customer_was_charged():
    """The charge is the sum of customer_charge credits, not the settlement balance."""
    with client_with(lambda r: httpx.Response(200, json=LEDGER)) as c:
        led = c.ledger("wl_abc")
    assert led.charged_usd == 5.0


def test_a_settlement_carries_no_total_the_server_never_sends():
    """A settlement's amount comes only from ``balance_usd``, the key pilot.go writes."""
    assert not hasattr(nodus.Settlement(), "total_usd")
    # A stray money key in the body is ignored, not read as the amount.
    st = nodus.Settlement.from_dict(
        {"status": "closed", "balance_usd": 0.0, "total_usd": 99.0}
    )
    assert st.balance_usd == 0.0


# -- a malformed field is not a crash --------------------------------------


def test_a_malformed_money_field_does_not_end_a_wait():
    """An 18-hour wait must not die of one bad field in one poll.

    ``float()`` on whatever the body carried raised a bare TypeError -- not a
    NodusError, so the wait's own failure policy never saw it and the caller
    got a traceback for a workload that was still running and still billing.
    """
    bodies = [
        {**WORKLOAD, "status": "running", "spend_usd": {"usd": 5}},
        {**WORKLOAD, "status": "completed"},
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=bodies.pop(0) if bodies else WORKLOAD)

    with client_with(handler) as c:
        wl = c.wait("wl_abc", poll_seconds=0)
    assert wl.succeeded


def test_money_that_is_not_a_number_is_not_shown_as_money():
    """NaN and infinity format as "nan" and "inf" in a dollar figure."""
    import math

    body = {
        **WORKLOAD,
        "spend_usd": float("nan"),
        "meter": {
            "settled_usd": float("nan"),
            "accruing_usd": 1e400,
            "accruing_rate_usd_hour": "six dollars",
            "total_now_usd": None,
            "as_of": "2026-09-01T12:00:00Z",
        },
    }
    with client_with(lambda r: httpx.Response(200, json=body)) as c:
        wl = c.get("wl_abc")
    assert math.isfinite(wl.spend_usd) and wl.spend_usd == 0.0
    assert math.isfinite(wl.meter.accruing_usd) and wl.meter.accruing_usd == 0.0
    assert math.isfinite(wl.cost_now_usd)


def test_a_payload_that_is_not_an_object_is_not_read_as_one():
    """``payload.outcome.max_cost_usd`` is three assumptions about a free-form
    field, and the first one raised AttributeError."""
    body = {**WORKLOAD, "payload": "surprise", "stages": "nope", "route": "gone",
            "revision": {"n": 2}}
    with client_with(lambda r: httpx.Response(200, json=body)) as c:
        wl = c.get("wl_abc")
    assert wl.budget_usd == 0.0
    assert wl.stages == []
    assert wl.revision == 1


def test_a_malformed_ledger_row_still_parses():
    body = {
        "entries": [{"id": "led_1", "entry_type": "customer_charge",
                     "credit_usd": "five", "evidence": "none"}],
        "settlement": {"status": "closed", "balance_usd": float("nan")},
    }
    with client_with(lambda r: httpx.Response(200, json=body)) as c:
        led = c.ledger("wl_abc")
    assert led.entries[0].credit_usd == 0.0
    assert led.entries[0].evidence == {}
    assert led.settlement.balance_usd == 0.0
    assert led.charged_usd == 0.0


# -- an id is one path segment ---------------------------------------------


TRAVERSALS = [
    "../../v1/webhooks",
    "..%2f..%2fv1%2fwebhooks",
    "wl_abc/../../v1/webhooks",
    "wl_abc?x=1",
    "wl_abc#frag",
    "wl abc",
    "",
]


@pytest.mark.parametrize("bad", TRAVERSALS)
def test_a_workload_id_cannot_walk_out_of_its_own_path(bad):
    """``/v1/workloads/{id}`` is a template, and an id is a segment of it.

    httpx normalises dot segments before it sends, so an id of ``../../v1/
    webhooks`` left the workload namespace entirely and read back the webhook
    signing secret. The same id can arrive from the server, through _absorb.
    """
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        return httpx.Response(200, json=WORKLOAD)

    with client_with(handler) as c:
        with pytest.raises(nodus.ValidationError):
            c.get(bad)
    assert not paths, f"the request left the client: {paths}"


@pytest.mark.parametrize(
    "call",
    [
        lambda c, i: c.get(i),
        lambda c, i: c.cancel(i),
        lambda c, i: c.events(i),
        lambda c, i: c.artifacts(i),
        lambda c, i: c.ledger(i),
        lambda c, i: c.logs(i),
        lambda c, i: list(c.iter_events(i)),
        lambda c, i: c.wait(i, poll_seconds=0),
        lambda c, i: list(c.stream_events(i, poll_seconds=0)),
    ],
)
def test_every_call_that_takes_an_id_checks_it(call):
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        return httpx.Response(200, json=WORKLOAD)

    with client_with(handler) as c:
        with pytest.raises(nodus.ValidationError):
            call(c, "../../v1/webhooks")
    assert not paths, f"the request left the client: {paths}"


def test_the_async_client_checks_ids_too():
    async def go():
        c = nodus.AsyncClient(api_key="nk_live_test", base_url="https://nodus.invalid")
        paths: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            paths.append(request.url.path)
            return httpx.Response(200, json=WORKLOAD)

        c._http = httpx.AsyncClient(
            base_url="https://nodus.invalid", transport=httpx.MockTransport(handler)
        )
        async with c:
            for call in (c.get, c.cancel, c.events, c.artifacts, c.ledger, c.logs):
                with pytest.raises(nodus.ValidationError):
                    await call("../../v1/webhooks")
        return paths

    assert asyncio.run(go()) == []


def test_an_id_the_server_supplied_is_checked_before_it_is_followed():
    """A handle refreshes on the id the body carried, so a hostile plane could
    aim the next request wherever it liked."""
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        return httpx.Response(200, json={**WORKLOAD, "id": "../../v1/webhooks"})

    with client_with(handler) as c:
        wl = c.get("wl_abc")
        with pytest.raises(nodus.ValidationError):
            wl.refresh()
    assert paths == ["/v1/workloads/wl_abc"], paths


def test_an_ordinary_id_still_works():
    with client_with(lambda r: httpx.Response(200, json=WORKLOAD)) as c:
        assert c.get("wl_00000001-0000-4000-8000-000000000000").id == "wl_abc"


# -- errors ----------------------------------------------------------------


@pytest.mark.parametrize(
    "status,body,expected",
    [
        (401, {"error": "unauthorized"}, nodus.AuthenticationError),
        (403, {"error": "forbidden"}, nodus.AuthenticationError),
        (404, {"error": "not_found"}, nodus.NotFoundError),
        (400, {"error": "invalid_plan"}, nodus.ValidationError),
        (422, {"error": "bad"}, nodus.ValidationError),
        (409, {"error": "idempotency_conflict"}, nodus.IdempotencyConflictError),
        (402, {"error": "budget"}, nodus.BudgetExceededError),
        (503, {"error": "no_capacity"}, nodus.CapacityUnavailableError),
        (418, {"error": "teapot"}, nodus.APIError),
    ],
)
def test_status_codes_map_to_typed_errors(status, body, expected):
    with client_with(lambda r: httpx.Response(status, json=body), max_retries=0) as c:
        with pytest.raises(expected):
            c.get("wl_abc")


def test_every_error_is_a_nodus_error():
    with client_with(lambda r: httpx.Response(404, json={"error": "x"}), max_retries=0) as c:
        with pytest.raises(nodus.NodusError):
            c.get("wl_abc")


def test_signature_rejection_is_distinct_from_a_bad_key():
    body = {"error": "invalid_signature", "message": "bad sig"}
    with client_with(lambda r: httpx.Response(401, json=body), max_retries=0) as c:
        with pytest.raises(nodus.SignatureError):
            c.get("wl_abc")


def test_rate_limit_exposes_retry_after():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": "rate_limited"}, headers={"Retry-After": "7"})

    with client_with(handler, max_retries=0) as c:
        with pytest.raises(nodus.RateLimitError) as exc:
            c.get("wl_abc")
    assert exc.value.retry_after == 7.0


def test_budget_error_carries_the_payload_the_docs_read():
    body = {
        "error": "budget_exceeded",
        "monthly_spend_cap_usd": 2500,
        "month_to_date_usd": 2400,
        "estimated_cost_usd": 300,
    }
    with client_with(lambda r: httpx.Response(402, json=body), max_retries=0) as c:
        with pytest.raises(nodus.BudgetExceededError) as exc:
            c.get("wl_abc")
    p = exc.value.payload
    assert p["monthly_spend_cap_usd"] - p["month_to_date_usd"] == 100


def test_error_exposes_code_and_message():
    body = {"error": "invalid_plan", "message": "stage id is not well formed"}
    with client_with(lambda r: httpx.Response(400, json=body), max_retries=0) as c:
        with pytest.raises(nodus.ValidationError) as exc:
            c.get("wl_abc")
    assert exc.value.code == "invalid_plan"
    assert "stage id" in str(exc.value)


# -- retries ---------------------------------------------------------------


def test_transient_failures_are_retried():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(503, json={"error": "no_capacity"})
        return httpx.Response(200, json=WORKLOAD)

    c = client_with(handler, max_retries=3)
    c._backoff = lambda *a, **k: 0  # type: ignore[method-assign]
    with c:
        assert c.get("wl_abc").id == "wl_abc"
    assert calls["n"] == 3


def test_client_errors_are_not_retried():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(400, json={"error": "invalid_plan"})

    with client_with(handler, max_retries=3) as c:
        with pytest.raises(nodus.ValidationError):
            c.get("wl_abc")
    assert calls["n"] == 1


def test_a_submit_that_times_out_hands_back_the_key_that_makes_the_retry_safe():
    """A timeout is not a refusal: the workload may exist and be billing, and
    the key on the error is what makes a retry the same submission."""
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("too slow", request=request)

    with client_with(handler, max_retries=0) as c:
        with pytest.raises(nodus.APITimeoutError) as exc:
            c.run(model="x", budget=1, idempotency_key="nightly-2026-09-01")
    assert exc.value.payload.get("idempotency_key") == "nightly-2026-09-01"
    assert "idempotency_key" in str(exc.value)


def test_a_submit_that_cannot_connect_hands_back_its_generated_key():
    """The key run() minted for itself is the one the caller never saw."""
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    with client_with(handler, max_retries=0) as c:
        with pytest.raises(nodus.APIConnectionError) as exc:
            c.run(model="x", budget=1)
    key = exc.value.payload.get("idempotency_key")
    assert key and key.startswith("nodus-")


def test_an_async_submit_that_times_out_hands_back_its_key_too():
    async def go():
        c = nodus.AsyncClient(api_key="nk_live_test", base_url="https://nodus.invalid",
                              max_retries=0)

        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectTimeout("too slow", request=request)

        c._http = httpx.AsyncClient(
            base_url="https://nodus.invalid", transport=httpx.MockTransport(handler)
        )
        async with c:
            with pytest.raises(nodus.APITimeoutError) as exc:
                await c.run(model="x", budget=1, idempotency_key="k-1")
        return exc.value.payload.get("idempotency_key")

    assert asyncio.run(go()) == "k-1"


def test_a_read_that_times_out_says_nothing_about_keys():
    """A GET creates nothing, so there is no resubmission to make safe."""
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("too slow", request=request)

    with client_with(handler, max_retries=0) as c:
        with pytest.raises(nodus.APITimeoutError) as exc:
            c.get("wl_abc")
    assert exc.value.payload == {}


def test_idempotency_conflict_is_not_retried():
    """Retrying a 409 cannot help: the key already names a different payload."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(409, json={"error": "idempotency_conflict"})

    with client_with(handler, max_retries=3) as c:
        with pytest.raises(nodus.IdempotencyConflictError):
            c.run(model="x", budget=1)
    assert calls["n"] == 1


# -- async -----------------------------------------------------------------


def test_the_async_client_reads_the_same_wire_shape():
    """That the two clients offer the same calls is pinned in test_api_surface."""

    async def go():
        c = nodus.AsyncClient(api_key="nk_live_test", base_url="https://nodus.invalid")
        c._http = httpx.AsyncClient(
            base_url="https://nodus.invalid",
            transport=httpx.MockTransport(lambda r: httpx.Response(200, json=WORKLOAD)),
        )
        async with c:
            wl = await c.get("wl_abc")
            assert isinstance(wl, nodus.AsyncWorkload)
            assert wl.route.sku == "nodus:a100-80-us-east"
            assert wl.succeeded
            assert (await wl.refresh()) is wl

    asyncio.run(go())


def test_sync_and_async_handles_expose_the_same_attributes():
    shared = {"id", "status", "route", "spend_usd", "budget_usd", "stages",
              "error", "replayed", "is_terminal", "succeeded", "created_at", "updated_at"}
    for cls in (nodus.Workload, nodus.AsyncWorkload):
        for name in shared:
            assert hasattr(cls, name) or name in cls.__annotations__ or \
                name in nodus._WorkloadState.__annotations__, f"{cls.__name__}.{name}"


# -- unknown wire values ---------------------------------------------------


def test_unknown_status_from_a_newer_control_plane_does_not_raise():
    payload = {**WORKLOAD, "status": "quiescing"}
    with client_with(lambda r: httpx.Response(200, json=payload)) as c:
        wl = c.get("wl_abc")
    assert wl.status == "quiescing"
    assert not wl.is_terminal


# -- a typo'd keyword is not a brief ---------------------------------------


def test_a_typod_keyword_is_refused_rather_than_forwarded():
    """``budget_usd=`` is not ``budget=``, and the difference is the spend cap.

    The control plane drops keys it does not model, so a forwarded typo submits
    an uncapped run and answers 202. The rejection names the key and the field
    it was most likely meant to be.
    """
    with pytest.raises(TypeError) as exc:
        build_payload(model="x", command=["a"], budget_usd=400)
    message = str(exc.value)
    assert "budget_usd" in message
    assert "budget" in message


def test_every_unknown_keyword_is_named_at_once():
    with pytest.raises(TypeError) as exc:
        build_payload(model="x", budget=1, runtime_hours=3, memory_gb=80)
    message = str(exc.value)
    assert "runtime_hours" in message and "memory_gb" in message


def test_a_field_this_sdk_does_not_model_travels_in_extra():
    """The escape hatch is deliberate and spelled, so a typo cannot use it."""
    p = build_payload(model="x", budget=1, extra={"experimental_knob": 3})
    assert p["experimental_knob"] == 3


def test_extra_cannot_overwrite_the_brief_that_was_just_built():
    """``extra`` adds fields; it does not get to delete the cost ceiling."""
    with pytest.raises(TypeError) as exc:
        build_payload(model="x", budget=400, extra={"outcome": {"note": "hi"}})
    message = str(exc.value)
    assert "outcome" in message
    assert "extra" in message


def test_extra_still_carries_a_field_alongside_the_brief():
    p = build_payload(model="x", budget=400, extra={"experimental_knob": 3})
    assert p["experimental_knob"] == 3
    assert p["outcome"]["max_cost_usd"] == 400.0


def test_a_typod_keyword_never_reaches_the_network():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(202, json=SUBMIT_ACCEPTED)

    with client_with(handler) as c:
        with pytest.raises(TypeError) as exc:
            c.run(model="x", budget_usd=400)
    assert "budget" in str(exc.value)
    assert not calls, "an uncapped workload was submitted from a typo"


# -- what it is costing right now ------------------------------------------


# A list row as store.WorkloadListItem marshals it: one closed lease at $2.50,
# one open lease six dollars into its hour, all in this billing period.
RUNNING_METERED = {
    "id": "wl_abc",
    "status": "running",
    "revision": 1,
    "spend_usd": 2.5,
    "created_at": "2026-09-01T10:00:00Z",
    "updated_at": "2026-09-01T11:00:00Z",
    "meter": {
        "settled_usd": 2.5,
        "accruing_usd": 6.0,
        "accruing_rate_usd_hour": 6.0,
        "total_now_usd": 8.5,
        "as_of": "2026-09-01T12:00:00Z",
    },
}

# The same run started in the period before this one: half its $200 of charges
# fall outside the meter's window, and the open lease has $6 accruing.
RUNNING_ACROSS_A_PERIOD_BOUNDARY = {
    "id": "wl_abc",
    "status": "running",
    "revision": 1,
    "spend_usd": 200.0,
    "meter": {
        "settled_usd": 100.0,
        "accruing_usd": 6.0,
        "accruing_rate_usd_hour": 6.0,
        "total_now_usd": 106.0,
        "as_of": "2026-09-01T12:00:00Z",
    },
}


def test_a_running_workload_reports_what_it_is_costing_now():
    """``spend_usd`` moves only when a lease closes, so it reads $0 mid-run.

    Both the read and the list endpoint send a meter precisely so a running row
    does not claim it cost nothing. Dropping it is the difference between a
    customer seeing $8.50 and seeing $0.00.
    """
    with client_with(lambda r: httpx.Response(200, json=RUNNING_METERED)) as c:
        wl = c.get("wl_abc")
    assert wl.spend_usd == 2.5, "spend_usd stays settled-only"
    assert wl.meter is not None
    assert wl.meter.settled_usd == 2.5
    assert wl.meter.accruing_rate_usd_hour == 6.0
    assert wl.meter.as_of is not None
    assert wl.cost_now_usd == 8.5


def test_a_workload_charged_in_an_earlier_period_still_reports_what_it_cost():
    """A finished run is not free because the month rolled over: the meter
    counts the billing period, ``spend_usd`` counts the workload."""
    with client_with(lambda r: httpx.Response(200, json=WORKLOAD)) as c:
        wl = c.get("wl_abc")
    assert wl.meter is not None and wl.meter.total_now_usd == 0.0
    assert wl.cost_now_usd == 291.4


def test_a_run_that_crossed_a_period_boundary_counts_both_halves():
    """What is settled and what is accruing are two scopes, and both are owed:
    $200 charged, $6 running up, $206 owed."""
    body = RUNNING_ACROSS_A_PERIOD_BOUNDARY
    with client_with(lambda r: httpx.Response(200, json=body)) as c:
        wl = c.get("wl_abc")
    assert wl.cost_now_usd == 206.0


def test_cost_now_falls_back_to_settled_spend_when_no_meter_is_sent():
    """Defensive only: every live endpoint sends a meter, so this is a shape no
    current control plane returns. It is the answer for an older one."""
    body = {"id": "wl_abc", "status": "completed", "spend_usd": 291.4, "revision": 1}
    with client_with(lambda r: httpx.Response(200, json=body)) as c:
        wl = c.get("wl_abc")
    assert wl.meter is None
    assert wl.cost_now_usd == wl.spend_usd == 291.4


def test_list_rows_carry_the_meter_too():
    page = {"workloads": [RUNNING_METERED]}
    with client_with(lambda r: httpx.Response(200, json=page)) as c:
        assert c.list()[0].cost_now_usd == 8.5


# -- what v1 does not offer ------------------------------------------------


def test_there_is_no_module_level_run():
    """One idiom: build a client, submit on it, wait on it.

    The module-level helper closed its client before returning the handle, so
    every follow-up call on what it handed back failed.
    """
    assert "run" not in nodus.__all__
    assert not hasattr(nodus, "run")


def test_the_price_book_is_gone():
    """It resolved a path inside the monorepo, so it was dead for every install."""
    import importlib.util

    assert importlib.util.find_spec("nodus._pricebook") is None


# -- errors say what to do about them --------------------------------------


def _raised(status: int, body: dict, headers: dict | None = None) -> nodus.NodusError:
    with client_with(lambda r: httpx.Response(status, json=body, headers=headers or {}),
                     max_retries=0) as c:
        with pytest.raises(nodus.NodusError) as exc:
            c.run(model="x", budget=1)
    return exc.value


def test_an_error_says_what_to_do_and_which_request_it_was():
    """A message naming only what failed leaves the reader where they started."""
    err = _raised(
        400,
        {"error": "missing_source", "message": "source, framework, or stages required"},
        {"X-Request-Id": "req_0192"},
    )
    message = str(err)
    assert "source, framework, or stages required" in message
    assert "image=" in message, "no remedy in the message"
    assert "req_0192" in message, "the request id was captured and never rendered"


@pytest.mark.parametrize(
    "code,hint",
    [
        ("invalid_compute_class", "accelerator"),
        ("invalid_continuity_mode", "checkpointed"),
        ("invalid_complete_by", "finish_by"),
        ("idempotency_conflict", "Idempotency-Key"),
        ("capacity_unavailable", "interrupt_tolerance"),
    ],
)
def test_every_rejection_the_control_plane_sends_carries_a_remedy(code, hint):
    assert hint in str(_raised(400, {"error": code, "message": "no"}))


def test_budget_refusal_exposes_the_arithmetic_as_numbers():
    """The 402 body is the whole refusal; reading it should not be dict archaeology."""
    err = _raised(
        402,
        {
            "error": "budget_exceeded",
            "monthly_spend_cap_usd": 2500,
            "month_to_date_usd": 2400,
            "estimated_cost_usd": 300,
            "remaining_headroom_usd": 100,
        },
    )
    assert isinstance(err, nodus.BudgetExceededError)
    assert err.monthly_cap_usd == 2500.0
    assert err.month_to_date_usd == 2400.0
    assert err.estimated_cost_usd == 300.0
    assert err.headroom_usd == 100.0


def test_the_refusal_carries_the_money_that_is_not_settled_yet():
    """The 402 carries all four numbers the guard refused on, accruing and
    committed money included."""
    err = _raised(
        402,
        {
            "error": "budget_exceeded",
            "monthly_spend_cap_usd": 2500,
            "month_to_date_usd": 900,
            "accruing_usd": 1200,
            "in_flight_committed_usd": 300,
            "remaining_headroom_usd": 100,
            "estimated_cost_usd": 300,
        },
    )
    assert err.accruing_usd == 1200.0
    assert err.in_flight_committed_usd == 300.0


def test_headroom_is_the_servers_number_or_none_at_all():
    """cap - month_to_date is not headroom, and guessing it overstates it."""
    err = _raised(
        402,
        {
            "error": "budget_exceeded",
            "message": "spend cap reached",
            "monthly_spend_cap_usd": 2500,
            "month_to_date_usd": 900,
        },
    )
    assert err.headroom_usd is None
    assert "spend cap reached" in str(err), "the server's own words are the answer"


def test_a_long_retry_after_is_clamped_rather_than_discarded():
    """A 600-second hint is real; throwing it away retried in half a second."""
    err = _raised(429, {"error": "rate_limited"}, {"Retry-After": "600"})
    assert err.retry_after == 600.0, "the server's own number is what the caller reads"
    assert err.retry_after_header == "600"
    resp = httpx.Response(429, headers={"Retry-After": "600"})
    assert nodus.Client._backoff(0, resp) == 300.0, "a backoff must not hold for ten minutes"


def test_retry_after_in_http_date_form_is_understood():
    from email.utils import format_datetime
    from datetime import datetime, timedelta, timezone

    when = datetime.now(timezone.utc) + timedelta(seconds=45)
    err = _raised(429, {"error": "rate_limited"}, {"Retry-After": format_datetime(when)})
    assert err.retry_after is not None and 30 <= err.retry_after <= 60


# -- handles are identities, and a partial body is not a blank one ---------


def test_two_handles_on_one_workload_are_two_handles():
    """Blank handles used to compare equal, and no handle could go in a set."""
    with client_with(lambda r: httpx.Response(200, json=WORKLOAD)) as c:
        a, b = c.get("wl_abc"), c.get("wl_abc")
    assert a == a and a != b
    assert len({a, b}) == 2


def test_a_partial_body_keeps_what_was_already_known():
    """An absent field is not a field set to nothing.

    Clobbering on a short body wiped ``status`` to None, and a wait polling that
    never sees a terminal state runs until the caller's own bound.
    """
    bodies = [WORKLOAD, {"id": "wl_abc", "revision": 2}]
    with client_with(lambda r: httpx.Response(200, json=bodies.pop(0))) as c:
        wl = c.get("wl_abc")
        wl.refresh()
    assert wl.status is nodus.WorkloadStatus.COMPLETED
    assert wl.route is not None
    assert wl.spend_usd == 291.4
    assert wl.revision == 2


def test_an_empty_body_is_an_error_not_a_blank_workload():
    with client_with(lambda r: httpx.Response(200, content=b""), max_retries=0) as c:
        with pytest.raises(nodus.NodusError):
            c.get("wl_abc")


# -- briefs: commands, deadlines, ceilings ---------------------------------


def test_a_string_command_is_split_the_way_a_shell_would():
    p = build_payload(command='python train.py --name "my run"', budget=1)
    assert p["source"]["command"] == ["python", "train.py", "--name", "my run"]


def test_a_datetime_deadline_reaches_the_wire_as_rfc3339():
    """``finish_by=datetime(...)`` used to die inside json.dumps."""
    from datetime import datetime, timezone

    p = build_payload(model="x", budget=1,
                      finish_by=datetime(2026, 8, 1, 9, 0, tzinfo=timezone.utc))
    assert p["outcome"]["complete_by"] == "2026-08-01T09:00:00Z"
    json.dumps(p)


def test_a_brief_with_no_budget_says_it_is_uncapped():
    """Uncapped is a choice, and it should be one somebody made on purpose."""
    with pytest.warns(UserWarning, match="budget="):
        build_payload(model="x", command=["a"])


def test_a_money_warning_blames_the_brief_that_caused_it(recwarn):
    """The default filter is one warning per location: blamed on the caller,
    both briefs are told; blamed on the SDK, only the first would be."""
    with client_with(lambda r: httpx.Response(202, json=SUBMIT_ACCEPTED)) as c:
        with warnings.catch_warnings(record=True) as seen:
            warnings.simplefilter("default")
            c.run(model="first", command=["python", "train.py"])
            c.run(model="second", command=["python", "train.py"])

    uncapped = [w for w in seen if "budget=" in str(w.message)]
    assert len(uncapped) == 2, [str(w.message) for w in seen]
    for w in uncapped:
        assert w.filename.endswith("test_sdk.py"), w.filename


def test_a_money_warning_blames_a_direct_brief_too(recwarn):
    """build_payload() sits one frame closer, and a hardcoded depth cannot be
    right for both it and run()."""
    with warnings.catch_warnings(record=True) as seen:
        warnings.simplefilter("default")
        build_payload(model="x", command=["a"])
    assert len(seen) == 1
    assert seen[0].filename.endswith("test_sdk.py"), seen[0].filename


def test_a_staged_brief_is_warned_about_a_budget_like_any_other():
    """A staged pipeline is the brief with the most to spend, not the least."""
    with pytest.warns(UserWarning, match="budget="):
        build_payload(stages=[{"id": "train"}])
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        build_payload(stages=[{"id": "train"}], budget=50)


def test_stages_refuse_the_source_they_would_throw_away():
    """``stages=`` replaces the top-level source, so image= alongside it is a lie."""
    with pytest.raises(TypeError) as exc:
        build_payload(stages=[{"id": "train"}], budget=1, image="ubuntu:22.04",
                      command=["python", "train.py"])
    message = str(exc.value)
    assert "image" in message and "command" in message
    assert "stages" in message


# -- pagination and event walking ------------------------------------------


def test_list_says_it_is_one_page():
    assert "first page" in (nodus.Client.list.__doc__ or "").splitlines()[0]


def test_events_documents_the_server_cap():
    assert "100" in (nodus.Client.events.__doc__ or "")


def test_iter_events_walks_past_the_cap_the_server_returns():
    batches = [
        {"events": [{"id": i, "event_type": "workload.running"} for i in range(1, 101)]},
        {"events": [{"id": 101, "event_type": "workload.completed"}]},
        {"events": []},
    ]
    with client_with(lambda r: httpx.Response(200, json=batches.pop(0))) as c:
        assert len(list(c.iter_events("wl_abc"))) == 101


def test_iter_workloads_stops_when_the_offset_stops_advancing():
    """A next_offset that does not move is a stall, not a next page."""
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        assert len(calls) <= 4, "iter_workloads never stopped"
        return httpx.Response(
            200, json={"workloads": [{"id": "wl_1", "status": "running"}], "next_offset": 0}
        )

    with client_with(handler) as c:
        assert [w.id for w in c.iter_workloads(page_size=1)] == ["wl_1"]


def test_streaming_asks_whether_it_is_over_only_when_a_batch_is_empty():
    """Two requests per poll doubles the load on a stream that runs for hours."""
    paths: list[str] = []
    events = [{"events": [{"id": 1, "event_type": "workload.running"}]}, {"events": []}]

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path.endswith("/events"):
            return httpx.Response(200, json=events.pop(0) if events else {"events": []})
        return httpx.Response(200, json=WORKLOAD)

    with client_with(handler) as c:
        assert [e.type for e in c.stream_events("wl_abc", poll_seconds=0)] == ["workload.running"]
    assert paths.count("/v1/workloads/wl_abc") == 1


# -- logs ------------------------------------------------------------------


def _log_handler(seen: dict) -> object:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/logs"):
            seen["params"] = dict(request.url.params)
            return httpx.Response(200, text="step-1\nstep-2\n")
        return httpx.Response(200, json=WORKLOAD)

    return handler


def test_generation_zero_is_still_a_filter():
    """A caller computing generation 0 has a bug; silently reading the latest hides it."""
    seen: dict = {}
    with client_with(_log_handler(seen)) as c:
        c.logs("wl_abc", generation=0)
    assert seen["params"].get("generation") == "0"


def test_a_handle_reads_its_own_logs():
    seen: dict = {}
    with client_with(_log_handler(seen)) as c:
        assert c.get("wl_abc").logs().startswith("step-1")


def test_the_async_client_waits_streams_and_reads_logs_like_the_sync_one():
    """The async half is not a subset: same calls, same names, awaited."""

    async def go():
        seen: dict = {}
        c = nodus.AsyncClient(api_key="nk_live_test", base_url="https://nodus.invalid")
        c._http = httpx.AsyncClient(
            base_url="https://nodus.invalid",
            transport=httpx.MockTransport(_log_handler(seen)),
        )
        async with c:
            wl = await c.wait("wl_abc", poll_seconds=0)
            assert wl.succeeded
            assert (await c.logs("wl_abc")).startswith("step-1")
            assert (await wl.logs()).startswith("step-1")
            assert [e.type async for e in c.stream_events("wl_abc", poll_seconds=0)] == []

    asyncio.run(go())


# -- deliberate membership -------------------------------------------------


def test_the_terminal_sets_are_exported_or_they_are_private():
    assert "TERMINAL" in nodus.__all__ and "TERMINAL_STATUSES" in nodus.__all__
    assert "MEDIA_TAR" in types.__all__
    assert "Meter" in nodus.__all__


def test_everything_exported_actually_exists():
    for name in nodus.__all__:
        assert hasattr(nodus, name), name


def test_the_version_is_read_from_the_package_metadata_not_repeated():
    """Two copies of a version number are one release away from disagreeing."""
    import pathlib
    import re

    root = pathlib.Path(nodus.__file__).parents[1]
    declared = re.search(r'^version = "([^"]+)"', (root / "pyproject.toml").read_text(
        encoding="utf-8"), re.M).group(1)
    src = pathlib.Path(nodus.__file__).read_text(encoding="utf-8")
    assert f'__version__ = "{declared}"' not in src, "the version is declared twice"
    assert "importlib.metadata" in src
    assert nodus.__version__


def test_the_module_docstring_shows_one_idiom():
    doc = nodus.__doc__ or ""
    assert "client.wait(" in doc
    assert "nodus.run(" not in doc
