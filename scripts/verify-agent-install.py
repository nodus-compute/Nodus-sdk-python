"""Run a native installer against a local API with an isolated home."""

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading


def render_installer(destination: Path) -> Path:
    root = Path(__file__).resolve().parents[1]
    plugin = root / "plugins/nodus"
    manifest = json.loads((plugin / ".mcp.json").read_text())
    version = manifest["mcpServers"]["nodus"]["args"][1].split("==")[1]
    skills = {name: (plugin / "skills" / name / "SKILL.md").read_text(encoding="utf-8")
              for name in ("setup", "workloads")}
    source = (root / "install/connect.py").read_text(encoding="utf-8").replace(
        "SKILL_SOURCES = {}", "SKILL_SOURCES = " + json.dumps(skills))
    filename = "install.ps1" if os.name == "nt" else "install.sh"
    content = (root / "install" / filename).read_text(encoding="utf-8")
    content = content.replace("@@SDK_VERSION@@", version).replace("@@CONNECT_SOURCE@@", source)
    assert "@@" not in content
    target = destination / filename
    target.write_text(content, encoding="utf-8")
    return target


def run_installer(installer: Path, env: dict) -> subprocess.CompletedProcess:
    if os.name != "nt":
        return subprocess.run(["sh", "-s", "--", "--agents", "claude,codex,cursor,vscode,gemini,opencode", "--yes"],
                              input=installer.read_bytes(), env=env, capture_output=True, timeout=300)
    quoted = str(installer.resolve()).replace("'", "''")
    driver = f"""
$ErrorActionPreference = 'Stop'
$before = @{{}}
Get-ChildItem Env: | Where-Object Name -Match '^(UV_|PIP_|PYTHON)' | ForEach-Object {{ $before[$_.Name] = $_.Value }}
$failed = $false
try {{ & '{quoted}' --agents 'claude,codex,cursor,vscode,gemini,opencode' --yes }}
catch {{ Write-Output $_.Exception.Message; $failed = $true }}
foreach ($key in $before.Keys) {{
    if ([Environment]::GetEnvironmentVariable($key, 'Process') -ne $before[$key]) {{
        Write-Output 'Caller environment was not restored'
        exit 2
    }}
}}
if ($failed) {{ exit 1 }}
"""
    native_env = {key: value for key, value in env.items() if key.upper() != "PSMODULEPATH"}
    return subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", driver],
                          env=native_env, capture_output=True, timeout=300)


def verify(installer: Path) -> None:
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append((self.path, self.headers.get("Authorization")))
            payload = {"email": "installer@example.test"} if self.path == "/v1/me" else {"workloads": [], "next_offset": None}
            content = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        with tempfile.TemporaryDirectory(prefix="nodus-install-check-") as directory:
            root = Path(directory).resolve()
            temporary = root / "temp"
            temporary.mkdir()
            env = {key: value for key, value in os.environ.items()
                   if not key.startswith(("NODUS_", "UV_", "PIP_", "PYTHON", "CODEX_", "CLAUDE_", "XDG_", "GH_", "GITHUB_"))}
            env.update(HOME=str(root), USERPROFILE=str(root), APPDATA=str(root / "AppData/Roaming"),
                       LOCALAPPDATA=str(root / "AppData/Local"),
                       TEMP=str(temporary), TMP=str(temporary), TMPDIR=str(temporary),
                       UV_EXTRA_INDEX_URL="http://127.0.0.1:1/unwanted-index",
                       PIP_FIND_LINKS=str(root / "unwanted-links"), PYTHONPATH=str(root / "unwanted-python"),
                       NODUS_API_KEY="installer-test-key", NODUS_BASE_URL=f"http://127.0.0.1:{server.server_port}")
            code = ("AppData/Roaming/Code/User/mcp.json" if os.name == "nt" else
                    "Library/Application Support/Code/User/mcp.json" if sys.platform == "darwin" else
                    ".config/Code/User/mcp.json")
            paths = [".claude.json", ".codex/config.toml", ".cursor/mcp.json",
                     code, ".gemini/settings.json", ".config/opencode/opencode.json"]
            for attempt in range(2):
                result = run_installer(installer, env)
                if result.returncode:
                    raise RuntimeError(result.stdout.decode() + result.stderr.decode())
                assert "Nodus tools verified" in result.stdout.decode()
                current = {path: (root / path).read_bytes() for path in paths}
                if attempt:
                    assert current == first
                else:
                    first = current
                assert "installer-test-key" not in result.stdout.decode() + result.stderr.decode()
                print(f"Pass {attempt + 1}: six clients configured, MCP workload read verified")
            assert requests == [("/v1/me", "Bearer installer-test-key"),
                                ("/v1/workloads?limit=1", "Bearer installer-test-key")] * 2, requests
            launch = json.loads((root / ".cursor/mcp.json").read_text())["mcpServers"]["nodus"]
            assert Path(launch["command"]).is_absolute()
            assert launch["args"][0] == "-I"
            assert (root / ".cursor/skills/nodus-setup/SKILL.md").is_file()
            assert (root / ".agents/skills/nodus-workloads/SKILL.md").is_file()
            (root / ".cursor/mcp.json").write_text('{"mcpServers":{"nodus":{"command":"keep-existing"}}}')
            before_failure = {path: (root / path).read_bytes() for path in paths}
            result = run_installer(installer, env)
            assert result.returncode == 1, result.stdout.decode(errors="replace") + result.stderr.decode(errors="replace")
            assert {path: (root / path).read_bytes() for path in paths} == before_failure
            assert len(requests) == 4, requests
            assert "installer-test-key" not in result.stdout.decode(errors="replace") + result.stderr.decode(errors="replace")
            assert not list(temporary.glob("nodus-*")), "Bootstrap temporary files were not cleaned up"
            lock = root / ".nodus/agent-tools/install.lock"
            if lock.exists():
                lock.unlink()
            print("Read-only API calls, isolated absolute runtime, bundled skills, repeat install unchanged")
            print("Conflict refusal preserved settings, restored caller environment and released setup files")
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--installer", type=Path, help="use a generated installer instead of rendering the native source")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="nodus-installer-source-") as directory:
        verify(args.installer or render_installer(Path(directory)))
