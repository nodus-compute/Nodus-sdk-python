"""Uncertain sandbox mutations retain the identity needed for a safe retry."""

import asyncio

import httpx
import pytest

import nodus


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("operation", ["create", "exec", "terminate"])
@pytest.mark.parametrize("idempotency_key", [None, "customer-attempt"])
@pytest.mark.parametrize("body", [None, {}, [], {"unexpected": "ok"},
                                  {"id": ""}, {"id": 42}, {"id": "untrusted;echo injected"}])
def test_invalid_mutation_receipt_retains_sent_key(asynchronous, operation, idempotency_key, body):
    exercise_invalid_receipt(asynchronous, operation, idempotency_key, body)


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("operation,body", [
    ("terminate", {"id": "sb_different", "state": "terminated"}),
    ("exec", {"id": "ex_test", "sandbox_id": "sb_different", "state": "queued"}),
])
def test_mutation_receipt_cannot_change_existing_identity(asynchronous, operation, body):
    exercise_invalid_receipt(asynchronous, operation, "customer-attempt", body)


def exercise_invalid_receipt(asynchronous, operation, idempotency_key, body):
    requests = []
    paths = {"create": "/v1/sandboxes", "exec": "/v1/sandboxes/sb_test/exec",
             "terminate": "/v1/sandboxes/sb_test/terminate"}

    def respond(request):
        requests.append(request)
        assert request.method == "POST"
        assert request.url.path == paths[operation]
        if body is None:
            return httpx.Response(202)
        return httpx.Response(202, json=body)

    async def exercise_async():
        async with nodus.AsyncClient(api_key="test-key", base_url="https://api.example.com") as client:
            await client._http.aclose()
            client._http = httpx.AsyncClient(base_url="https://api.example.com", transport=httpx.MockTransport(respond))
            sandbox = nodus.AsyncSandbox(client, "sb_test")
            with pytest.raises(nodus.APIError) as error:
                if operation == "create":
                    await client.sandboxes.create(image="python:3.12", budget=1, idempotency_key=idempotency_key)
                elif operation == "exec":
                    await sandbox.exec(["true"], idempotency_key=idempotency_key)
                else:
                    await sandbox.terminate(idempotency_key=idempotency_key)
            assert sandbox.id == "sb_test"
            return error.value

    if asynchronous:
        error = asyncio.run(exercise_async())
    else:
        with nodus.Client(api_key="test-key", base_url="https://api.example.com") as client:
            client._http.close()
            client._http = httpx.Client(base_url="https://api.example.com", transport=httpx.MockTransport(respond))
            sandbox = nodus.Sandbox(client, "sb_test")
            with pytest.raises(nodus.APIError) as caught:
                if operation == "create":
                    client.sandboxes.create(image="python:3.12", budget=1, idempotency_key=idempotency_key)
                elif operation == "exec":
                    sandbox.exec(["true"], idempotency_key=idempotency_key)
                else:
                    sandbox.terminate(idempotency_key=idempotency_key)
            assert sandbox.id == "sb_test"
            error = caught.value
    assert len(requests) == 1
    sent_key = requests[0].headers["Idempotency-Key"]
    assert sent_key and (idempotency_key is None or sent_key == idempotency_key)
    assert error.body == {"idempotency_key": sent_key}
    assert "invalid sandbox receipt" in str(error)
    assert "untrusted" not in str(error)
    assert "sb_different" not in str(error)
