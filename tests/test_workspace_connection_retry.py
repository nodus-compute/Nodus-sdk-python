import asyncio
import json

import httpx
import pytest

import nodus
from nodus.errors import error_from_response


BASE_URL = "https://nodus.invalid"
API_KEY = "nk_synthetic_connection_retry"
ERROR_URL = "https://github.com/nodus-compute/Nodus-sdk-python/tree/main/docs"


def sync_client(handler, *, max_retries=4):
    client = nodus.Client(api_key=API_KEY, base_url=BASE_URL, max_retries=max_retries)
    headers = dict(client._http.headers)
    client._http.close()
    client._http = httpx.Client(
        base_url=BASE_URL,
        headers=headers,
        transport=httpx.MockTransport(handler),
    )
    return client


async def async_client(handler, *, max_retries=4):
    client = nodus.AsyncClient(api_key=API_KEY, base_url=BASE_URL, max_retries=max_retries)
    headers = dict(client._http.headers)
    await client._http.aclose()
    client._http = httpx.AsyncClient(
        base_url=BASE_URL,
        headers=headers,
        transport=httpx.MockTransport(handler),
    )
    return client


def assert_retry_request(request, tool):
    assert request.method == "POST"
    assert request.url.path == "/v1/research-workspaces/ws_1234-abcd/connections/retry"
    assert request.headers["Authorization"] == f"Bearer {API_KEY}"
    assert "Idempotency-Key" not in request.headers
    assert request.content == f'{{"tool":"{tool}"}}'.encode()
    assert json.loads(request.content) == {"tool": tool}


@pytest.mark.parametrize("tool", ["editor", "notebook"])
def test_sync_retry_connection_sends_exact_explicit_request(tool):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(202, json={"status": "retry_scheduled"})

    with sync_client(handler) as client:
        result = client.workspaces.retry_connection("ws_1234-abcd", tool=tool)

    assert result == {"status": "retry_scheduled"}
    assert len(requests) == 1
    assert_retry_request(requests[0], tool)


@pytest.mark.parametrize("tool", ["editor", "notebook"])
def test_async_retry_connection_sends_exact_explicit_request(tool):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(202, json={"status": "retry_scheduled"})

    async def scenario():
        client = await async_client(handler)
        async with client:
            return await client.workspaces.retry_connection("ws_1234-abcd", tool=tool)

    assert asyncio.run(scenario()) == {"status": "retry_scheduled"}
    assert len(requests) == 1
    assert_retry_request(requests[0], tool)


def test_retry_connection_accepts_a_256_character_opaque_id():
    workspace_id = "a" * 256
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(202, json={"status": "retry_scheduled"})

    with sync_client(handler) as client:
        assert client.workspaces.retry_connection(workspace_id, tool="editor") == {
            "status": "retry_scheduled"
        }

    assert len(requests) == 1
    assert requests[0].url.path == (
        f"/v1/research-workspaces/{workspace_id}/connections/retry"
    )


@pytest.mark.parametrize(
    "workspace_id",
    [
        "",
        " ",
        ".",
        "..",
        "../private",
        "ws/private",
        "ws?tool=editor",
        "ws#fragment",
        "ws%2fprivate",
        "ws\nheader",
        "café",
        42,
        None,
        "a" * 257,
    ],
)
def test_retry_connection_rejects_hostile_workspace_ids_without_transport(workspace_id):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(202, json={"status": "retry_scheduled"})

    with sync_client(handler) as client:
        with pytest.raises(nodus.ValidationError) as caught:
            client.workspaces.retry_connection(workspace_id, tool="editor")

    assert requests == []
    assert str(caught.value) == "Use a research workspace ID returned by Nodus"


@pytest.mark.parametrize("tool", [None, "", "EDITOR", " editor", "notebook ", "browser", 7])
def test_retry_connection_rejects_invalid_tools_without_transport(tool):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(202, json={"status": "retry_scheduled"})

    with sync_client(handler) as client:
        with pytest.raises(nodus.ValidationError) as caught:
            client.workspaces.retry_connection("ws_safe", tool=tool)

    assert requests == []
    assert str(caught.value) == "tool must be editor or notebook"


