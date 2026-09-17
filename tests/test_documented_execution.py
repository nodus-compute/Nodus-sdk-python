"""Execute documentation through the installed SDK and a local HTTP test server.

The server simulates completion. These checks do not execute GPU containers.
"""
from __future__ import annotations

import ast
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import sysconfig
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import nodus
import pytest

ROOT = Path(__file__).parents[1]
DATA = b'{"sum": 6, "sum_of_squares": 385, "gpu": "Synthetic GPU"}\n'
DIGEST = hashlib.sha256(DATA).hexdigest()


@pytest.fixture
def docs_api(monkeypatch):
    calls = []
    submissions = []
    row = {"id": "wl_docs", "status": "completed", "revision": 2,
           "spend_usd": 0.01, "meter": {"total_now_usd": 0.01},
           "route": {"sku": "nodus:test", "region": "test-region", "expected_cost_usd": 0.01}}
    sandbox = {
        "id": "sb_docs", "state": "ready", "envelope": {}, "cost_usd": 0.01,
        "network_usage": {"sent_bytes": 123, "received_bytes": 456},
        "url": "https://console.nodus-compute.ai/sandboxes/sb_docs",
        "created_at": "2026-09-13T12:00:00Z", "updated_at": "2026-09-13T12:00:01Z",
        "last_activity_at": "2026-09-13T12:00:01Z", "terminal_at": None,
    }
    execution = {
        "id": "sx_docs", "sandbox_id": "sb_docs", "state": "running",
        "spec": {"command": ["python", "agent.py"], "cwd": "", "env": {}, "timeout_s": 120, "stdin": True},
        "created_at": "2026-09-13T12:00:02Z", "updated_at": "2026-09-13T12:00:03Z",
        "dispatched_at": "2026-09-13T12:00:02Z", "deadline_at": "2026-09-13T12:02:02Z",
        "started_at": "2026-09-13T12:00:03Z", "completed_at": None, "exit_code": None,
        "failure_code": "", "output_sequence": 0, "stdout_bytes": 0, "stderr_bytes": 0,
        "final_output_sequence": None, "cancel_requested_at": None, "cancel_reason": "",
    }

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def reply(self, body, status=200, headers=None):
            raw = body if isinstance(body, bytes) else json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Content-Type", "application/json")
            for name, value in (headers or {}).items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            path = urlsplit(self.path).path
            calls.append(("GET", path))
            if self.headers.get("Authorization") != "Bearer nk_docs":
                return self.reply({"error": "unauthorized"}, 401)
            if path == "/v1/pools/pool_docs/proposals":
                query = parse_qs(urlsplit(self.path).query)
                if query != {"limit": ["25"], "state": ["pending"]}:
                    return self.reply({"error": "invalid_query"}, 400)
                return self.reply({"pool_id": "pool_docs", "proposals": [], "next_cursor": None})
            if path == "/v1/pools/pool_docs/recommendations":
                query = parse_qs(urlsplit(self.path).query)
                if query.get("limit") != ["25"] or query.get("state") != ["expired"]:
                    return self.reply({"error": "invalid_query"}, 400)
                cursor = query.get("cursor", [None])[0]
                if cursor not in (None, "docs_next"):
                    return self.reply({"error": "invalid_cursor"}, 400)
                return self.reply({"pool_id": "pool_docs", "predict_enabled": False,
                    "refresh_status": "disabled", "recommendations": [],
                    "next_cursor": "docs_next" if cursor is None else None})
            if path == "/v1/assets":
                return self.reply({"assets": [], "max_import_bytes": 1048576})
            if path == "/v1/workloads":
                return self.reply({"workloads": [row]})
            if path == "/v1/workloads/wl_docs":
                return self.reply(row)
            if path in ("/v1/sandboxes/sb_docs/events", "/v1/sandboxes/sb_example/events"):
                after = int(parse_qs(urlsplit(self.path).query).get("after", ["0"])[0])
                return self.reply({"events": [] if after >= 1 else [{"id": 1, "event_type": "sandbox.egress_denied", "payload": {"hostname": "example.com", "count": 2, "generation": 1}}]})
            if path in ("/v1/sandboxes/sb_docs", "/v1/sandboxes/sb_example"):
                return self.reply({**sandbox, "id": path.rsplit("/", 1)[-1]})
            if path.endswith("/execs/sx_docs/stream"):
                return self.reply({
                    "frames": [{
                        "sequence": 1, "stream": "stdout", "offset": 0,
                        "data": base64.b64encode(b"tool result\n").decode(),
                        "created_at": "2026-09-13T12:00:04Z",
                    }],
                    "next_sequence": 1, "last_sequence": 1, "final_sequence": 1,
                    "state": "completed", "done": True, "complete": True,
                })
            if path.endswith("/execs/sx_docs"):
                return self.reply({**execution, "state": "completed", "exit_code": 0, "final_output_sequence": 1})
            suffix = path.removeprefix("/v1/workloads/wl_docs/")
            responses = {
                "logs": b"GPU output fixture\n",
                "events": {"events": [] if int(parse_qs(urlsplit(self.path).query).get("after", ["0"])[0]) >= 1 else [{"id": 1, "event_type": "workload.completed", "payload": {}}]},
                "artifacts": {"artifacts": []},
                "outputs": {"outputs": [{"name": "result", "stage_id": "summarize", "bytes": len(DATA), "sha256": DIGEST}]},
                "routing": {"placements": []},
                "ledger": {"entries": [], "charged_usd": 0.01, "settlement": {"status": "settled", "balance_usd": 0}},
            }
            if suffix == "outputs/result":
                return self.reply(DATA, headers={"X-Nodus-SHA256": DIGEST})
            if suffix in responses:
                return self.reply(responses[suffix])
            return self.reply({"error": "not_found", "message": path}, 404)

        def do_PATCH(self):
            path = urlsplit(self.path).path
            payload = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
            calls.append(("PATCH", path))
            if self.headers.get("Authorization") != "Bearer nk_docs":
                return self.reply({"error": "unauthorized"}, 401)
            if path != "/v1/pools/pool_docs":
                return self.reply({"error": "not_found"}, 404)
            if payload.get("route_enabled") is True and (payload.get("accepted_route_rate_version") != "route-platform-v1" or payload.get("accepted_route_rate_micros") != 20000):
                return self.reply({"error": "route_rate_consent"}, 409)
            return self.reply({"id": "pool_docs", "name": "Research", "kind": "hosts", "state": "active",
                "route_enabled": True, "platform_rate_micros": 20000, **payload})

        def do_POST(self):
            path = urlsplit(self.path).path
            if self.headers.get("Transfer-Encoding") == "chunked":
                body = bytearray()
                while True:
                    count = int(self.rfile.readline().strip(), 16)
                    if count == 0:
                        self.rfile.readline()
                        break
                    body.extend(self.rfile.read(count))
                    self.rfile.read(2)
                raw = bytes(body)
            else:
                raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            if path in ("/v1/assets/upload", "/v1/assets/import"):
                if self.headers.get("Authorization") != "Bearer nk_docs":
                    return self.reply({"error": "unauthorized"}, 401)
                return self.reply({"id": "asset_docs", "state": "ready", "name": "fixture", "kind": "file"}, 201)
            payload = json.loads(raw or b"{}")
            calls.append(("POST", path))
            if path == "/v1/console/device/start":
                return self.reply({"device_code": "device-test", "user_code": "ABCD-EFGH",
                    "verification_url": "https://console.nodus-compute.ai/device?code=ABCD-EFGH", "interval": 1, "expires_in": 60})
            if path == "/v1/console/device/token":
                return self.reply({"api_key": "nk_docs", "base_url": address, "tenant": "docs-test"})
            if self.headers.get("Authorization") != "Bearer nk_docs":
                return self.reply({"error": "unauthorized"}, 401)
            if path == "/v1/pools/pool_docs/enrollment-tokens":
                if payload != {"mode": "execute", "host_id": "host_docs"}:
                    return self.reply({"error": "invalid_enrollment"}, 400)
                return self.reply({"id": "pet_docs", "mode": "execute", "token": "synthetic-token", "expires_at": "2026-09-18T12:00:00Z"}, 201)
            if path == "/v1/workloads":
                submissions.append(payload)
                if not self.headers.get("Idempotency-Key") or not payload.get("outcome", {}).get("max_cost_usd"):
                    return self.reply({"error": "invalid_brief"}, 400)
                return self.reply({"id": "wl_docs", "workload_id": "wl_docs", "status": "accepted", "revision": 1}, 202)
            if path == "/v1/sandboxes":
                return self.reply(sandbox, 202)
            if path in ("/v1/sandboxes/sb_docs/exec", "/v1/sandboxes/sb_example/exec"):
                sandbox_id = path.split("/")[3]
                return self.reply({**execution, "sandbox_id": sandbox_id, "spec": {**execution["spec"], **payload}}, 202)
            if path.endswith("/execs/sx_docs/stdin"):
                data = base64.b64decode(payload.get("data", ""))
                return self.reply({"sequence": 1, "bytes": len(data), "eof": bool(payload.get("eof")), "created_at": "2026-09-13T12:00:04Z"}, 202)
            if path in ("/v1/sandboxes/sb_docs/terminate", "/v1/sandboxes/sb_example/terminate"):
                sandbox_id = path.split("/")[3]
                return self.reply({**sandbox, "id": sandbox_id, "state": "terminated"})
            if path == "/v1/workloads/wl_docs/cancel":
                return self.reply({"status": "cancel_requested"}, 202)
            return self.reply({"error": "not_found", "message": path}, 404)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    address = f"http://127.0.0.1:{server.server_port}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("NODUS_BASE_URL", address)
    monkeypatch.setenv("NODUS_API_KEY", "nk_docs")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    try:
        yield address, calls, submissions
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def blocks():
    for path in [ROOT / "README.md", *sorted((ROOT / "docs").rglob("*.md"))]:
        for number, match in enumerate(re.finditer(r'^```python\n(.*?)^```', path.read_text(), re.M | re.S)):
            yield path, number, match.group(1)


