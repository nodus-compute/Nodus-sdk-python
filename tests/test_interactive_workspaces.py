import asyncio
import json

import httpx
import pytest

import nodus
from test_workspace_connection_retry import API_KEY, async_client, sync_client


BASE = "/v1/research-workspaces"
CONFIG = {
    "name": "kernel-lab", "environment": "pytorch-cuda", "editor": "jupyter",
    "gpu": "H100", "gpu_count": 4, "gpu_memory_gb": 80,
    "budget_usd": 8, "max_hours": 2, "size_gb": 0.25,
}
RECORD = {"id": "ws_lab", "name": "kernel-lab", "configuration": CONFIG, "state": "stopped"}
RUN = {"id": "wl_lab", "workload_id": "wl_lab", "status": "accepted", "revision": 1}


def invoke(handler, asynchronous, method, *args, **kwargs):
    if asynchronous:
        async def run():
            async with await async_client(handler) as client:
                return await getattr(client.workspaces, method)(*args, **kwargs)
        return asyncio.run(run())
    with sync_client(handler) as client:
        return getattr(client.workspaces, method)(*args, **kwargs)


@pytest.mark.parametrize("asynchronous", [False, True])
def test_create_interactive_preserves_explicit_gpu_environment_and_limits(asynchronous):
    calls = []
    def handler(request):
        calls.append(request)
        assert request.url.path == BASE
        assert request.method == "POST"
        assert request.headers["Authorization"] == "Bearer " + API_KEY
        assert json.loads(request.content) == CONFIG
        return httpx.Response(201, json=RECORD)
    assert invoke(handler, asynchronous, "create_interactive", **CONFIG) == RECORD
    assert len(calls) == 1


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("method,args,kwargs,path,verb,payload,response", [
    ("capabilities", (), {}, BASE + "/capabilities", "GET", None, {"editors": ["vscode", "jupyter", "ssh"]}),
    ("list_interactive", (), {}, BASE, "GET", None, {"workspaces": [RECORD]}),
    ("get", ("ws_lab",), {}, BASE + "/ws_lab", "GET", None, RECORD),
    ("start", ("ws_lab",), {"idempotency_key": "start-once"}, BASE + "/ws_lab/start", "POST", {}, RECORD),
    ("stop", ("ws_lab",), {"session_id": "sb_original", "idempotency_key": "stop-once"}, BASE + "/ws_lab/stop", "POST", {"session_id": "sb_original"}, RECORD),
    ("connect", ("ws_lab",), {"tool": "notebook"}, BASE + "/ws_lab/connections", "POST", {"tool": "notebook"}, {"url": "https://notebook.example/connect"}),
    ("workloads", ("ws_lab",), {}, BASE + "/ws_lab/workloads", "GET", None, {"workloads": [{"id": "wl_lab", "status": "running", "created_at": "2026-09-20T12:00:00Z", "source_revision": "a" * 64}]}),
])
def test_interactive_lifecycle_uses_the_owned_wire_contract(asynchronous, method, args, kwargs, path, verb, payload, response):
    calls = []
    def handler(request):
        calls.append(request)
        assert request.method == verb
        assert request.url.path == path
        assert request.headers["Authorization"] == "Bearer " + API_KEY
        assert request.headers.get("Idempotency-Key") == kwargs.get("idempotency_key")
        if payload is not None:
            assert json.loads(request.content) == payload
        return httpx.Response(200, json=response)
    result = invoke(handler, asynchronous, method, *args, **kwargs)
    assert result == response.get("workspaces", response.get("workloads", response))
    assert len(calls) == 1


