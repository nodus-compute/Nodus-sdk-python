"""The public action retains submission identity and verifies downloaded results."""
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
import threading

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "actions/run/run.py"


@pytest.fixture
def action_api():
    calls = []
    data = b"verified customer result"
    state = {"status": "completed", "digest": hashlib.sha256(data).hexdigest()}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
            calls.append((self.path, self.headers.get("Idempotency-Key"), body))
            self.reply({"id": "wl_action", "status": "queued"}, 202)

        def do_GET(self):
            if self.path.endswith("/outputs/result?stage=train"):
                self.send_response(200)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("X-Nodus-SHA256", state["digest"])
                self.end_headers()
                self.wfile.write(data)
            elif self.path.endswith("/outputs"):
                state["list_count"] = state.get("list_count", 0) + 1
                items = [{"name": "result", "stage_id": "train", "sha256": state["digest"], "bytes": len(data), "download": "/v1/workloads/wl_action/outputs/result?stage=train"}]
                self.reply({"outputs": [] if state.get("changing_outputs") and state["list_count"] > 1 else items})
            elif self.path == "/v1/workloads/wl_action":
                self.reply({"id": "wl_action", "status": state["status"]})
            else:
                self.reply({})

        def reply(self, body, status=200):
            raw = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}", calls, state, data
    server.shutdown()
    server.server_close()
    thread.join()


def run_action(tmp_path, api, attempt="1", budget=True, api_key="workflow-test-key", with_origin=True):
    origin, _, _, _ = api
    workload = tmp_path / "train.toml"
    workload.write_text('image = "customer/trainer:v1"\ncommand = ["python", "train.py"]\n' + ('budget = 10\n' if budget else '') + '[outputs]\nresult = "result.txt"\n')
    env = dict(os.environ)
    env.update(NODUS_API_KEY=api_key, NODUS_BASE_URL=origin,
               NODUS_WORKLOAD_FILE=str(workload), NODUS_OUTPUT_DIRECTORY=str(tmp_path / ("results-" + attempt)),
               NODUS_WAIT_TIMEOUT="30", GITHUB_REPOSITORY="customer/project", GITHUB_RUN_ID="1234",
               GITHUB_JOB="training", GITHUB_RUN_ATTEMPT=attempt, GITHUB_OUTPUT=str(tmp_path / "github-output"),
               HOME=str(tmp_path / "home"), USERPROFILE=str(tmp_path / "home"))
    env.pop("NODUS_IDEMPOTENCY_KEY", None)
    if not with_origin:
        env.pop("NODUS_BASE_URL", None)
    return subprocess.run([sys.executable, str(SCRIPT)], env=env, text=True, capture_output=True, timeout=20)


def test_action_retry_keeps_submission_key_and_verifies_files(tmp_path, action_api):
    _, calls, _, data = action_api
    first = run_action(tmp_path, action_api)
    assert first.returncode == 0, first.stderr
    assert list((tmp_path / "results-1").rglob("result"))[0].read_bytes() == data
    second = run_action(tmp_path, action_api, "2")
    assert second.returncode == 0, second.stderr
    assert len(calls) == 2
    assert calls[0][1] == calls[1][1]
    assert calls[0][2]["outcome"]["max_cost_usd"] == 10
    assert "workload-id=wl_action" in (tmp_path / "github-output").read_text()
    assert "workflow-test-key" not in first.stdout + first.stderr


@pytest.mark.parametrize("failure", ["failed", "checksum", "missing_budget"])
def test_action_refuses_false_success(tmp_path, action_api, failure):
    _, calls, state, _ = action_api
    if failure == "failed":
        state["status"] = "failed"
    if failure == "checksum":
        state["digest"] = "0" * 64
    result = run_action(tmp_path, action_api, budget=failure != "missing_budget")
    assert result.returncode != 0
    assert "verified outputs" not in result.stdout
    if failure == "missing_budget":
        assert calls == []


@pytest.mark.parametrize("api_key", ["", "   "])
def test_action_requires_its_own_credential_even_with_saved_login(tmp_path, action_api, api_key):
    origin, calls, _, _ = action_api
    saved = tmp_path / "home/.nodus/config.toml"
    saved.parent.mkdir(parents=True, exist_ok=True)
    saved.write_text(f'[default]\napi_key = "saved-other-account-key"\nbase_url = "{origin}"\n')
    result = run_action(tmp_path, action_api, api_key=api_key)
    assert result.returncode != 0
    assert calls == []
    assert "saved-other-account-key" not in result.stdout + result.stderr


def test_action_downloads_every_output_from_the_checked_manifest(tmp_path, action_api):
    _, _, state, data = action_api
    state["changing_outputs"] = True
    result = run_action(tmp_path, action_api)
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "results-1/train/result").read_bytes() == data
    assert state["list_count"] == 1


def test_action_does_not_use_an_origin_from_saved_login(tmp_path, action_api):
    origin, calls, _, _ = action_api
    saved = tmp_path / "home/.nodus/config.toml"
    saved.parent.mkdir(parents=True, exist_ok=True)
    saved.write_text(f'[default]\napi_key = "saved-other-key"\nbase_url = "{origin}"\n')
    result = run_action(tmp_path, action_api, with_origin=False)
    assert result.returncode != 0
    assert calls == []