EXAMPLES = list(blocks())


@pytest.mark.parametrize("path,number,body", EXAMPLES, ids=[f"{p.relative_to(ROOT)}:{n + 1}" for p, n, _ in EXAMPLES])
def test_python_documentation_executes(path, number, body, docs_api, tmp_path, monkeypatch):
    if path.name == "containers-and-scripts.md" and number == 0:
        pytest.skip("container-side CUDA program requires a GPU and PyTorch image")
    monkeypatch.chdir(tmp_path)
    for name in ("hello.py", "train.py", "data.csv"):
        (tmp_path / name).write_text("test fixture")
    from nodus._workload_file import write_workload_file
    write_workload_file(tmp_path / "train.toml")
    with nodus.Client() as client:
        sandbox_handle = client.sandboxes.from_id("sb_docs")
        execution_handle = sandbox_handle.exec(["python", "agent.py"], stdin=True)
        namespace = {"__name__": "__docs__", "client": client, "nodus": nodus,
                     "workload_id": "wl_docs", "pool_id": "pool_docs", "host_id": "host_docs", "allowed_regions": ["test-region"],
                     "done": client.get("wl_docs"), "workload": client.get("wl_docs"),
                     "sandbox": sandbox_handle, "execution": execution_handle}
        exec(compile(body.replace('"YOUR_WORKLOAD_ID"', '"wl_docs"'), str(path), "exec"), namespace)
    # Compile embedded Python argv too, without pretending it ran on a GPU.
    for payload in docs_api[2]:
        sources = [payload.get("source", {})] + [s.get("source", {}) for s in payload.get("stages", [])]
        for source in sources:
            command = source.get("command", [])
            if command and command[0] == "python" and "-c" in command:
                ast.parse(command[command.index("-c") + 1])


