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

WORKLOAD = {
    "id": "wl_abc",
    "status": "completed",
    "spend_usd": 291.4,
    "revision": 1,
    "created_at": "2026-07-27T10:00:00Z",
    "updated_at": "2026-07-27T11:00:00Z",
    "payload": {"outcome": {"max_cost_usd": 400}},
    "route": {
        "offer_id": "nodus:a100-80-us-east",
        "compute_class": "accelerator",
        "fit_class": "a100-80",
        "region": "us-east",
        "expected_cost_usd": 300.0,
        "expected_hours": 18.0,
        "interruptible": True,
    },
    "stages": [
        {"id": "main", "status": "completed", "continuity_mode": "checkpointed",
         "completed_units": 4, "total_units": 4}
    ],
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
    assert build_payload()["continuity"]["mode"] == "checkpointed"
    eph = build_payload(continuity="ephemeral")["continuity"]
    assert eph == {"mode": "ephemeral", "resume_on_interruption": False}


def test_enums_and_strings_are_interchangeable():
    a = build_payload(continuity=nodus.ContinuityMode.RESTARTABLE,
                      interrupt_tolerance=nodus.InterruptTolerance.HIGH)
    b = build_payload(continuity="restartable", interrupt_tolerance="high")
    assert a == b


def test_status_filter_accepts_presets_members_and_lists():
    assert status_filter("active") == "active"
    assert status_filter(nodus.WorkloadStatus.RUNNING) == "running"
    assert status_filter([nodus.WorkloadStatus.FAILED, "cancelled"]) == "failed,cancelled"
    assert status_filter(None) is None


def test_stages_replace_the_single_source():
    p = build_payload(stages=[{"id": "prepare"}, {"id": "train", "depends_on": ["prepare"]}])
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
    assert build_payload(command=["python", "train.py"])["source"]["image"] == DEFAULT_IMAGE


def test_an_image_measured_to_ship_no_fetch_tool_warns_before_submission():
    """The warning has to arrive before a host is rented, not after it bills."""
    with pytest.warns(UserWarning, match="curl"):
        p = build_payload(image="ubuntu:22.04", command=["python", "train.py"])
    assert p["source"]["image"] == "ubuntu:22.04", "the brief is the caller's, not ours to rewrite"


def test_a_stage_naming_that_image_warns_too():
    """A staged brief rents the same hosts; the image lives one level down."""
    with pytest.warns(UserWarning, match="curl"):
        build_payload(stages=[{"id": "train", "source": {"image": "ubuntu:22.04"}}])


def test_an_unmeasured_image_is_not_second_guessed():
    """Silence about images nobody measured: a guess would train callers to ignore it."""
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        build_payload(image="registry.example.com/team/trainer:2026-09", command=["./go"])


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


def test_caller_supplied_idempotency_key_is_used_verbatim():
    """The docs tell people to pass a stable key for cross-call retries."""
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["key"] = request.headers.get("Idempotency-Key")
        return httpx.Response(202, json=WORKLOAD)

    with client_with(handler) as c:
        c.run(model="x", idempotency_key="nightly-eval-2026-07-27")
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
    ships, because that is what the Go client sends too.
    """
    outcome = _submitted_outcome(model="7B fine-tune", command=["python", "train.py"])
    assert "max_cost_usd" not in outcome, f"invented a ceiling: {outcome}"
    assert outcome == {}


def test_run_sends_an_explicit_budget_as_the_cost_ceiling():
    """Either spelling reaches the wire, with ``max_cost_usd`` winning."""
    assert _submitted_outcome(model="x", budget=25)["max_cost_usd"] == 25.0
    assert _submitted_outcome(model="x", max_cost_usd=40, budget=25)["max_cost_usd"] == 40.0


def test_workload_exposes_the_documented_attributes():
    with client_with(lambda r: httpx.Response(200, json=WORKLOAD)) as c:
        wl = c.get("wl_abc")

    assert wl.status is nodus.WorkloadStatus.COMPLETED
    assert wl.is_terminal and wl.succeeded
    assert wl.spend_usd == 291.4
    assert wl.budget_usd == 400.0
    assert wl.route.sku == "nodus:a100-80-us-east"
    assert wl.route.compute_class is nodus.ComputeClass.ACCELERATOR
    assert wl.route.offer_id == wl.route.sku  # wire-name alias
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


def test_ledger_parses_entries_and_settlement():
    payload = {
        "entries": [{"id": "le_1", "entry_type": "customer_charge", "debit_usd": 5.0}],
        "settlement": {"status": "settled", "total_usd": 5.0},
    }
    with client_with(lambda r: httpx.Response(200, json=payload)) as c:
        led = c.ledger("wl_abc")
    assert led.entries[0].debit_usd == 5.0
    assert led.settlement.status == "settled"


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


def test_idempotency_conflict_is_not_retried():
    """Retrying a 409 cannot help: the key already names a different payload."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(409, json={"error": "idempotency_conflict"})

    with client_with(handler, max_retries=3) as c:
        with pytest.raises(nodus.IdempotencyConflictError):
            c.run(model="x")
    assert calls["n"] == 1


# -- async -----------------------------------------------------------------


def test_async_client_mirrors_the_sync_surface():
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
              "error", "is_terminal", "succeeded", "created_at", "updated_at"}
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
