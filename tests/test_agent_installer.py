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


def test_repair_updates_owned_runtime_and_skills_without_touching_other_settings(installer, tmp_path, monkeypatch):
    monkeypatch.setattr(installer.Path, "home", lambda: tmp_path)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    installer.SKILL_SOURCES = {"setup": "name: setup\nold", "workloads": "name: workloads\nold"}
    installer.apply_edits(installer.plan(["cursor"], "/old/nodus/python"))
    config = tmp_path / ".cursor/mcp.json"
    data = json.loads(config.read_text())
    data["mcpServers"]["other"] = {"command": "keep-me"}
    config.write_text(json.dumps(data))
    installer.SKILL_SOURCES = {"setup": "name: setup\nnew", "workloads": "name: workloads\nnew"}
    installer.apply_edits(installer.plan(["cursor"], "/new/nodus/python", repair=True))
    data = json.loads(config.read_text())
    assert data["mcpServers"]["nodus"]["command"] == "/new/nodus/python"
    assert data["mcpServers"]["other"] == {"command": "keep-me"}
    assert (tmp_path / ".cursor/skills/nodus-workloads/SKILL.md").read_text().endswith("new")
    assert list(config.parent.glob("*.nodus-backup-*"))


def test_repair_refuses_customer_changes_to_managed_connection(installer, tmp_path, monkeypatch):
    monkeypatch.setattr(installer.Path, "home", lambda: tmp_path)
    installer.apply_edits(installer.plan(["cursor"], "/old/nodus/python"))
    config = tmp_path / ".cursor/mcp.json"
    changed = '{"mcpServers":{"nodus":{"url":"https://my-custom-endpoint.test/mcp"}}}'
    config.write_text(changed)
    with pytest.raises(installer.SetupError):
        installer.plan(["cursor"], "/new/nodus/python", repair=True)
    assert config.read_text() == changed


def test_repair_adopts_legacy_installer_runtime_without_executing_it(installer, tmp_path, monkeypatch):
    monkeypatch.setattr(installer.Path, "home", lambda: tmp_path)
    path = tmp_path / ".cursor/mcp.json"
    path.parent.mkdir()
    legacy = tmp_path / ".nodus/agent-tools/0.4.2-1/bin/python"
    path.write_text(json.dumps({"mcpServers": {"nodus": {"command": str(legacy), "args": installer.MCP_ARGS}}}))
    installer.apply_edits(installer.plan(["cursor"], "/new/runtime/python", repair=True))
    assert json.loads(path.read_text())["mcpServers"]["nodus"]["command"] == "/new/runtime/python"
    assert not legacy.exists()


@pytest.mark.parametrize("version", ["..", "-", "0.4.2-.."])
def test_repair_refuses_unowned_runtime_path(installer, tmp_path, monkeypatch, version):
    monkeypatch.setattr(installer.Path, "home", lambda: tmp_path)
    path = tmp_path / ".cursor/mcp.json"
    path.parent.mkdir()
    original = json.dumps({"mcpServers": {"nodus": {
        "command": str(tmp_path / ".nodus/agent-tools" / version / "bin/python"), "args": installer.MCP_ARGS}}})
    path.write_text(original)
    with pytest.raises(installer.SetupError):
        installer.plan(["cursor"], "/new/runtime/python", repair=True)
    assert path.read_text() == original


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


@pytest.fixture
def edit_batch(installer, tmp_path):
    edits = [
        installer.Edit(tmp_path / "first.json", b'{"theme":"dark"}', b'{"theme":"dark","nodus":true}'),
        installer.Edit(tmp_path / "SKILL.md", None, b"Nodus skill"),
        installer.Edit(tmp_path / "last.toml", b'model = "custom"\n', b'model = "custom"\n[nodus]\n'),
    ]
    for edit in edits:
        if edit.before is not None:
            edit.path.write_bytes(edit.before)
    (tmp_path / "unrelated.json").write_bytes(b"untouched")
    return edits