def test_async_retry_connection_validates_before_transport():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(202, json={"status": "retry_scheduled"})

    async def scenario():
        client = await async_client(handler)
        async with client:
            with pytest.raises(nodus.ValidationError, match="research workspace ID"):
                await client.workspaces.retry_connection("../private", tool="editor")
            with pytest.raises(nodus.ValidationError, match="editor or notebook"):
                await client.workspaces.retry_connection("ws_safe", tool="EDITOR")

    asyncio.run(scenario())
    assert requests == []


def test_connection_not_retryable_is_not_an_idempotency_conflict():
    refused = error_from_response(
        "POST",
        "/v1/research-workspaces/ws_safe/connections/retry",
        409,
        {
            "code": "connection_not_retryable",
            "message": "connection cannot be retried",
            "fix": "Observe the workspace and retry only after a retryable failure.",
            "url": ERROR_URL,
        },
    )
    unrelated = error_from_response(
        "POST",
        "/v1/workloads",
        409,
        {"error": "idempotency_conflict", "message": "key names another request"},
    )

    assert type(refused) is nodus.APIError
    assert refused.code == "connection_not_retryable"
    assert refused.status_code == 409
    assert isinstance(unrelated, nodus.IdempotencyConflictError)


@pytest.mark.parametrize(
    "body,expected",
    [
        ({"error": "legacy", "code": "current"}, "legacy"),
        ({"code": "current"}, "current"),
        ({}, None),
        ({"code": 7}, None),
        ({"error": 7, "code": "current"}, "current"),
    ],
)
def test_error_code_prefers_legacy_error_then_string_code(body, expected):
    assert nodus.APIError("synthetic", body=body).code == expected


@pytest.mark.parametrize(
    "status,body,error_type",
    [
        (409, {"code": "connection_not_retryable", "message": "cannot retry", "fix": "Observe it.", "url": ERROR_URL}, nodus.APIError),
        (503, {"code": "connection_unavailable", "message": "unavailable", "fix": "Observe it.", "url": ERROR_URL}, nodus.APIError),
        (404, {"code": "not_found", "message": "workspace not found", "fix": "Use its ID.", "url": ERROR_URL}, nodus.NotFoundError),
        (500, {"code": "connection_retry_failed", "message": "retry failed", "fix": "Try later.", "url": ERROR_URL}, nodus.APIError),
    ],
)
def test_retry_connection_preserves_server_refusals(status, body, error_type):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(status, json=body)

    with sync_client(handler) as client:
        with pytest.raises(error_type) as caught:
            client.workspaces.retry_connection("ws_safe", tool="notebook")

    assert len(requests) == 1
    assert caught.value.body == body
    assert caught.value.status_code == status
    if body["code"] == "connection_not_retryable":
        assert not isinstance(caught.value, nodus.IdempotencyConflictError)


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("failure", ["503", "transport"])
def test_retry_connection_never_repeats_ambiguous_failures(asynchronous, failure):
    requests = []

    def handler(request):
        requests.append(request)
        if failure == "transport":
            raise httpx.ReadError("synthetic connection lost", request=request)
        return httpx.Response(
            503,
            json={
                "code": "connection_unavailable",
                "message": "connection unavailable",
                "fix": "Observe the workspace before retrying.",
                "url": ERROR_URL,
            },
        )

    expected = nodus.APIConnectionError if failure == "transport" else nodus.APIError

    if asynchronous:
        async def scenario():
            client = await async_client(handler, max_retries=5)
            async with client:
                with pytest.raises(expected):
                    await client.workspaces.retry_connection("ws_safe", tool="editor")

        asyncio.run(scenario())
    else:
        with sync_client(handler, max_retries=5) as client:
            with pytest.raises(expected):
                client.workspaces.retry_connection("ws_safe", tool="editor")

    assert len(requests) == 1


def test_async_retry_connection_propagates_cancellation_without_repeating():
    requests = []

    async def handler(request):
        requests.append(request)
        raise asyncio.CancelledError

    async def scenario():
        client = await async_client(handler, max_retries=5)
        async with client:
            with pytest.raises(asyncio.CancelledError):
                await client.workspaces.retry_connection("ws_safe", tool="editor")

    asyncio.run(scenario())
    assert len(requests) == 1
