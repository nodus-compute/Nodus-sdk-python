"""Versioned operations preserve workload authority, receipts and retry identity."""

import asyncio
import json

import httpx
import pytest

import nodus


ORIGIN = "https://nodus.invalid"
WORKLOAD = {"source": {"image": "python:3.12", "command": ["python", "train.py"]},
            "outcome": {"max_cost_usd": 3}}
SUBMITTED = {"id": "wl_new", "workload_id": "wl_new", "status": "accepted", "revision": 1}


def exercise(asynchronous, handler, call, *, max_retries=0):
    if asynchronous:
        async def run():
            async with nodus.AsyncClient(api_key="nk_test", base_url=ORIGIN, max_retries=max_retries) as client:
                await client._http.aclose()
                client._http = httpx.AsyncClient(base_url=ORIGIN, headers={"Authorization": "Bearer nk_test"},
                                               transport=httpx.MockTransport(handler))
                return await call(client.operations)
        return asyncio.run(run())
    with nodus.Client(api_key="nk_test", base_url=ORIGIN, max_retries=max_retries) as client:
        client._http.close()
        client._http = httpx.Client(base_url=ORIGIN, headers={"Authorization": "Bearer nk_test"},
                                   transport=httpx.MockTransport(handler))
        return call(client.operations)


@pytest.mark.parametrize("asynchronous", [False, True])
def test_catalog_uses_server_definitions(asynchronous):
    definition = {"id": "workloads.get", "version": "v1", "name": "get_workload",
                  "description": "Get workload status and current meter.",
                  "inputSchema": {"type": "object", "required": ["workload_id"]},
                  "annotations": {"readOnlyHint": True}, "required_scope": "workloads:read",
                  "transports": ["api", "hosted_mcp"]}

    def handler(request):
        assert (request.method, request.url.path) == ("GET", "/v1/operations/v1")
        assert request.headers["Authorization"] == "Bearer nk_test"
        return httpx.Response(200, json={"version": "v1", "operations": [definition]})

    catalog = exercise(asynchronous, handler, lambda operations: operations.catalog())
    assert catalog.version == "v1"
    assert catalog.operations[0].id == "workloads.get"
    assert catalog.operations[0].input_schema == definition["inputSchema"]
    assert catalog.operations[0].required_scope == "workloads:read"


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("method,arguments,wire,response", [
    ("list", {"scope": "mine", "limit": 2, "offset": 5}, {"scope": "mine", "limit": 2, "offset": 5},
     {"workloads": [{"id": "wl_existing", "status": "running"}], "next_offset": 7}),
    ("get", {"workload_id": "wl_existing"}, {"workload_id": "wl_existing"},
     {"id": "wl_existing", "status": "running"}),
    ("events", {"workload_id": "wl_existing", "after": 0}, {"workload_id": "wl_existing", "after": 0},
     {"events": [{"id": 42, "event_id": "evt_1", "event_type": "workload.running", "payload": {}}]}),
    ("outputs", {"workload_id": "wl_existing"}, {"workload_id": "wl_existing"},
     {"workload_id": "wl_existing", "outputs": [{"name": "result.json", "stage_id": "main", "bytes": 3,
       "sha256": "a" * 64, "download": "/v1/workloads/wl_existing/outputs/result.json?stage=main"}]}),
    ("validate", {"workload": WORKLOAD}, {"workload": WORKLOAD},
     {"valid": True, "submitted": False, "workload": WORKLOAD,
      "message": "Request validated. No compute started. Capacity and billing are checked at submission."}),
    ("submit", {"workload": WORKLOAD, "idempotency_key": "same-run"},
     {"workload": WORKLOAD, "idempotency_key": "same-run"}, SUBMITTED),
    ("cancel", {"workload_id": "wl_existing"}, {"workload_id": "wl_existing"}, {"status": "cancel_requested"}),
])
def test_typed_operations_use_canonical_wire_contract(asynchronous, method, arguments, wire, response):
    def handler(request):
        assert (request.method, request.url.path) == ("POST", "/v1/operations/v1/workloads." + method)
        assert json.loads(request.content) == wire
        if method == "submit":
            assert request.headers["Idempotency-Key"] == "same-run"
        return httpx.Response(202 if method in {"submit", "cancel"} else 200, json=response,
                              headers={"Idempotent-Replayed": "true"} if method == "submit" else {})

    result = exercise(asynchronous, handler, lambda operations: getattr(operations, method)(**arguments))
    if method == "list":
        assert result.next_offset == 7
        assert result.workloads[0].id == "wl_existing"
        assert isinstance(result.workloads[0], nodus.AsyncWorkload if asynchronous else nodus.Workload)
    elif method in {"get", "submit"}:
        assert isinstance(result, nodus.AsyncWorkload if asynchronous else nodus.Workload)
        assert result.id == ("wl_new" if method == "submit" else "wl_existing")
        if method == "submit":
            assert result.replayed is True
    elif method == "events":
        assert isinstance(result[0], nodus.Event)
        assert result[0].seq == 42 and result[0].type == "workload.running"
    elif method == "outputs":
        assert isinstance(result[0], nodus.Output)
        assert result[0].sha256 == "a" * 64 and result[0].bytes == 3
    elif method == "validate":
        assert result.valid is True and result.submitted is False and result.workload == WORKLOAD
    else:
        assert result is None


