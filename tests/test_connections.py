"""Connections exchange references and metadata through the actual HTTP layer."""
import asyncio
import json
import httpx
import pytest
import nodus
from nodus import cli
from test_sandboxes import sync_client
from test_sandbox_cli import client_factory

CONNECTION = {"id": "conn_example", "name": "metrics", "kind": "wandb", "secret_id": "sec_example", "secret_version": 1, "scope": "write", "egress_hosts": ["api.wandb.ai", "files.wandb.ai", "storage.googleapis.com"], "live_mode": True, "entity": "team", "project": "project", "verified_at": "2026-09-19T00:00:00Z", "created_at": "2026-09-19T00:00:00Z", "created_by": "usr_admin"}

def wire(calls):
    def handler(request):
        calls.append((request.method, request.url.path, json.loads(request.content) if request.content else None))
        if request.method == "DELETE":
            return httpx.Response(204)
        if request.method == "GET" and request.url.path == "/v1/connections":
            return httpx.Response(200, json={"connections": [CONNECTION]})
        return httpx.Response(201 if request.url.path == "/v1/connections" else 200, json=CONNECTION)
    return handler

def test_connections_sync_wire_and_input_validation():
    calls = []
    with sync_client(wire(calls)) as client:
        assert client.connections.create("metrics", "wandb", secret="WB_KEY", scope="write", live=True, entity="team", project="project") == CONNECTION
        assert client.connections.list() == [CONNECTION]
        assert client.connections.get("conn_example") == CONNECTION
        assert client.connections.verify("conn_example") == CONNECTION
        client.connections.delete("conn_example")
        assert calls == [("POST", "/v1/connections", {"name": "metrics", "kind": "wandb", "secret": "WB_KEY", "scope": "write", "live_mode": True, "entity": "team", "project": "project"}), ("GET", "/v1/connections", None), ("GET", "/v1/connections/conn_example", None), ("POST", "/v1/connections/conn_example/verify", None), ("DELETE", "/v1/connections/conn_example", None)]
        for kwargs in [{"secret": "postgres://user:private@db/db"}, {"secret": "DB", "scope": "admin"}, {"secret": "DB", "live": True}, {"secret": "DB", "region": "../../region"}]:
            with pytest.raises(nodus.ValidationError):
                client.connections.create("db", "postgres", **kwargs)
        for ref in ["../secrets", "https://host", "foo?x=private"]:
            with pytest.raises(nodus.ValidationError):
                client.connections.get(ref)
        assert len(calls) == 5

def test_connections_async_wire():
    async def run():
        calls = []
        client = nodus.AsyncClient(api_key="nk_test", base_url="https://nodus.invalid")
        client._http = httpx.AsyncClient(base_url="https://nodus.invalid", transport=httpx.MockTransport(wire(calls)))
        async with client:
            assert await client.connections.create("metrics", "wandb", secret="sec_example", live=True, entity="team", project="project") == CONNECTION
            assert await client.connections.list() == [CONNECTION]
            assert await client.connections.get("conn_example") == CONNECTION
            assert await client.connections.verify("conn_example") == CONNECTION
            await client.connections.delete("conn_example")
        assert [c[:2] for c in calls] == [("POST", "/v1/connections"), ("GET", "/v1/connections"), ("GET", "/v1/connections/conn_example"), ("POST", "/v1/connections/conn_example/verify"), ("DELETE", "/v1/connections/conn_example")]
        assert calls[0][2]["secret"] == "sec_example"
        assert "scope" not in calls[0][2]
    asyncio.run(run())

def test_connection_cli_roundtrip(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(cli, "Client", client_factory(wire(calls)))
    assert cli.main(["connection", "add", "wandb", "--name", "metrics", "--secret", "WB_KEY", "--live", "--entity", "team", "--project", "project"]) == 0
    assert cli.main(["connection", "ls"]) == 0
    assert cli.main(["connection", "verify", "conn_example"]) == 0
    assert cli.main(["connection", "rm", "conn_example"]) == 0
    assert calls[0][2]["live_mode"] is True
    assert calls[0][2]["secret"] == "WB_KEY"
    assert calls[-1][:2] == ("DELETE", "/v1/connections/conn_example")
    assert "metrics" in capsys.readouterr().out

@pytest.mark.parametrize("debug", [False, True])
def test_connection_cli_hides_remote_error_body(monkeypatch, capsys, debug):
    def handler(request):
        return httpx.Response(422, json={"error": "verification_failed", "message": "private-credential"})
    monkeypatch.setattr(cli, "Client", client_factory(handler))
    assert cli.main((["--debug"] if debug else []) + ["connection", "verify", "conn_example"]) == 2
    assert "private-credential" not in str(capsys.readouterr())

def test_connection_name_cannot_shadow_existing_github_route():
    with sync_client(lambda request: pytest.fail("reserved name reached HTTP")) as client:
        with pytest.raises(nodus.ValidationError):
            client.connections.create("github", "postgres", secret="DB")

def test_connection_name_cannot_shadow_connection_id():
    with sync_client(lambda request: pytest.fail("reserved name reached HTTP")) as client:
        with pytest.raises(nodus.ValidationError):
            client.connections.create("conn_reserved", "postgres", secret="DB")
