"""Managed run recovery status reflects the server's committed state."""

import httpx
import nodus
import pytest


@pytest.mark.parametrize("checkpoint", [
    {"recovery_policy": "checkpoint-v1", "checkpoint_id": "ac_saved", "checkpoint_status": "pending", "last_checkpoint_at": "2026-09-23T12:00:00Z"},
    {"recovery_policy": "checkpoint-v1", "checkpoint_status": "failed", "checkpoint_error": "agent_checkpoint_state_changed"},
    {},
])
def test_run_exposes_saved_state_and_latest_capture_without_inventing_progress(checkpoint):
    def handle(request):
        assert request.method == "GET"
        if request.url.path == "/v1/agents/ag_one":
            return httpx.Response(200, json={"id": "ag_one", "status": "active"})
        assert request.url.path == "/v1/agents/ag_one/runs/run_one"
        return httpx.Response(200, json={"id": "run_one", "agent_id": "ag_one", "status": "running", **checkpoint})
    with nodus.Client(api_key="test", base_url="https://nodus.invalid") as client:
        client._http.close()
        client._http = httpx.Client(base_url="https://nodus.invalid", transport=httpx.MockTransport(handle))
        run = client.agents.get("ag_one").runs.get("run_one")
        for field in ("recovery_policy", "checkpoint_id", "checkpoint_status", "checkpoint_error", "last_checkpoint_at"):
            assert getattr(run, field) == checkpoint.get(field)

@pytest.mark.parametrize("startup", [
    {"stage": "setting_up", "image_preparation_ms": 1800, "image_cache_reads": 3, "image_cache_hits": 2, "warm_miss_reason": "shared_session_state"},
    {"stage": "ready", "warm_claimed": True},
    None,
])
def test_run_exposes_observed_startup_without_inventing_missing_timings(startup):
    def handle(request):
        if request.url.path == "/v1/agents/ag_one":
            return httpx.Response(200, json={"id": "ag_one", "status": "active"})
        return httpx.Response(200, json={"id": "run_one", "agent_id": "ag_one", "status": "starting", **({"startup": startup} if startup is not None else {})})
    with nodus.Client(api_key="test", base_url="https://nodus.invalid") as client:
        client._http.close()
        client._http = httpx.Client(base_url="https://nodus.invalid", transport=httpx.MockTransport(handle))
        assert client.agents.get("ag_one").runs.get("run_one").startup == startup


def test_sandbox_exposes_observed_startup_without_waking_compute():
    startup = {"stage": "sleeping", "image_preparation_ms": 1700, "project_prefetched": True}
    requests = []
    def handle(request):
        requests.append((request.method, request.url.path))
        return httpx.Response(200, json={"id": "sb_one", "state": "suspended", "startup": startup})
    with nodus.Client(api_key="test", base_url="https://nodus.invalid") as client:
        client._http.close()
        client._http = httpx.Client(base_url="https://nodus.invalid", transport=httpx.MockTransport(handle))
        assert client.sandboxes.from_id("sb_one").startup == startup
    assert requests == [("GET", "/v1/sandboxes/sb_one")]
