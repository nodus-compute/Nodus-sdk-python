"""Secret operations expose metadata and send values only on explicit writes."""
import asyncio
import json

import httpx
import pytest
import nodus
from test_sandboxes import SANDBOX, sync_client


def test_secret_store_create_injection_and_refresh_wire():
    calls = []
    def handler(request):
        body = json.loads(request.content) if request.content else None
        calls.append((request.method, request.url.path, body))
        if request.url.path == "/v1/secrets" and request.method == "POST":
            return httpx.Response(201, json={"name": "API_KEY", "version": 1, "created_at": "2026-09-17T00:00:00Z"})
        if request.url.path == "/v1/secrets":
            return httpx.Response(200, json={"secrets": [{"name": "API_KEY", "version": 1}]})
        if request.url.path == "/v1/sandboxes":
            return httpx.Response(202, json=SANDBOX)
        return httpx.Response(204)
    client = sync_client(handler)
    assert client.secrets.put("API_KEY", "synthetic-private-value")["version"] == 1
    assert client.secrets.list() == [{"name": "API_KEY", "version": 1}]
    box = client.sandboxes.create(image="python:3.12", secrets=["API_KEY"])
    box.refresh_secrets()
    client.secrets.delete("API_KEY")
    assert calls[0] == ("POST", "/v1/secrets", {"name": "API_KEY", "value": "synthetic-private-value"})
    assert calls[2][2]["secrets"] == ["API_KEY"]
    assert calls[3][:2] == ("POST", "/v1/sandboxes/sb_agent/refresh-secrets")
    assert calls[4][:2] == ("DELETE", "/v1/secrets/API_KEY")
    with pytest.raises(nodus.ValidationError):
        client.secrets.delete("../../tenant")
    with pytest.raises(nodus.ValidationError):
        client.sandboxes.create(image="python:3.12", secrets=["API_KEY", "API_KEY"])
    client.close()


def test_async_secret_wire():
    async def run():
        calls = []
        def handler(request):
            calls.append((request.method, request.url.path, json.loads(request.content) if request.content else None))
            if request.url.path == "/v1/secrets":
                return httpx.Response(200, json={"secrets": []} if request.method == "GET" else {"name": "API_KEY", "version": 2})
            if request.url.path == "/v1/sandboxes":
                return httpx.Response(202, json=SANDBOX)
            return httpx.Response(204)
        client = nodus.AsyncClient(api_key="nk_test", base_url="https://nodus.invalid")
        client._http = httpx.AsyncClient(base_url="https://nodus.invalid", transport=httpx.MockTransport(handler))
        assert (await client.secrets.put("API_KEY", "new-value"))["version"] == 2
        assert await client.secrets.list() == []
        box = await client.sandboxes.create(image="python:3.12", secrets=["API_KEY"])
        await box.refresh_secrets()
        await client.secrets.delete("API_KEY")
        assert calls[2][2]["secrets"] == ["API_KEY"]
        assert calls[3][1].endswith("/refresh-secrets")
        await client.aclose()
    asyncio.run(run())
