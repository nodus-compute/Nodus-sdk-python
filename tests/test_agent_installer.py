"""Customer setup preserves existing agents and verifies a read before success."""

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "install/connect.py"


def test_dry_run_explains_selected_agents_without_creating_files(tmp_path):
    before = list(tmp_path.iterdir())
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("NODUS_", "CODEX_", "CLAUDE_", "XDG_"))}
    env.update(HOME=str(tmp_path), USERPROFILE=str(tmp_path))
    result = subprocess.run([sys.executable, str(SCRIPT), "--agents", "cursor,codex", "--dry-run"],
                            env=env, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert "Cursor" in result.stdout and "Codex" in result.stdout
    assert "Dry run" in result.stdout
    assert list(tmp_path.iterdir()) == before


@pytest.fixture
def installer():
    spec = importlib.util.spec_from_file_location("nodus_agent_installer", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_config_merge_preserves_settings_and_backup_and_is_repeatable(installer, tmp_path):
    path = tmp_path / "mcp.json"
    original = '// my settings\n{"mcpServers":{"other":{"command":"keep"}},"theme":"dark",}\n'
    path.write_text(original)
    server = {"command": "/private/nodus-mcp", "args": []}
    edit = installer.config_edit(path, "json", "mcpServers", server)
    installer.apply_edits([edit])
    content = json.loads(path.read_text())
    assert content == {"mcpServers": {"other": {"command": "keep"}, "nodus": server}, "theme": "dark"}
    backups = list(tmp_path.glob("*.nodus-backup-*"))
    assert len(backups) == 1 and backups[0].read_text() == original
    if os.name == "posix":
        assert backups[0].stat().st_mode & 0o777 == 0o600
    assert installer.config_edit(path, "json", "mcpServers", server) is None
    assert len(list(tmp_path.glob("*.nodus-backup-*"))) == 1


def test_codex_preserves_comments_and_unrelated_tables(installer, tmp_path):
    import tomlkit
    path = tmp_path / "config.toml"
    original = '# keep this\nmodel = "custom"\n[mcp_servers.other]\ncommand = "keep"\n'
    path.write_text(original)
    server = {"command": "/private/nodus-mcp", "args": []}
    installer.apply_edits([installer.config_edit(path, "toml", "mcp_servers", server)])
    assert path.read_text().startswith(original)
    result = tomlkit.parse(path.read_text())
    assert result["model"] == "custom" and result["mcp_servers"]["other"]["command"] == "keep"
    assert result["mcp_servers"]["nodus"] == server


@pytest.mark.parametrize("content", ['{broken secret-value', '{"mcpServers":42}',
                                      '{"mcpServers":{},"mcpServers":{}}',
                                      '{"mcpServers":{"nodus":{"command":"custom"}}}'])
def test_invalid_or_conflicting_settings_are_not_overwritten(installer, tmp_path, content):
    path = tmp_path / "config.json"
    path.write_text(content)
    before = list(tmp_path.iterdir())
    with pytest.raises(installer.SetupError) as caught:
        installer.config_edit(path, "json", "mcpServers", {"command": "/nodus-mcp", "args": []})
    assert "secret-value" not in str(caught.value)
    assert path.read_text() == content
    assert list(tmp_path.iterdir()) == before


def test_symlinks_are_refused(installer, tmp_path):
    actual = tmp_path / "actual.json"
    actual.write_text('{}')
    link = tmp_path / "linked.json"
    try:
        link.symlink_to(actual)
    except OSError as exc:
        if os.name == "nt" and getattr(exc, "winerror", None) == 1314:
            pytest.skip("Windows symlink creation requires Developer Mode or the symlink privilege")
        raise
    with pytest.raises(installer.SetupError):
        installer.config_edit(link, "json", "mcpServers", {"command": "/nodus-mcp"})


def test_changes_during_setup_are_refused(installer, tmp_path):
    actual = tmp_path / "actual.json"
    actual.write_text('{}')
    edit = installer.config_edit(actual, "json", "mcpServers", {"command": "/nodus-mcp"})
    actual.write_text('{"new":"setting"}')
    with pytest.raises(installer.SetupError):
        installer.apply_edits([edit])
    assert actual.read_text() == '{"new":"setting"}'


def test_no_success_or_config_write_after_verification_failure(installer, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(installer.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(installer, "sign_in", lambda *_: None)
    async def fail(*_):
        raise installer.SetupError("Connection check failed")
    monkeypatch.setattr(installer, "verify", fail)
    assert installer.main(["--agents", "cursor", "--yes"]) == 1
    assert not (tmp_path / ".cursor/mcp.json").exists()
    assert "Connected" not in capsys.readouterr().out


def test_existing_claude_plugin_prevents_duplicate_connection(installer, tmp_path, monkeypatch):
    monkeypatch.setattr(installer.Path, "home", lambda: tmp_path)
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    directory = tmp_path / ".claude"
    directory.mkdir()
    (directory / "settings.json").write_text('{"enabledPlugins":{"nodus@nodus":true}}')
    with pytest.raises(installer.SetupError, match="plugin"):
        installer.plan(["claude"], sys.executable)
    assert not (tmp_path / ".claude.json").exists()


@pytest.mark.parametrize(("agent", "plugin_config"), [
    ("codex", '[plugins."nodus@nodus"]\nenabled = true\n'),
    ("codex", '[plugins."nodus@nodus"]\n'),
    ("cursor", None),
])
def test_existing_plugin_locations_prevent_duplicate_connections(installer, tmp_path, monkeypatch, agent, plugin_config):
    monkeypatch.setattr(installer.Path, "home", lambda: tmp_path)
    monkeypatch.delenv("CODEX_HOME", raising=False)
    if agent == "codex":
        directory = tmp_path / ".codex"
        directory.mkdir()
        (directory / "config.toml").write_text(plugin_config)
    else:
        (tmp_path / ".cursor/plugins/local/nodus").mkdir(parents=True)
    with pytest.raises(installer.SetupError, match="plugin"):
        installer.plan([agent], sys.executable)