def test_later_replace_failure_restores_existing_files_and_removes_new_files(installer, edit_batch, monkeypatch):
    first, skill, last = edit_batch
    replace = os.replace

    def fail_last(source, destination):
        if Path(destination) == last.path:
            raise OSError("private-config-value")
        return replace(source, destination)

    monkeypatch.setattr(os, "replace", fail_last)
    with pytest.raises((installer.SetupError, OSError)) as caught:
        installer.apply_edits(edit_batch)
    assert first.path.read_bytes() == first.before
    assert not skill.path.exists()
    assert last.path.read_bytes() == last.before
    assert (first.path.parent / "unrelated.json").read_bytes() == b"untouched"
    assert not list(first.path.parent.glob(".nodus-*"))
    assert isinstance(caught.value, installer.SetupError)
    assert "restored" in str(caught.value).lower()
    assert "private-config-value" not in str(caught.value)


def test_staging_write_failure_leaves_all_originals_untouched(installer, edit_batch, monkeypatch):
    first, skill, last = edit_batch
    mkstemp, fdopen = installer.tempfile.mkstemp, os.fdopen
    failing_descriptors = set()
    replacements = []

    def track_temporary(*args, **kwargs):
        descriptor, path = mkstemp(*args, **kwargs)
        if kwargs.get("prefix") == ".nodus-":
            replacements.append(path)
            if len(replacements) == 2:
                failing_descriptors.add(descriptor)
        return descriptor, path

    class FailedWrite:
        def __init__(self, file):
            self.file = file

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.file.close()

        def write(self, content):
            self.file.write(content[:1])
            raise OSError("private-config-value")

    def fail_write(descriptor, *args, **kwargs):
        file = fdopen(descriptor, *args, **kwargs)
        return FailedWrite(file) if descriptor in failing_descriptors else file

    monkeypatch.setattr(installer.tempfile, "mkstemp", track_temporary)
    monkeypatch.setattr(os, "fdopen", fail_write)
    with pytest.raises((installer.SetupError, OSError)) as caught:
        installer.apply_edits(edit_batch)
    assert first.path.read_bytes() == first.before
    assert not skill.path.exists()
    assert last.path.read_bytes() == last.before
    assert not list(first.path.parent.glob(".nodus-*"))
    assert not list(first.path.parent.glob("*.nodus-backup-*"))
    assert isinstance(caught.value, installer.SetupError)
    assert "private-config-value" not in str(caught.value)


def test_later_concurrent_change_rolls_back_completed_edits(installer, edit_batch, monkeypatch):
    first, skill, last = edit_batch
    replace = os.replace

    def change_next(source, destination):
        result = replace(source, destination)
        if Path(destination) == first.path:
            last.path.write_bytes(b"concurrent settings")
        return result

    monkeypatch.setattr(os, "replace", change_next)
    with pytest.raises(installer.SetupError):
        installer.apply_edits(edit_batch)
    assert first.path.read_bytes() == first.before
    assert not skill.path.exists()
    assert last.path.read_bytes() == b"concurrent settings"


@pytest.mark.parametrize("changed_index", [0, 1])
def test_rollback_preserves_concurrent_edits_and_retains_original_backups(installer, edit_batch, monkeypatch, changed_index):
    first, skill, last = edit_batch
    changed = edit_batch[changed_index]
    replace = os.replace

    def fail_after_external_change(source, destination):
        if Path(destination) == last.path:
            changed.path.write_bytes(b"concurrent settings")
            raise OSError("private-config-value")
        return replace(source, destination)

    monkeypatch.setattr(os, "replace", fail_after_external_change)
    with pytest.raises((installer.SetupError, OSError)) as caught:
        installer.apply_edits(edit_batch)
    assert changed.path.read_bytes() == b"concurrent settings"
    if changed_index == 0:
        assert not skill.path.exists()
        backups = list(first.path.parent.glob("first.json.nodus-backup-*"))
        assert len(backups) == 1 and backups[0].read_bytes() == first.before
        if os.name == "posix":
            assert backups[0].stat().st_mode & 0o777 == 0o600
    else:
        assert first.path.read_bytes() == first.before
    assert last.path.read_bytes() == last.before
    assert isinstance(caught.value, installer.SetupError)
    assert "rollback incomplete" in str(caught.value).lower()
    assert "concurrent" in str(caught.value).lower()
    assert "private-config-value" not in str(caught.value)


