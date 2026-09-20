"""Public MCP installation, saved login and customer HTTP contracts."""
import json
import os
import subprocess
import sys


def test_cli_starts_mcp_instead_of_requiring_a_private_binary():
    env = {k: v for k, v in os.environ.items() if not k.startswith("NODUS_")}
    request = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2025-06-18", "capabilities": {},
        "clientInfo": {"name": "customer-install", "version": "1"}}}
    result = subprocess.run([sys.executable, "-m", "nodus.cli", "mcp"],
                            input=json.dumps(request) + "\n", text=True,
                            capture_output=True, env=env, timeout=15)
    assert result.returncode == 0, result.stderr
    response = json.loads(result.stdout)
    assert response["result"]["serverInfo"]["name"] == "nodus"
    assert "tools" in response["result"]["capabilities"]

import httpx
import pytest


@pytest.fixture
def api(monkeypatch):
    from nodus import _mcp
    from nodus.config import save_credentials
    save_credentials("saved-test-key", "https://api.example.test")
    requests = []
    responses = []

    def respond(request):
        requests.append(request)
        return responses.pop(0) if responses else httpx.Response(200, json={"ok": True})

    client = httpx.AsyncClient
    monkeypatch.setattr(_mcp.httpx, "AsyncClient", lambda **kwargs: client(
        transport=httpx.MockTransport(respond), **kwargs))
    return _mcp.create_server(), requests, responses


@pytest.mark.asyncio
async def test_saved_login_and_all_seven_tool_contracts(api):
    server, requests, responses = api
    tools = {tool.name: tool for tool in await server.list_tools()}
    assert set(tools) == {"submit_workload", "list_workloads", "get_workload",
                          "cancel_workload", "get_workload_events", "get_workload_logs",
                          "list_workload_outputs"}
    assert tools["cancel_workload"].inputSchema["required"] == ["workload_id"]
    workload = {"source": {"image": "example/image", "command": ["nvidia-smi"]},
                "outcome": {"max_cost_usd": 1}}
    await server.call_tool("submit_workload", {"idempotency_key": "unique-run", "workload": workload})
    assert requests[-1].method == "POST"
    assert requests[-1].url.path == "/v1/workloads"
    assert requests[-1].headers["idempotency-key"] == "unique-run"
    assert json.loads(requests[-1].content) == workload
    await server.call_tool("list_workloads", {"scope": "mine", "limit": 20, "offset": 40})
    assert dict(requests[-1].url.params) == {"scope": "mine", "limit": "20", "offset": "40"}
    for tool, suffix in [("get_workload", ""), ("get_workload_events", "/events"),
                         ("get_workload_logs", "/logs"), ("list_workload_outputs", "/outputs")]:
        responses.append(httpx.Response(200, text="log text" if suffix == "/logs" else '{"ok":true}'))
        args = {"workload_id": "wl_test"}
        if suffix == "/events":
            args["after"] = 100
        result = await server.call_tool(tool, args)
        assert requests[-1].method == "GET"
        assert requests[-1].url.path == "/v1/workloads/wl_test" + suffix
        assert result[0].text == ("log text" if suffix == "/logs" else '{"ok":true}')
        if suffix == "/events":
            assert requests[-1].url.params["after"] == "100"
    await server.call_tool("cancel_workload", {"workload_id": "wl_test"})
    assert requests[-1].method == "POST"
    assert requests[-1].url.path == "/v1/workloads/wl_test/cancel"
    assert requests[-1].content == b""
    assert "idempotency-key" not in requests[-1].headers
    assert all(r.headers["authorization"] == "Bearer saved-test-key" for r in requests)


@pytest.mark.asyncio
@pytest.mark.parametrize("tool,args", [
    ("get_workload", {"workload_id": "../secrets"}),
    ("submit_workload", {"idempotency_key": "bad\r\nkey", "workload": {}}),
    ("list_workloads", {"scope": "other"}),
    ("list_workloads", {"limit": 101}),
    ("list_workloads", {"limit": True}),
    ("list_workloads", {"offset": -1}),
    ("get_workload_events", {"workload_id": "wl_test", "after": 9007199254740992}),
])
async def test_invalid_arguments_never_reach_api(api, tool, args):
    server, requests, _ = api
    with pytest.raises(Exception):
        await server.call_tool(tool, args)
    assert requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [302, 401, 500])
async def test_http_failures_and_redirects_are_tool_errors(api, status):
    server, requests, responses = api
    responses.append(httpx.Response(status, headers={"Location": "https://other.example/"},
                                    text='{"error":"denied"}'))
    with pytest.raises(Exception, match=str(status)):
        await server.call_tool("list_workloads", {})
    assert len(requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [200, 500])
async def test_large_success_and_error_responses_are_bounded(api, status):
    server, _, responses = api
    responses.append(httpx.Response(status, content=b"x" * (16 * 1024 * 1024 + 1)))
    with pytest.raises(Exception, match="16 MiB"):
        await server.call_tool("get_workload_logs", {"workload_id": "wl_test"})


@pytest.mark.asyncio
@pytest.mark.parametrize("url", ["http://example.test", "http://127.evil.test", "https://user:pass@example.test"])
async def test_unsafe_api_origin_sends_no_credentials(api, monkeypatch, url):
    server, requests, _ = api
    monkeypatch.setenv("NODUS_BASE_URL", url)
    with pytest.raises(Exception):
        await server.call_tool("list_workloads", {})
    assert requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize("executable", ["nodus", "nodus-mcp"])
async def test_installed_executables_complete_real_mcp_session(executable):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from pathlib import Path
    import threading
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append((self.path, self.headers.get("Authorization")))
            body = b'{"workloads":[],"next_offset":null}'
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    api_server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=api_server.serve_forever, daemon=True)
    thread.start()
    command = Path(sys.executable).parent / (executable + (".exe" if os.name == "nt" else ""))
    params = StdioServerParameters(
        command=str(command), args=["mcp"] if executable == "nodus" else [],
        env={"NODUS_API_KEY": "integration-test-key",
             "NODUS_BASE_URL": f"http://127.0.0.1:{api_server.server_port}"})
    try:
        async with stdio_client(params) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                result = await session.initialize()
                assert result.serverInfo.name == "nodus"
                assert len((await session.list_tools()).tools) == 7
                listed = await session.call_tool("list_workloads", {})
                assert not listed.isError
                assert json.loads(listed.content[0].text) == {"workloads": [], "next_offset": None}
                invalid = await session.call_tool("get_workload", {"workload_id": "../secret"})
                assert invalid.isError
    finally:
        api_server.shutdown()
        api_server.server_close()
        thread.join(timeout=5)
    assert requests == [("/v1/workloads", "Bearer integration-test-key")]
