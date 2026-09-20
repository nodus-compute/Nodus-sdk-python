"""Run the generated shell installer against a local API with an isolated home."""

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading


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
            root = Path(directory)
            env = {key: value for key, value in os.environ.items()
                   if not key.startswith(("NODUS_", "UV_", "PIP_", "PYTHON", "CODEX_", "CLAUDE_", "XDG_", "GH_", "GITHUB_"))}
            env.update(HOME=directory, USERPROFILE=directory,
                       NODUS_API_KEY="installer-test-key", NODUS_BASE_URL=f"http://127.0.0.1:{server.server_port}")
            paths = [".claude.json", ".codex/config.toml", ".cursor/mcp.json",
                     ".config/Code/User/mcp.json", ".gemini/settings.json", ".config/opencode/opencode.json"]
            for attempt in range(2):
                result = subprocess.run(["sh", "-s", "--", "--agents", "claude,codex,cursor,vscode,gemini,opencode", "--yes"],
                                        input=installer.read_bytes(), env=env, capture_output=True, timeout=300)
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
            print("Read-only API calls, isolated absolute runtime, bundled skills, repeat install unchanged")
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--installer", type=Path, required=True)
    verify(parser.parse_args().installer)