@pytest.mark.parametrize("script,args", [
    ("basic.py", ["--budget", "5"]),
    ("async_sweep.py", ["--run-id", "docs-check", "--budget-per-run", "1"]),
    ("ci_submit.py", ["--submission-id", "docs-check", "--budget", "5"]),
    ("multi_stage.py", ["--submission-id", "docs-check", "--budget", "5"]),
])
def test_complete_example_programs(script, args, docs_api, tmp_path):
    result = subprocess.run([sys.executable, str(ROOT / "examples" / script), *args],
                            cwd=tmp_path, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert "wl_docs" in result.stdout
    if script == "multi_stage.py":
        assert (tmp_path / "results/result.json").read_bytes() == DATA


@pytest.mark.parametrize("args", [
    ["--version"], ["--help"], ["run", "--help"], ["login", "--help"],
    ["init"], ["run"], ["submit"], ["list", "active"],
    ["status", "wl_docs"], ["wait", "wl_docs"],
    ["events", "wl_docs"], ["logs", "wl_docs"],
    ["artifacts", "wl_docs"], ["explain", "wl_docs"], ["ledger", "wl_docs"],
    ["download", "wl_docs"], ["cancel", "wl_docs"], ["assets"], ["upload", "hello.py"],
])
def test_installed_terminal_commands(args, docs_api, tmp_path):
    from nodus._workload_file import write_workload_file
    if args[0] != "init":
        write_workload_file(tmp_path / "nodus.toml")
    (tmp_path / "hello.py").write_text("print('ready')")
    executable = Path(sysconfig.get_path("scripts")) / ("nodus.exe" if os.name == "nt" else "nodus")
    result = subprocess.run([str(executable), *args], cwd=tmp_path, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert "Traceback" not in result.stderr
    assert "\x1b" not in result.stdout
    assert "elapsed" not in result.stderr
    if args[0] == "download":
        assert (tmp_path / "outputs/wl_docs/summarize/result").read_bytes() == DATA


def test_login_saved_credentials_and_logout_in_separate_processes(docs_api, tmp_path):
    bootstrap = (
        "import sys,pathlib,nodus.config,nodus.cli; "
        f"nodus.config.config_path=lambda:pathlib.Path({str(tmp_path / 'config.toml')!r}); "
        "raise SystemExit(nodus.cli.main(sys.argv[1:]))"
    )
    env = dict(os.environ)
    env.pop("NODUS_API_KEY", None)
    for args in (["login", "--no-browser"], ["list"], ["logout"]):
        result = subprocess.run([sys.executable, "-c", bootstrap, *args], env=env,
                                cwd=tmp_path, capture_output=True, text=True, timeout=20)
        assert result.returncode == 0, result.stderr
        assert "nk_docs" not in result.stdout

    result = subprocess.run([sys.executable, "-c", bootstrap, "list"], env=env,
                            cwd=tmp_path, capture_output=True, text=True, timeout=20)
    assert result.returncode == 2
    assert "nodus login" in result.stderr


def test_status_prints_the_public_value_without_changing_json():
    status = nodus.WorkloadStatus.COMPLETED
    assert f"Status: {status}" == "Status: completed"
    assert status == "completed"
    assert json.dumps({"status": status}) == '{"status": "completed"}'
    assert nodus.WorkloadStatus.coerce("future_status") == "future_status"