def test_rollback_failure_is_reported_with_private_original_backup(installer, edit_batch, monkeypatch):
    first, skill, last = edit_batch
    replace = os.replace
    first_replaced = False

    def fail_commit_and_restore(source, destination):
        nonlocal first_replaced
        if Path(destination) == last.path or (Path(destination) == first.path and first_replaced):
            raise OSError("private-config-value")
        result = replace(source, destination)
        if Path(destination) == first.path:
            first_replaced = True
        return result

    monkeypatch.setattr(os, "replace", fail_commit_and_restore)
    with pytest.raises((installer.SetupError, OSError)) as caught:
        installer.apply_edits(edit_batch)
    assert first.path.read_bytes() == first.after
    assert not skill.path.exists()
    assert last.path.read_bytes() == last.before
    backups = list(first.path.parent.glob("first.json.nodus-backup-*"))
    assert len(backups) == 1 and backups[0].read_bytes() == first.before
    if os.name == "posix":
        assert backups[0].stat().st_mode & 0o777 == 0o600
    assert isinstance(caught.value, installer.SetupError)
    assert "rollback incomplete" in str(caught.value).lower()
    assert "backup" in str(caught.value).lower()
    assert "private-config-value" not in str(caught.value)


def test_no_success_or_config_write_after_verification_failure(installer, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(installer.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(installer, "sign_in", lambda *_: None)
    async def fail(*_):
        raise installer.SetupError("Connection check failed")
    monkeypatch.setattr(installer, "verify", fail)
    assert installer.main(["--agents", "cursor", "--yes"]) == 1
    assert not (tmp_path / ".cursor/mcp.json").exists()
    assert "Connected" not in capsys.readouterr().out


@pytest.mark.parametrize("plugin", ["nodus", "nodus-hosted"])
def test_existing_claude_plugin_prevents_duplicate_connection(installer, tmp_path, monkeypatch, plugin):
    monkeypatch.setattr(installer.Path, "home", lambda: tmp_path)
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    directory = tmp_path / ".claude"
    directory.mkdir()
    (directory / "settings.json").write_text(json.dumps({"enabledPlugins": {plugin + "@nodus": True}}))
    with pytest.raises(installer.SetupError, match="plugin"):
        installer.plan(["claude"], sys.executable)
    assert not (tmp_path / ".claude.json").exists()


@pytest.mark.parametrize("plugin", ["nodus", "nodus-hosted"])
@pytest.mark.parametrize(("agent", "plugin_config"), [
    ("codex", '[plugins."nodus@nodus"]\nenabled = true\n'),
    ("codex", '[plugins."nodus@nodus"]\n'),
    ("cursor", None),
])
def test_existing_plugin_locations_prevent_duplicate_connections(installer, tmp_path, monkeypatch, agent, plugin_config, plugin):
    monkeypatch.setattr(installer.Path, "home", lambda: tmp_path)
    monkeypatch.delenv("CODEX_HOME", raising=False)
    if agent == "codex":
        directory = tmp_path / ".codex"
        directory.mkdir()
        (directory / "config.toml").write_text(plugin_config.replace("nodus@nodus", plugin + "@nodus"))
    else:
        (tmp_path / ".cursor/plugins/local" / plugin).mkdir(parents=True)
    with pytest.raises(installer.SetupError, match="plugin"):
        installer.plan([agent], sys.executable)


def test_check_verifies_owned_older_runtime_without_changing_settings(installer, tmp_path, monkeypatch):
    monkeypatch.setattr(installer.Path, "home", lambda: tmp_path)
    installer.SKILL_SOURCES = {"setup": "name: setup\nold", "workloads": "name: workloads\nold"}
    installer.apply_edits(installer.plan(["cursor"], "/old/nodus/python"))
    before = {str(path): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    installer.SKILL_SOURCES = {"setup": "name: setup\nnew", "workloads": "name: workloads\nnew"}
    checked = []
    async def verify(command):
        checked.append(command)
    monkeypatch.setattr(installer, "verify", verify)
    assert installer.main(["--agents", "cursor", "--check"]) == 0
    assert checked == ["/old/nodus/python"]
    assert {str(path): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()} == before
