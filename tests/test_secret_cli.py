"""Secret values enter through private input and never appear in CLI output."""
import io
import json

import httpx
import pytest
from nodus import cli
from test_sandbox_cli import client_factory


def test_secret_cli_roundtrip_stdin_file_list_and_revoke(monkeypatch, capsys, tmp_path):
    calls = []
    def handler(request):
        body = json.loads(request.content) if request.content else None
        calls.append((request.method, request.url.path, body))
        if request.method == "POST":
            return httpx.Response(201, json={"id": "sec_example", "name": "API_KEY", "version": 2, "created_at": "2026-09-19T00:00:00Z"})
        if request.method == "GET":
            return httpx.Response(200, json={"secrets": [{"id": "sec_example", "name": "API_KEY", "version": 2}]})
        return httpx.Response(204)
    monkeypatch.setattr(cli, "Client", client_factory(handler))
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO("private-stdin-value"))
    assert cli.main(["secret", "set", "API_KEY"]) == 0
    path = tmp_path / "key"
    path.write_text("private-file-value\n")
    assert cli.main(["secret", "set", "API_KEY", "--from-file", str(path)]) == 0
    assert cli.main(["secret", "ls"]) == 0
    assert cli.main(["secret", "rm", "API_KEY"]) == 0
    assert calls[0] == ("POST", "/v1/secrets", {"name": "API_KEY", "value": "private-stdin-value"})
    assert calls[1][2]["value"] == "private-file-value\n"
    assert calls[-1][:2] == ("DELETE", "/v1/secrets/API_KEY")
    output = capsys.readouterr()
    assert "API_KEY" in output.out
    assert "private-" not in output.out + output.err


def test_secret_cli_rejects_oversized_input_without_request(monkeypatch, capsys):
    def handler(request):
        raise AssertionError("invalid secret reached HTTP")
    monkeypatch.setattr(cli, "Client", client_factory(handler))
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO("private-" * 1000))
    assert cli.main(["secret", "set", "API_KEY"]) == 2
    assert "private-" not in capsys.readouterr().err


@pytest.mark.parametrize("status", [400, 422])
@pytest.mark.parametrize("debug", [False, True])
def test_secret_cli_never_echoes_remote_validation_errors(monkeypatch, capsys, status, debug):
    value = "synthetic-private-sentinel"
    def handler(request):
        assert json.loads(request.content)["value"] == value
        return httpx.Response(status, json={"error": "invalid_secret", "message": "Rejected value " + value})
    monkeypatch.setattr(cli, "Client", client_factory(handler))
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO(value))
    argv = (["--debug"] if debug else []) + ["secret", "set", "API_KEY"]
    assert cli.main(argv) == 2
    output = capsys.readouterr()
    assert value not in output.out + output.err
    assert "Secret operation failed" in output.err