@pytest.mark.parametrize("asynchronous", [False, True])
def test_submit_inherits_project_resources_and_returns_an_observable_workload(asynchronous):
    calls = []
    def handler(request):
        calls.append(request)
        assert request.url.path == BASE + "/ws_lab/workloads"
        assert request.headers["Idempotency-Key"] == "training-attempt-1"
        assert request.headers["Authorization"] == "Bearer " + API_KEY
        assert json.loads(request.content) == {"command": "python train.py", "budget_usd": 12}
        return httpx.Response(202, json=RUN, headers={"Idempotent-Replayed": "true"})
    run = invoke(handler, asynchronous, "submit", "ws_lab", command="python train.py", budget_usd=12, idempotency_key="training-attempt-1")
    assert isinstance(run, nodus.AsyncWorkload if asynchronous else nodus.Workload)
    assert run.id == "wl_lab"
    assert run.status == "accepted"
    assert run.replayed is True
    assert len(calls) == 1


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("response", ["pending", "unavailable", "timeout"])
def test_paid_submission_sends_once_and_preserves_refusal_and_retry_identity(asynchronous, response):
    calls = []
    def handler(request):
        calls.append(request)
        if response == "timeout":
            raise httpx.ReadTimeout("synthetic lost reply", request=request)
        if response == "pending":
            return httpx.Response(409, json={"error": "workspace_save_pending", "message": "Saving project files."}, headers={"Retry-After": "3"})
        return httpx.Response(503, json={"error": "workspace_submission_unavailable", "message": "Unavailable."})
    with pytest.raises(nodus.NodusError) as error:
        invoke(handler, asynchronous, "submit", "ws_lab", command="python train.py", budget_usd=12, idempotency_key="same-operation")
    assert len(calls) == 1
    assert calls[0].headers["Idempotency-Key"] == "same-operation"
    if response == "pending":
        assert error.value.code == "workspace_save_pending"
        assert not isinstance(error.value, nodus.IdempotencyConflictError)
    if response == "timeout":
        assert error.value.body["idempotency_key"] == "same-operation"


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("change", [
    {"workspace_id": "../credentials"}, {"workspace_id": "ws_lab?x=y"},
    {"budget_usd": 0}, {"budget_usd": float("nan")}, {"budget_usd": float("inf")}, {"budget_usd": True},
    {"idempotency_key": ""}, {"idempotency_key": "key\r\nInjected: yes"},
    {"command": ""}, {"command": "python\x00train.py"}, {"gpu_count": 3}, {"gpu_count": True},
    {"gpu_memory_gb": -1},
])
def test_invalid_submission_cannot_reach_transport(asynchronous, change):
    calls = []
    arguments = {"workspace_id": "ws_lab", "command": "python train.py", "budget_usd": 2, "idempotency_key": "safe", **change}
    with pytest.raises(nodus.ValidationError):
        invoke(lambda request: calls.append(request), asynchronous, "submit", **arguments)
    assert calls == []


@pytest.mark.parametrize("asynchronous", [False, True])
def test_ssh_only_creation_requires_a_public_key_before_transport(asynchronous):
    calls = []
    with pytest.raises(nodus.ValidationError):
        invoke(lambda request: calls.append(request), asynchronous, "create_interactive", **{**CONFIG, "editor": "ssh"})
    assert calls == []


@pytest.mark.parametrize("asynchronous", [False, True])
def test_account_storage_preserves_server_values_without_computing_charges(asynchronous):
    response = {"policy_version": "r2-standard-10gb-account-v1",
                "included_bytes": 10000000000, "retained_bytes": 12000000000,
                "billable_bytes": 2000000000, "rate_usd_gb_month": 0.015,
                "charged_usd": 0.0007, "status": "funded", "as_of": "2026-09-20T12:00:00Z"}
    def handler(request):
        assert request.method == "GET"
        assert request.url.path == BASE + "/storage"
        assert request.headers["Authorization"] == "Bearer " + API_KEY
        return httpx.Response(200, json=response)
    assert invoke(handler, asynchronous, "storage") == response


@pytest.mark.parametrize("asynchronous", [False, True])
def test_rejected_workspace_source_is_not_an_idempotency_conflict(asynchronous):
    def handler(request):
        return httpx.Response(409, json={"error": "workspace_source_rejected", "message": "Project exceeds source limits."})
    with pytest.raises(nodus.APIError) as error:
        invoke(handler, asynchronous, "submit", "ws_lab", command="python train.py", budget_usd=2, idempotency_key="source-rejected")
    assert not isinstance(error.value, nodus.IdempotencyConflictError)
    assert error.value.code == "workspace_source_rejected"
