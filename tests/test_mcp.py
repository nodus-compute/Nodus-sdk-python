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
from mcp.shared.memory import create_connected_server_and_client_session


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
async def test_saved_login_and_all_tool_contracts(api):
    server, requests, responses = api
    async with create_connected_server_and_client_session(server) as session:
        tools = {tool.name: tool for tool in (await session.list_tools()).tools}
        assert set(tools) == {"submit_workload", "list_workloads", "get_workload",
                              "cancel_workload", "get_workload_events", "get_workload_logs",
                              "list_workload_outputs", "validate_workload", "download_workload_output"}
        assert tools["cancel_workload"].inputSchema["required"] == ["workload_id"]
        assert all("ctx" not in tool.inputSchema["properties"] for tool in tools.values())
        workload = {"source": {"image": "example/image", "command": ["nvidia-smi"]},
                    "outcome": {"max_cost_usd": 1}}
        await session.call_tool("submit_workload", {"idempotency_key": "unique-run", "workload": workload})
        assert requests[-1].method == "POST"
        assert requests[-1].url.path == "/v1/workloads"
        assert requests[-1].headers["idempotency-key"] == "unique-run"
        assert json.loads(requests[-1].content) == workload
        await session.call_tool("list_workloads", {"scope": "mine", "limit": 20, "offset": 40})
        assert dict(requests[-1].url.params) == {"scope": "mine", "limit": "20", "offset": "40"}
        for tool, suffix in [("get_workload", ""), ("get_workload_events", "/events"),
                             ("get_workload_logs", "/logs"), ("list_workload_outputs", "/outputs")]:
            responses.append(httpx.Response(200, text="log text" if suffix == "/logs" else '{"ok":true}'))
            args = {"workload_id": "wl_test"}
            if suffix == "/events":
                args["after"] = 100
            result = await session.call_tool(tool, args)
            assert requests[-1].method == "GET"
            assert requests[-1].url.path == "/v1/workloads/wl_test" + suffix
            assert result.content[0].text == ("log text" if suffix == "/logs" else '{"ok":true}')
            if suffix == "/events":
                assert requests[-1].url.params["after"] == "100"
        await session.call_tool("cancel_workload", {"workload_id": "wl_test"})
        assert requests[-1].method == "POST"
        assert requests[-1].url.path == "/v1/workloads/wl_test/cancel"
        assert requests[-1].content == b""
        assert "idempotency-key" not in requests[-1].headers
        assert all(r.headers["authorization"] == "Bearer saved-test-key" for r in requests)


@pytest.mark.asyncio
async def test_output_download_verifies_bytes_and_refuses_overwrite(api, tmp_path):
    import hashlib
    server, requests, responses = api
    output = b'customer training result\n'
    target = tmp_path / "result.txt"
    headers = {"Content-Length": str(len(output)), "X-Nodus-SHA256": hashlib.sha256(output).hexdigest()}
    async with create_connected_server_and_client_session(server) as session:
        responses.append(httpx.Response(200, content=output, headers=headers))
        result = await session.call_tool("download_workload_output", {"workload_id": "wl_test", "name": "result", "destination": str(target), "stage": "train"})
        assert not result.isError, result
        assert target.read_bytes() == output
        assert requests[-1].url.path == "/v1/workloads/wl_test/outputs/result"
        assert requests[-1].url.params["stage"] == "train"
        responses.append(httpx.Response(200, content=b"replacement", headers=headers))
        result = await session.call_tool("download_workload_output", {"workload_id": "wl_test", "name": "result", "destination": str(target)})
        assert result.isError
        assert target.read_bytes() == output


