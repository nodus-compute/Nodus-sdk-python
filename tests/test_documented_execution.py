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
DATA = b'{"sum": 6}\n'
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
            payload = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            calls.append(("POST", path))
            if path == "/v1/console/device/start":
                return self.reply({"device_code": "device-test", "user_code": "ABCD-EFGH",
                    "verification_url": "https://console.nodus-compute.ai/device?code=ABCD-EFGH", "interval": 1, "expires_in": 60})
            if path == "/v1/console/device/token":
                return self.reply({"api_key": "nk_docs", "base_url": address, "tenant": "docs-test"})
            if self.headers.get("Authorization") != "Bearer nk_docs":
                return self.reply({"error": "unauthorized"}, 401)
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
            if len(command) >= 3 and command[0] == "python" and command[1] == "-c":
                ast.parse(command[2])


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
    ["run", "--compute-class", "accelerator", "--image", "pytorch/pytorch:2.6.0-cuda12.4-cudnn9-runtime",
     "--budget", "5", "--wait", "--", "python", "-c", "print('ready')"],
    ["list", "--status", "active", "--limit", "10"],
    ["get", "wl_docs", "--json"], ["get", "wl_docs", "--wait"],
    ["events", "wl_docs", "--follow"], ["logs", "wl_docs", "--tail", "50"],
    ["artifacts", "wl_docs"], ["explain", "wl_docs"], ["ledger", "wl_docs"],
    ["ledger", "wl_docs", "--json"], ["cancel", "wl_docs"],
])
def test_installed_terminal_commands(args, docs_api, tmp_path):
    executable = Path(sysconfig.get_path("scripts")) / ("nodus.exe" if os.name == "nt" else "nodus")
    result = subprocess.run([str(executable), *args], cwd=tmp_path, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert "Traceback" not in result.stderr
    assert "\x1b" not in result.stdout
    assert "elapsed" not in result.stderr


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
