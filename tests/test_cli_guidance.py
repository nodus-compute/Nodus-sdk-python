import shlex
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from nodus import cli


@pytest.mark.parametrize("filename", ["nodus.toml", "training.toml", "my training.toml"])
def test_init_guidance_names_created_file(filename, monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)
    assert cli.main(["init", filename]) == 0
    assert (tmp_path / filename).is_file()
    output = capsys.readouterr().out
    if filename == "nodus.toml":
        assert "then run nodus run." in output
    else:
        command = next(line for line in output.splitlines() if line.startswith("nodus run "))
        assert shlex.split(command) == ["nodus", "run", filename]
        assert f'"{filename}"' in command


@pytest.mark.parametrize("filename", ["Sam's $training`file.toml", "line\nbreak.toml", "-training.toml", r"\\server\training.toml"])
def test_init_metacharacter_guidance_does_not_offer_unsafe_command(filename, monkeypatch, capsys):
    monkeypatch.setattr(cli, "write_workload_file", lambda path: path)
    assert cli._cmd_init(SimpleNamespace(file=filename)) == 0
    output = capsys.readouterr().out
    assert "pass its filename to nodus run" in output
    assert not any(line.startswith("nodus run ") for line in output.splitlines())


@pytest.mark.parametrize("status,message", [
    (None, "No runs yet. Start with nodus init, then nodus run."),
    ("active", "No active runs."),
    ("failed", "No runs with status failed."),
    ("mine", "No runs submitted by you."),
    ("team", "No runs for this team."),
])
def test_empty_list_guidance_matches_filter(status, message, monkeypatch, capsys):
    client = MagicMock()
    client.__enter__.return_value = client
    client.list.return_value = []
    monkeypatch.setattr(cli, "Client", lambda **kwargs: client)
    args = SimpleNamespace(base_url=None, status=status, limit=20, json=False, plain=True)
    assert cli._cmd_list(args) == 0
    output = capsys.readouterr().out
    assert message in output
    if status:
        assert "Start with" not in output
    filters = {"scope": status} if status in ("mine", "team") else {"status": status}
    client.list.assert_called_once_with(limit=20, **filters)
@pytest.mark.parametrize('data,expected', [
    ({'offer_id': 'nodus:compute-0-any', 'fit_class': 'compute'}, 'Not reported'),
    ({'resources': {'accelerator': '\x1b[31mRTX\n4090', 'device_memory_gb': 24}}, 'RTX 4090 (24 GB)'),
])
def test_status_and_list_use_same_reported_compute(data, expected, monkeypatch, capsys):
    from nodus._terminal import workload_rows
    from nodus.types import Route
    workload = SimpleNamespace(id='wl_fixture', status='completed', route=Route.from_dict(data),
                               cost_now_usd=0, stages=[], raw={})
    assert dict(workload_rows(workload))['Compute'] == expected
    client = MagicMock()
    client.__enter__.return_value = client
    client.list.return_value = [workload]
    monkeypatch.setattr(cli, 'Client', lambda **kwargs: client)
    args = SimpleNamespace(base_url=None, status=None, limit=20, json=False, plain=True)
    assert cli._cmd_list(args) == 0
    output = capsys.readouterr().out
    assert expected in output
    assert 'nodus:compute' not in output
    assert '\x1b' not in output
    client.get.assert_not_called()
