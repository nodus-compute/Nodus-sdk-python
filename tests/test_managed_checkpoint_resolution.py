"""Verified external outcomes retain the customer's explicit recovery state."""

import json

import httpx
import pytest

import nodus
from nodus._managed_agents import AsyncManagedRun, ManagedRun


def resolution():
    return dict(step_id="charge:1", expected_revision=3, decision="completed",
                reason="Verified external receipt and saved files", evidence_digest="sha256:" + "a" * 64,
                result={"receipt": "remote-1"}, checkpoint_id="ac_saved-state", idempotency_key="reconcile-once")


def client_for(handler):
    client = nodus.Client(api_key="test", base_url="https://nodus.invalid", max_retries=0)
    client._http.close()
    client._http = httpx.Client(base_url="https://nodus.invalid", transport=httpx.MockTransport(handler))
    return client


def test_managed_resolution_sends_explicit_checkpoint_with_verified_result():
    calls = []
    def handle(request):
        calls.append(request)
        body = json.loads(request.content)
        assert body["checkpoint_id"] == "ac_saved-state"
        assert body["step_id"] == "charge:1"
        assert body["expected_revision"] == 3
        assert request.headers["Idempotency-Key"] == "reconcile-once"
        return httpx.Response(200, json={"decision": "replay", "step_id": "charge:1"})
    with client_for(handle) as client:
        run = ManagedRun(client, {"id": "run_one", "agent_id": "ag_one"})
        assert run.resolve(**resolution())["decision"] == "replay"
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_async_managed_resolution_sends_explicit_checkpoint():
    calls = []
    def handle(request):
        calls.append(request)
        assert json.loads(request.content)["checkpoint_id"] == "ac_saved-state"
        return httpx.Response(200, json={"decision": "replay"})
    client = nodus.AsyncClient(api_key="test", base_url="https://nodus.invalid", max_retries=0)
    await client._http.aclose()
    client._http = httpx.AsyncClient(base_url="https://nodus.invalid", transport=httpx.MockTransport(handle))
    async with client:
        run = AsyncManagedRun(client, {"id": "run_one", "agent_id": "ag_one"})
        assert (await run.resolve(**resolution()))["decision"] == "replay"
    assert len(calls) == 1


@pytest.mark.parametrize("checkpoint,decision", [("../outside", "completed"), ("bad\nstate", "completed"), ("", "completed"), ("ac_saved-state", "no_effect")])
def test_invalid_state_selection_never_reaches_http(checkpoint, decision):
    with client_for(lambda request: pytest.fail("invalid resolution reached HTTP")) as client:
        run = ManagedRun(client, {"id": "run_one", "agent_id": "ag_one"})
        options = resolution()
        options.update(checkpoint_id=checkpoint, decision=decision)
        with pytest.raises(nodus.ValidationError):
            run.resolve(**options)


def test_managed_cli_resolution_preserves_state_selection(monkeypatch, capsys):
    from nodus import cli
    calls = []
    def handle(request):
        if request.method == "GET":
            if "/runs/" in request.url.path:
                return httpx.Response(200, json={"id": "run_one", "agent_id": "ag_one", "status": "blocked"})
            return httpx.Response(200, json={"id": "ag_one", "status": "active"})
        calls.append(json.loads(request.content))
        return httpx.Response(200, json={"decision": "replay"})
    monkeypatch.setattr(cli, "Client", lambda **kwargs: client_for(handle))
    assert cli.main(["agent", "resolve", "ag_one", "run_one", "--step-id", "charge:1",
                     "--expected-revision", "3", "--decision", "completed", "--reason", "Verified receipt and files",
                     "--evidence-digest", "sha256:" + "a" * 64, "--result", '{"receipt":"remote-1"}',
                     "--checkpoint-id", "ac_saved-state", "--idempotency-key", "reconcile-once"]) == 0
    assert len(calls) == 1 and calls[0]["checkpoint_id"] == "ac_saved-state"
    assert "replay" in capsys.readouterr().out
