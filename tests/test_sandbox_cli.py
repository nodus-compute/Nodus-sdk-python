"""CLI journeys for the public sandbox resource."""

from __future__ import annotations

import base64
import json

import httpx

import nodus
from nodus import cli

SANDBOX = {
    "id": "sb_agent",
    "state": "ready",
    "envelope": {"name": "agent-session", "source": {"image": "python:3.12"}},
    "cost_usd": 1.25,
    "url": "https://console.nodus-compute.ai/sandboxes/sb_agent",
}
EXEC = {
    "id": "sx_python",
    "sandbox_id": "sb_agent",
    "spec": {"command": ["python", "-c", "print('hello')"]},
    "state": "running",
    "exit_code": None,
}


def client_factory(handler):
    def build(**_kwargs):
        client = nodus.Client(api_key="nk_live_test", base_url="https://nodus.invalid")
        client._http = httpx.Client(
            base_url="https://nodus.invalid",
            transport=httpx.MockTransport(handler),
            headers={"Authorization": "Bearer nk_live_test"},
        )
        return client

    return build


def test_sandbox_cli_mirrors_create_exec_logs_cost_list_and_remove(monkeypatch, capsys):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        calls.append((request.method, request.url.path, dict(request.url.params), body))
        if request.method == "POST" and request.url.path == "/v1/sandboxes":
            return httpx.Response(202, json=SANDBOX)
        if request.method == "GET" and request.url.path == "/v1/sandboxes":
            return httpx.Response(200, json={"sandboxes": [SANDBOX], "next_cursor": None})
        if request.method == "GET" and request.url.path == "/v1/sandboxes/sb_agent":
            return httpx.Response(200, json=SANDBOX)
        if request.method == "POST" and request.url.path.endswith("/exec"):
            return httpx.Response(202, json=EXEC)
        if request.method == "GET" and request.url.path.endswith("/stream"):
            return httpx.Response(200, json={
                "frames": [{
                    "sequence": 1,
                    "stream": "stdout",
                    "offset": 0,
                    "data": base64.b64encode(b"hello\n").decode(),
                    "created_at": "2026-09-13T12:00:05Z",
                }],
                "next_sequence": 1,
                "last_sequence": 1,
                "final_sequence": 1,
                "state": "completed",
                "done": True,
                "complete": True,
            })
        if request.method == "GET" and request.url.path.endswith("/execs/sx_python"):
            return httpx.Response(200, json={**EXEC, "state": "completed", "exit_code": 0})
        if request.method == "POST" and request.url.path.endswith("/terminate"):
            return httpx.Response(200, json={**SANDBOX, "state": "terminated"})
        raise AssertionError(request.url)

    monkeypatch.setattr(cli, "Client", client_factory(handler))

    assert cli.main(["sandbox", "new", "python:3.12", "--name", "agent-session", "--budget", "3"]) == 0
    assert cli.main(["sandbox", "exec", "sb_agent", "printf 'hello\\n'"]) == 0
    assert cli.main(["sandbox", "logs", "sb_agent", "sx_python"]) == 0
    assert cli.main(["sandbox", "cost", "sb_agent"]) == 0
    assert cli.main(["--plain", "sandbox", "ls"]) == 0
    assert cli.main(["sandbox", "rm", "sb_agent"]) == 0

    output = capsys.readouterr().out
    assert "sb_agent" in output
    assert output.count("hello") == 2
    assert "$1.25" in output
    cost_read = ("GET", "/v1/sandboxes/sb_agent", {}, None)
    assert cost_read in calls
    exec_call = next(call for call in calls if call[0] == "POST" and call[1].endswith("/exec"))
    assert exec_call[3]["command"] == ["/bin/sh", "-lc", "printf 'hello\\n'"]


def test_sandbox_cli_accepts_name_only_reattach():
    args = cli.build_parser().parse_args(["sandbox", "new", "--name", "agent-session"])
    assert args.image is None
    assert args.name == "agent-session"
    assert "sandbox" in cli.build_parser().format_help()


def test_sandbox_cli_rejects_nonpositive_and_nonfinite_budgets():
    parser = cli.build_parser()
    for value in ("0", "-1", "nan", "inf"):
        try:
            parser.parse_args(["sandbox", "new", "python:3.12", "--budget", value])
        except SystemExit as error:
            assert error.code == 2
        else:
            raise AssertionError(f"accepted invalid budget {value}")