@pytest.mark.parametrize("asynchronous", [False, True])
def test_default_list_keeps_server_scope_and_cursor_defaults(asynchronous):
    def handler(request):
        assert json.loads(request.content) == {}
        return httpx.Response(200, json={"workloads": []})
    result = exercise(asynchronous, handler, lambda operations: operations.list())
    assert result.workloads == [] and result.next_offset is None


@pytest.mark.parametrize("asynchronous", [False, True])
def test_logs_are_text(asynchronous):
    def handler(request):
        assert request.url.path == "/v1/operations/v1/workloads.logs"
        assert json.loads(request.content) == {"workload_id": "wl_existing"}
        return httpx.Response(200, text="step=5 loss=0.2\n")
    assert exercise(asynchronous, handler, lambda operations: operations.logs("wl_existing")) == "step=5 loss=0.2\n"


@pytest.mark.parametrize("asynchronous", [False, True])
def test_retry_freezes_paid_request_and_retains_key(asynchronous, monkeypatch):
    body = json.loads(json.dumps(WORKLOAD))
    sent = []

    def handler(request):
        sent.append(request)
        if len(sent) == 1:
            body["outcome"]["max_cost_usd"] = 300
            body["source"]["command"].append("--changed")
            return httpx.Response(503, json={"error": {"code": "spend_check_unavailable", "message": "Retry"}})
        return httpx.Response(202, json=SUBMITTED)

    monkeypatch.setattr(nodus._Transport, "_backoff", staticmethod(lambda attempt, response: 0))
    result = exercise(asynchronous, handler,
                      lambda operations: operations.submit(body, idempotency_key="same-paid-intent"), max_retries=1)
    assert result.id == "wl_new" and len(sent) == 2
    assert sent[0].content == sent[1].content
    assert json.loads(sent[1].content)["workload"] == WORKLOAD
    assert {request.headers["Idempotency-Key"] for request in sent} == {"same-paid-intent"}


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("receipt", [{}, [], {"id": "../other"}, {"id": "wl_one", "workload_id": "wl_two"}])
def test_invalid_paid_receipt_preserves_retry_identity(asynchronous, receipt):
    with pytest.raises(nodus.APIError) as failure:
        exercise(asynchronous, lambda request: httpx.Response(202, json=receipt),
                 lambda operations: operations.submit(WORKLOAD, idempotency_key="recover-this-run"))
    assert failure.value.payload["idempotency_key"] == "recover-this-run"


@pytest.mark.parametrize("asynchronous", [False, True])
def test_uncertain_submission_does_not_lose_retry_identity(asynchronous):
    def handler(request):
        raise httpx.ReadTimeout("reply lost", request=request)
    with pytest.raises(nodus.APITimeoutError) as failure:
        exercise(asynchronous, handler, lambda operations: operations.submit(WORKLOAD, idempotency_key="recover-timeout"))
    assert failure.value.payload["idempotency_key"] == "recover-timeout"


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_redirect_cannot_become_a_paid_admission_receipt(asynchronous, status):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(status, json={"id": "wl_not_admitted", "status": "accepted"},
                              headers={"Location": "https://another.invalid/collect"})

    with pytest.raises(nodus.APIError) as failure:
        exercise(asynchronous, handler, lambda operations: operations.submit(
            WORKLOAD, idempotency_key="recover-redirect"), max_retries=2)
    assert failure.value.payload["idempotency_key"] == "recover-redirect"
    assert len(requests) == 1 and requests[0].url.host == "nodus.invalid"


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("key", ["", "line\r\nbreak", "two words"])
def test_invalid_retry_key_never_reaches_transport(asynchronous, key):
    requests = []
    with pytest.raises((ValueError, nodus.ValidationError)):
        exercise(asynchronous, lambda request: requests.append(request),
                 lambda operations: operations.submit(WORKLOAD, idempotency_key=key))
    assert requests == []


@pytest.mark.parametrize("asynchronous", [False, True])
def test_budget_policy_is_server_authoritative_without_invented_values(asynchronous):
    requests = []

    def handler(request):
        requests.append(request)
        assert json.loads(request.content) == {"workload": {"source": {"command": ["train"]}}, "idempotency_key": "budget-check"}
        return httpx.Response(400, json={"error": {"code": "invalid_operation", "message": "Provide an explicit positive outcome.max_cost_usd in the workload"}})

    with pytest.raises(nodus.ValidationError):
        exercise(asynchronous, handler, lambda operations: operations.submit(
            {"source": {"command": ["train"]}}, idempotency_key="budget-check"), max_retries=2)
    assert len(requests) == 1


@pytest.mark.parametrize("asynchronous", [False, True])
def test_catalog_version_mismatch_is_rejected(asynchronous):
    with pytest.raises(nodus.APIError):
        exercise(asynchronous, lambda request: httpx.Response(200, json={"version": "v2", "operations": []}),
                 lambda operations: operations.catalog())


@pytest.mark.parametrize("asynchronous", [False, True])
def test_validation_does_not_claim_a_submitted_run_is_a_dry_run(asynchronous):
    with pytest.raises(nodus.APIError):
        exercise(asynchronous, lambda request: httpx.Response(200, json={"valid": True, "submitted": True, "workload": WORKLOAD}),
                 lambda operations: operations.validate(WORKLOAD))
