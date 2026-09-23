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