@pytest.mark.asyncio
async def test_validate_and_submit_require_explicit_positive_budget(api):
    server, requests, responses = api
    async with create_connected_server_and_client_session(server) as session:
        for budget in (None, 0, -1, True, "10"):
            workload = {"outcome": {"max_cost_usd": budget}}
            for tool in ("validate_workload", "submit_workload"):
                args = {"workload": workload}
                if tool == "submit_workload":
                    args["idempotency_key"] = "saved-before-submit"
                result = await session.call_tool(tool, args)
                assert result.isError
        assert requests == []
        workload = {"source": {"image": "customer/image", "command": ["python", "train.py"]}, "outcome": {"max_cost_usd": 10}}
        responses.append(httpx.Response(200, json={"valid": True, "submitted": False, "workload": workload}))
        result = await session.call_tool("validate_workload", {"workload": workload})
        assert not result.isError
        assert requests[-1].url.path == "/v1/workloads/validate"
        assert json.loads(requests[-1].content) == workload


@pytest.mark.asyncio
@pytest.mark.parametrize("tool,args", [
    ("get_workload", {"workload_id": "../secrets"}),
    ("submit_workload", {"idempotency_key": "bad\r\nkey", "workload": {"outcome": {"max_cost_usd": 10}}}),
    ("list_workloads", {"scope": "other"}),
    ("list_workloads", {"limit": 101}),
    ("list_workloads", {"limit": True}),
    ("list_workloads", {"offset": -1}),
    ("get_workload_events", {"workload_id": "wl_test", "after": 9007199254740992}),
])
async def test_invalid_arguments_never_reach_api(api, tool, args):
    server, requests, _ = api
    async with create_connected_server_and_client_session(server) as session:
        result = await session.call_tool(tool, args)
        assert result.isError
        assert requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [302, 401, 500])
async def test_http_failures_and_redirects_are_tool_errors(api, status):
    server, requests, responses = api
    async with create_connected_server_and_client_session(server) as session:
        responses.append(httpx.Response(status, headers={"Location": "https://other.example/"},
                                        text='{"error":"denied"}'))
        result = await session.call_tool("list_workloads", {})
        assert result.isError
        assert str(status) in result.content[0].text
        assert len(requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [200, 500])
async def test_large_success_and_error_responses_are_bounded(api, status):
    server, _, responses = api
    async with create_connected_server_and_client_session(server) as session:
        responses.append(httpx.Response(status, content=b"x" * (16 * 1024 * 1024 + 1)))
        result = await session.call_tool("get_workload_logs", {"workload_id": "wl_test"})
        assert result.isError
        assert "16 MiB" in result.content[0].text


@pytest.mark.asyncio
@pytest.mark.parametrize("url", ["http://example.test", "http://127.evil.test", "https://user:pass@example.test"])
async def test_unsafe_api_origin_sends_no_credentials(api, monkeypatch, url):
    server, requests, _ = api
    async with create_connected_server_and_client_session(server) as session:
        monkeypatch.setenv("NODUS_BASE_URL", url)
        result = await session.call_tool("list_workloads", {})
        assert result.isError
        assert requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize("executable", ["nodus", "nodus-mcp"])
async def test_installed_executables_complete_real_mcp_session(executable, nodus_config):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from pathlib import Path
    import threading
    import sysconfig
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    requests = []
    closed = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self):
            requests.append((self.path, self.headers.get("Authorization"),
                             self.headers.get("Cookie"), self.client_address))
            body = b'{"workloads":[],"next_offset":null}'
            self.send_response(500 if self.path.endswith("wl_error") else 200)
            self.send_header("Set-Cookie", "session=server-session; Path=/")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def finish(self):
            super().finish()
            closed.set()

        def log_message(self, *args):
            pass

    api_server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=api_server.serve_forever, daemon=True)
    thread.start()
    command = Path(sysconfig.get_path("scripts")) / (executable + (".exe" if os.name == "nt" else ""))
    from nodus.config import save_credentials
    origin = f"http://127.0.0.1:{api_server.server_port}"
    save_credentials("integration-test-key", origin)
    home = str(nodus_config.parent.parent)
    params = StdioServerParameters(
        command=str(command), args=["mcp"] if executable == "nodus" else [],
        env={"HOME": home, "USERPROFILE": home,
             "NODUS_API_KEY": "", "NODUS_BASE_URL": ""})
    try:
        async with stdio_client(params) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                result = await session.initialize()
                assert result.serverInfo.name == "nodus"
                assert len((await session.list_tools()).tools) == 9
                listed = await session.call_tool("list_workloads", {})
                assert not listed.isError
                assert json.loads(listed.content[0].text) == {"workloads": [], "next_offset": None}
                failed = await session.call_tool("get_workload", {"workload_id": "wl_error"})
                assert failed.isError
                save_credentials("renewed-test-key", origin)
                refreshed = await session.call_tool("list_workloads", {})
                assert not refreshed.isError
                assert len({request[3] for request in requests}) == 1
                assert not closed.is_set()
                invalid = await session.call_tool("get_workload", {"workload_id": "../secret"})
                assert invalid.isError
        assert closed.wait(timeout=5)
    finally:
        api_server.shutdown()
        api_server.server_close()
        thread.join(timeout=5)
    assert [request[:3] for request in requests] == [
        ("/v1/workloads", "Bearer integration-test-key", None),
        ("/v1/workloads/wl_error", "Bearer integration-test-key", None),
        ("/v1/workloads", "Bearer renewed-test-key", None),
    ]


