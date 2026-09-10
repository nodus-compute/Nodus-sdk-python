"""Execute documentation through the installed SDK and a local HTTP test server.

The server simulates completion. These checks do not execute GPU containers.
"""
from __future__ import annotations

import ast
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
            if path == "/v1/assets":
                return self.reply({"assets": [], "max_import_bytes": 1048576})
            if path == "/v1/workloads":
                return self.reply({"workloads": [row]})
            if path == "/v1/workloads/wl_docs":
                return self.reply(row)
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
            if path == "/v1/estimation/estimate":
                return self.reply({"status": "unavailable", "scope": "stage" if payload.get("stage_id") else "workload",
                    **({"stage_id": payload["stage_id"]} if payload.get("stage_id") else {}),
                    "execution_seconds": None, "completion_seconds": None, "compute_cost_usd": None,
                    "valid_until": None, "reasons": ["estimate_service_unavailable"], "diagnostics": [{
                        "code": "estimate_service_unavailable", "message": "Estimates are not enabled for this account.",
                        "action": "You can submit with a spending limit, or contact Nodus to enable estimates."}]})
            if path == "/v1/workloads":
                submissions.append(payload)
                if not self.headers.get("Idempotency-Key") or not payload.get("outcome", {}).get("max_cost_usd"):
                    return self.reply({"error": "invalid_brief"}, 400)
                return self.reply({"id": "wl_docs", "workload_id": "wl_docs", "status": "accepted", "revision": 1}, 202)
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
        namespace = {"__name__": "__docs__", "client": client, "nodus": nodus,
                     "workload_id": "wl_docs", "allowed_regions": ["test-region"],
                     "done": client.get("wl_docs"), "workload": client.get("wl_docs")}
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
    ["estimate"], ["estimate", "train.toml"], ["estimate", "train.toml", "--stage", "main"],
    ["estimate", "train.toml", "--json"],
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
    write_workload_file(tmp_path / "train.toml")
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
