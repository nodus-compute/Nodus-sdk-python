"""Devbox profile submission and named lifecycle through the sandbox API."""

import json

import httpx
import pytest

import nodus
from nodus import cli
from test_sandbox_cli import client_factory

BOX = {"id": "sb_dev", "state": "suspended", "envelope": {"profile": "devbox", "name": "scratch"}, "cost_usd": 0}


def test_devbox_uses_server_defaults_and_explicit_overrides(monkeypatch):
    bodies = []

    def handler(request):
        assert request.method == "POST" and request.url.path == "/v1/sandboxes"
        assert request.headers["Idempotency-Key"]
        bodies.append(json.loads(request.content))
        return httpx.Response(202, json=BOX)

    factory = client_factory(handler)
    clients = iter([factory(), factory()])
    monkeypatch.setattr(nodus, "Client", lambda: next(clients))
    box = nodus.Devbox(name="scratch", image="python:3.12")
    assert isinstance(box, nodus.Sandbox)
    assert box.id == "sb_dev"
    box.close()
    nodus.Devbox(name="scratch", budget=7, policy={"network": "deny"}).close()
    assert bodies == [
        {"profile": "devbox", "name": "scratch", "image": "python:3.12", "requirements": {}},
        {"profile": "devbox", "name": "scratch", "requirements": {}, "outcome": {"max_cost_usd": 7}, "policy": {"network": "deny"}},
    ]


def test_devbox_cli_lists_pages_and_removes_exact_active_name(monkeypatch, capsys):
    calls = []

    def handler(request):
        calls.append((request.method, request.url.path, dict(request.url.params)))
        if request.method == "POST" and request.url.path == "/v1/sandboxes":
            assert json.loads(request.content) == {"profile": "devbox", "name": "scratch", "image": "python:3.12", "requirements": {}}
            return httpx.Response(202, json=BOX)
        if request.method == "GET":
            if request.url.params.get("cursor") == "page2":
                return httpx.Response(200, json={"sandboxes": [BOX], "next_cursor": None})
            return httpx.Response(200, json={"sandboxes": [{"id": "sb_old", "state": "terminated", "envelope": BOX["envelope"]}, {"id": "sb_other", "state": "ready", "envelope": {"name": "scratch-other"}}], "next_cursor": "page2"})
        assert request.url.path == "/v1/sandboxes/sb_dev/terminate"
        return httpx.Response(200, json={**BOX, "state": "terminated"})

    monkeypatch.setattr(cli, "Client", client_factory(handler))
    assert cli.main(["devbox", "up", "scratch", "--image", "python:3.12"]) == 0
    assert cli.main(["devbox", "ls", "--json"]) == 0
    assert cli.main(["devbox", "rm", "scratch"]) == 0
    assert "sb_other" not in capsys.readouterr().out
    assert ("GET", "/v1/sandboxes", {"limit": "50", "name": "scratch", "cursor": "page2"}) in calls


def test_devbox_rm_does_not_create_or_remove_another_profile(monkeypatch):
    def handler(request):
        assert request.method == "GET"
        return httpx.Response(200, json={"sandboxes": [{**BOX, "envelope": {"name": "scratch"}}]})

    monkeypatch.setattr(cli, "Client", client_factory(handler))
    assert cli.main(["devbox", "rm", "scratch"]) != 0


def test_devbox_rejects_internal_handle_arguments():
    with pytest.raises(nodus.ValidationError, match="handle"):
        nodus.Devbox(name="scratch", client=object(), sandbox_id="sb_other")


@pytest.mark.parametrize("profile,expected", [("devbox", 0), ("sandbox", 2)])
def test_devbox_rm_accepts_id_and_checks_profile(profile, expected, monkeypatch, capsys):
    calls = []

    def handler(request):
        calls.append((request.method, request.url.path))
        if request.url.path == "/v1/sandboxes":
            return httpx.Response(200, json={"sandboxes": [], "next_cursor": None})
        assert request.url.path in ("/v1/sandboxes/sb_dev", "/v1/sandboxes/sb_dev/terminate")
        assert request.method == "GET" or profile == "devbox"
        return httpx.Response(200, json={**BOX, "envelope": {"profile": profile, "name": "scratch"}})

    monkeypatch.setattr(cli, "Client", client_factory(handler))
    assert cli.main(["devbox", "rm", "sb_dev"]) == expected
    if expected == 0:
        assert ("POST", "/v1/sandboxes/sb_dev/terminate") in calls
    else:
        assert "not a devbox" in capsys.readouterr().err