@pytest.mark.asyncio
async def test_pool_uses_current_origin_and_supports_concurrent_calls(api):
    import asyncio
    from nodus.config import save_credentials

    server, requests, _ = api
    async with create_connected_server_and_client_session(server) as session:
        assert not (await session.call_tool("list_workloads", {})).isError
        save_credentials("second-account-key", "https://second.example.test")
        results = await asyncio.gather(*(
            session.call_tool("get_workload", {"workload_id": name})
            for name in ("wl_one", "wl_two", "wl_three")))
        assert all(not result.isError for result in results)
    assert requests[0].url.host == "api.example.test"
    assert {request.url.path for request in requests[1:]} == {
        "/v1/workloads/wl_one", "/v1/workloads/wl_two", "/v1/workloads/wl_three"}
    assert all(request.url.host == "second.example.test" for request in requests[1:])
    assert all(request.headers["authorization"] == "Bearer second-account-key"
               for request in requests[1:])


@pytest.mark.asyncio
async def test_server_owns_and_closes_the_pool_after_tool_failure(monkeypatch):
    from nodus import _mcp
    from nodus.config import save_credentials

    save_credentials("lifecycle-test-key", "https://api.example.test")
    clients = []
    client_class = httpx.AsyncClient

    def create_client(**kwargs):
        client = client_class(transport=httpx.MockTransport(
            lambda request: httpx.Response(500, text="temporary failure")), **kwargs)
        clients.append(client)
        return client

    monkeypatch.setattr(_mcp.httpx, "AsyncClient", create_client)
    async with create_connected_server_and_client_session(_mcp.create_server()) as session:
        for _ in range(2):
            result = await session.call_tool("list_workloads", {})
            assert result.isError
        assert len(clients) == 1
        assert not clients[0].is_closed
    assert clients[0].is_closed


@pytest.mark.asyncio
async def test_download_keeps_resolved_credential_and_origin_together(api, nodus_config, tmp_path, monkeypatch):
    import hashlib
    from pathlib import Path
    server, requests, responses = api
    original_read = Path.read_text
    changed = False

    def change_login_after_read(path, *args, **kwargs):
        nonlocal changed
        content = original_read(path, *args, **kwargs)
        if path == nodus_config and not changed:
            changed = True
            nodus_config.write_text('[default]\napi_key="other-account-key"\nbase_url="https://other.example.test"\n')
        return content

    monkeypatch.setattr(Path, "read_text", change_login_after_read)
    data = b"verified result"
    responses.append(httpx.Response(200, content=data, headers={"X-Nodus-SHA256": hashlib.sha256(data).hexdigest()}))
    async with create_connected_server_and_client_session(server) as session:
        result = await session.call_tool("download_workload_output", {
            "workload_id": "wl_test", "name": "result", "destination": str(tmp_path / "result")})
    assert not result.isError
    assert changed
    assert [(request.url.host, request.headers["authorization"]) for request in requests] == [
        ("api.example.test", "Bearer saved-test-key")]
