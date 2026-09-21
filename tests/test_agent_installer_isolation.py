"""Agent setup uses its own runtime without unrelated Python startup code."""

import asyncio
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
import tomlkit


@pytest.fixture
def installer(monkeypatch, tmp_path):
    source = Path(__file__).resolve().parents[1] / "install/connect.py"
    spec = importlib.util.spec_from_file_location("nodus_installer_isolation", source)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module.Path, "home", lambda: tmp_path)
    for key in list(os.environ):
        if key.startswith(("CODEX_", "CLAUDE_", "XDG_")):
            monkeypatch.delenv(key)
    monkeypatch.setenv("APPDATA", str(tmp_path / "AppData/Roaming"))
    return module


@pytest.fixture(params=["PYTHONPATH", "PYTHONHOME"])
def poisoned_python(tmp_path, request):
    marker = tmp_path / "unrelated-python-ran"
    startup = tmp_path / "python-overrides"
    startup.mkdir()
    (startup / "sitecustomize.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).touch()\n"
    )
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("NODUS_", "PYTHON"))}
    env[request.param] = str(startup if request.param == "PYTHONPATH"
                             else tmp_path / "missing-python-home")
    return env, marker


def test_empty_home_does_not_detect_agents(installer, monkeypatch):
    monkeypatch.setattr(installer.shutil, "which", lambda _: None)
    assert installer.detected() == []


def test_login_uses_runtime_despite_python_overrides(installer, poisoned_python, monkeypatch):
    env, marker = poisoned_python
    run = subprocess.run
    responses = []

    def explain_login(command, **kwargs):
        result = run([*command, "--help"], env=env, capture_output=True, text=True,
                     timeout=15, **kwargs)
        responses.append(result)
        return result

    monkeypatch.setattr(installer.subprocess, "run", explain_login)
    installer.sign_in(True)
    assert len(responses) == 1
    assert "nodus login" in responses[0].stdout
    assert not marker.exists()


@pytest.mark.parametrize("agent", ["cursor", "codex", "opencode"])
def test_configured_agent_discovers_tools_despite_python_overrides(
    installer, poisoned_python, agent
):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    env, marker = poisoned_python
    edits = installer.plan([agent], sys.executable)
    config = next(edit for edit in edits if edit is not None)
    if agent == "codex":
        server = tomlkit.parse(config.after.decode())["mcp_servers"]["nodus"]
        command, args = server["command"], list(server["args"])
    elif agent == "opencode":
        server = json.loads(config.after)["mcp"]["nodus"]
        command, *args = server["command"]
    else:
        server = json.loads(config.after)["mcpServers"]["nodus"]
        command, args = server["command"], server["args"]

    async def discover():
        parameters = StdioServerParameters(command=command, args=args, env=env)
        async with stdio_client(parameters) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                return {tool.name for tool in (await session.list_tools()).tools}

    async def check():
        return await asyncio.wait_for(discover(), timeout=20)

    assert asyncio.run(check()) == {
        "submit_workload", "list_workloads", "get_workload", "get_workload_events",
        "get_workload_logs", "list_workload_outputs", "cancel_workload",
        "validate_workload", "download_workload_output",
    }
    assert not marker.exists()
