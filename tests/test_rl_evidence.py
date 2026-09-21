"""Task evidence keeps its replay identity, loss state and grading provenance."""

import asyncio
from copy import deepcopy

import httpx
import pytest

import nodus
from test_rl import async_client, sync_client


PAGE = {
    "schema_version": 1,
    "events": [{
        "id": "9007199254740993", "stage_id": "main", "generation": 2,
        "received_at": "2026-09-21T04:00:00Z",
        "event": {
            "event_id": "evaluation-1", "phase": "evaluation", "task_id": "task-1",
            "attempt": 1, "kind": "task_completed", "outcome": "failed",
            "reward": 0, "duration_ms": 0, "input": "2+3", "output": "4",
            "future_evidence": "preserved",
        },
    }],
    "next_cursor": "opaque_cursor-1", "has_more": True,
    "dropped_events": 3, "truncated": True,
}

GRADING = {
    "schema_version": 1, "workload_id": "wl_rl", "revision": 2,
    "plan_sha256": "a" * 64, "parent_status": "completed",
    "grading_cleanup_complete": False,
    "receipts": [{
        "attempt_id": "attempt-1", "request_id": "request-1", "task_id": "task-1",
        "candidate_sha256": "b" * 64, "plan_sha256": "a" * 64,
        "manifest_sha256": "c" * 64, "state": "completed", "reward": 0,
        "cleanup_complete": False,
    }, {
        "attempt_id": "attempt-2", "request_id": "request-2", "task_id": "task-2",
        "candidate_sha256": "d" * 64, "plan_sha256": "a" * 64,
        "manifest_sha256": "e" * 64, "state": "infrastructure_failure",
        "infrastructure_code": "execution_lost", "cleanup_complete": True,
    }],
}


def exercise(asynchronous, handler, operation):
    if asynchronous:
        async def run():
            async with async_client(handler) as client:
                return await operation(client.rl)
        return asyncio.run(run())
    with sync_client(handler) as client:
        return operation(client.rl)


@pytest.mark.parametrize("asynchronous", [False, True])
def test_rl_evidence_pages_keep_opaque_cursor_and_loss_metadata(asynchronous):
    requests = []
    def handler(request):
        requests.append(request)
        assert request.method == "GET"
        assert request.url.path == "/v1/workloads/wl_rl/rl-events"
        assert request.headers["Authorization"] == "Bearer nk_test"
        assert request.url.params["limit"] == "2"
        if len(requests) == 1:
            assert "after" not in request.url.params
            return httpx.Response(200, json=PAGE)
        assert request.url.params["after"] == "opaque_cursor-1"
        return httpx.Response(200, json={**PAGE, "events": [], "has_more": False})

    first = exercise(asynchronous, handler, lambda rl: rl.events("wl_rl", limit=2))
    second = exercise(asynchronous, handler, lambda rl: rl.events("wl_rl", after=first.next_cursor, limit=2))
    assert isinstance(first, nodus.RLEventPage)
    row = first.events[0]
    assert isinstance(row, nodus.RLEventRow)
    assert row.id == "9007199254740993"
    assert (row.stage_id, row.generation, row.received_at) == ("main", 2, "2026-09-21T04:00:00Z")
    assert isinstance(row.event, nodus.RLEvent)
    assert (row.event.phase, row.event.kind, row.event.outcome) == ("evaluation", "task_completed", "failed")
    assert row.event.reward == 0 and row.event.duration_ms == 0
    assert row.event.raw["future_evidence"] == "preserved"
    assert second.events == [] and not second.has_more
    assert second.next_cursor == first.next_cursor == "opaque_cursor-1"
    assert second.truncated and second.dropped_events == 3
    assert len(requests) == 2


@pytest.mark.parametrize("asynchronous", [False, True])
def test_grading_results_preserve_revision_rewards_and_cleanup(asynchronous):
    def handler(request):
        assert request.method == "GET"
        assert request.url.path == "/v1/workloads/wl_rl/rl-grading-results"
        assert dict(request.url.params) == {"revision": "2"}
        return httpx.Response(200, json=GRADING)
    result = exercise(asynchronous, handler, lambda rl: rl.grading_results("wl_rl", revision=2))
    assert isinstance(result, nodus.RLGradingResults)
    assert (result.workload_id, result.revision, result.plan_sha256) == ("wl_rl", 2, "a" * 64)
    assert result.parent_status == "completed" and not result.grading_cleanup_complete
    measured, lost = result.receipts
    assert isinstance(measured, nodus.RLGradingReceipt)
    assert measured.reward == 0 and not measured.cleanup_complete
    assert lost.reward is None and lost.cleanup_complete
    assert lost.infrastructure_code == "execution_lost"
    assert measured.raw == GRADING["receipts"][0]


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("method,arguments", [
    ("events", {"workload_id": "../secret"}),
    ("events", {"workload_id": "wl_rl", "after": 123}),
    ("events", {"workload_id": "wl_rl", "after": "a" * 65}),
    ("events", {"workload_id": "wl_rl", "limit": True}),
    ("events", {"workload_id": "wl_rl", "limit": 0}),
    ("events", {"workload_id": "wl_rl", "limit": 201}),
    ("grading_results", {"workload_id": "wl_rl", "revision": 0}),
    ("grading_results", {"workload_id": "wl_rl", "revision": True}),
    ("grading_results", {"workload_id": "wl_rl", "revision": 2**31}),
])
def test_invalid_evidence_requests_never_reach_network(asynchronous, method, arguments):
    def handler(request):
        pytest.fail("invalid request reached network")
    with pytest.raises(nodus.ValidationError):
        exercise(asynchronous, handler, lambda rl: getattr(rl, method)(**arguments))


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("field,value", [("next_cursor", None), ("has_more", 0), ("dropped_events", -1), ("events", {})])
def test_malformed_event_pages_do_not_fabricate_complete_evidence(asynchronous, field, value):
    def handler(request):
        return httpx.Response(200, json={**PAGE, field: value})
    with pytest.raises(nodus.APIError):
        exercise(asynchronous, handler, lambda rl: rl.events("wl_rl"))


@pytest.mark.parametrize("asynchronous", [False, True])
def test_started_event_has_no_inferred_score(asynchronous):
    page = deepcopy(PAGE)
    page["events"][0]["event"] = {
        "event_id": "started", "phase": "training", "task_id": "task-2", "attempt": 1,
        "kind": "task_started",
    }
    result = exercise(asynchronous, lambda _: httpx.Response(200, json=page), lambda rl: rl.events("wl_rl"))
    event = result.events[0].event
    assert event.reward is None and event.duration_ms is None and event.outcome is None


@pytest.mark.parametrize("asynchronous", [False, True])
def test_grading_unavailable_is_not_an_empty_successful_snapshot(asynchronous):
    def handler(request):
        return httpx.Response(404, json={"error": "not_found", "message": "No grading plan exists"})
    with pytest.raises(nodus.NotFoundError):
        exercise(asynchronous, handler, lambda rl: rl.grading_results("wl_rl", revision=2))
