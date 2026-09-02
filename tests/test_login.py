"""``nodus login``: the device exchange, the file it writes, and what reads it.

The exchange runs over a real socket against the double below, which serves the
statuses ``design/simple-path.md`` §2 documents — 428 pending, 429 slow down,
410 expired — so a client that stops speaking that contract fails here rather
than on a customer's terminal. Nothing in this file reaches the network, and
nothing reads or writes the operator's own ``~/.nodus/config.toml``: the
``nodus_config`` fixture in ``conftest.py`` points the path at a tmp dir.
"""

from __future__ import annotations

import json
import os
import sys
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

import nodus
from nodus import cli, config, login


# -- the console's device endpoints, as a local server ---------------------


@dataclass
class _Script:
    """What the double answers with, and what it was asked."""

    start: tuple[int, dict[str, Any]]
    token: list[tuple[int, dict[str, Any]]] = field(default_factory=list)
    token_calls: list[dict[str, Any]] = field(default_factory=list)
    start_calls: list[dict[str, Any]] = field(default_factory=list)

    def next_token(self) -> tuple[int, dict[str, Any]]:
        # The last scripted answer repeats: a poll loop that runs one step
        # longer than expected should fail on the assertion, not on IndexError.
        return self.token.pop(0) if len(self.token) > 1 else self.token[0]


class _DeviceHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    @property
    def script(self) -> _Script:
        return self.server.script  # type: ignore[attr-defined]

    def log_message(self, *_: Any) -> None:
        """Silent: pytest output is for failures, not for a request log."""

    def _body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        return json.loads(raw or b"{}")

    def _send(self, code: int, body: Any) -> None:
        raw = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's spelling
        if self.path == "/v1/console/device/start":
            self.script.start_calls.append(self._body())
            self._send(*self.script.start)
        elif self.path == "/v1/console/device/token":
            self.script.token_calls.append(self._body())
            self._send(*self.script.next_token())
        else:
            self._send(404, {"error": "not_found", "message": self.path})


APPROVED = {
    "api_key": "nk_live_5f2a91c3d4e5",
    "key_id": "key_a1b2c3d4e5f6",
    "base_url": "https://api.nodus.example",
    "tenant": "acme",
    "expires_at": "2026-11-30T00:00:00Z",
}


def _started(**over: Any) -> dict[str, Any]:
    body = {
        "device_code": "dc_" + "a" * 32,
        "user_code": "WXYZ-4823",
        "verification_url": "https://nodus.example/console/device?code=WXYZ-4823",
        "expires_in": 600,
        "interval": 0.01,
    }
    body.update(over)
    return body


@pytest.fixture
def console():
    """A console serving the device endpoints, scripted per test."""
    script = _Script(start=(201, _started()), token=[(200, APPROVED)])
    server = ThreadingHTTPServer(("127.0.0.1", 0), _DeviceHandler)
    server.script = script  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    script.base_url = f"http://{host}:{port}"  # type: ignore[attr-defined]
    try:
        yield script
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture(autouse=True)
def no_browser_opens(monkeypatch):
    """Nothing in this suite is allowed to open a real browser."""
    opened: list[str] = []
    monkeypatch.setattr(cli.webbrowser, "open", lambda url, *a, **kw: opened.append(url) or True)
    return opened


@pytest.fixture(autouse=True)
def no_ambient_settings(monkeypatch):
    monkeypatch.delenv("NODUS_API_KEY", raising=False)
    monkeypatch.delenv("NODUS_BASE_URL", raising=False)


# -- the config file, read side --------------------------------------------


DOCUMENTED = '[default]\napi_key = "nk_live_from_file"\nbase_url = "https://file.example"\n'


def test_the_config_file_supplies_both_settings_when_nothing_else_does(nodus_config):
    nodus_config.write_text(DOCUMENTED, encoding="utf-8")
    with nodus.Client() as c:
        assert c.base_url == "https://file.example"
        assert c.api_key == nodus._redact("nk_live_from_file")


def test_a_missing_config_file_is_not_an_error_only_a_missing_setting(nodus_config):
    assert not nodus_config.exists()
    with pytest.raises(nodus.ConfigurationError):
        nodus.Client()


def test_the_environment_outranks_the_config_file(nodus_config, monkeypatch):
    """CI injects env; a stale login on the same box must never outrank it."""
    nodus_config.write_text(DOCUMENTED, encoding="utf-8")
    monkeypatch.setenv("NODUS_API_KEY", "nk_live_from_env")
    monkeypatch.setenv("NODUS_BASE_URL", "https://env.example")
    with nodus.Client() as c:
        assert c.base_url == "https://env.example"
        assert c.api_key == nodus._redact("nk_live_from_env")


def test_an_explicit_argument_outranks_the_environment_and_the_file(nodus_config, monkeypatch):
    nodus_config.write_text(DOCUMENTED, encoding="utf-8")
    monkeypatch.setenv("NODUS_API_KEY", "nk_live_from_env")
    monkeypatch.setenv("NODUS_BASE_URL", "https://env.example")
    with nodus.Client(api_key="nk_live_explicit", base_url="https://explicit.example") as c:
        assert c.base_url == "https://explicit.example"
        assert c.api_key == nodus._redact("nk_live_explicit")


def test_precedence_is_resolved_per_setting_not_per_source(nodus_config, monkeypatch):
    """A file key against an env address is the normal case, not a conflict."""
    nodus_config.write_text(DOCUMENTED, encoding="utf-8")
    monkeypatch.setenv("NODUS_BASE_URL", "https://staging.example")
    with nodus.Client() as c:
        assert c.base_url == "https://staging.example"
        assert c.api_key == nodus._redact("nk_live_from_file")


def test_the_setup_help_offers_nodus_login(nodus_config):
    with pytest.raises(nodus.ConfigurationError) as exc:
        nodus.Client()
    assert "nodus login" in str(exc.value)


def test_a_config_file_that_is_not_the_documented_shape_is_refused_loudly(nodus_config):
    """Wrong is louder than nearly right: a mis-set key must not read as absent."""
    nodus_config.write_text('[default]\napi_key = nk_live_unquoted\n', encoding="utf-8")
    with pytest.raises(nodus.ConfigurationError) as exc:
        nodus.Client()
    assert str(nodus_config) in str(exc.value)


def test_a_key_that_is_not_text_is_refused_rather_than_coerced(nodus_config):
    nodus_config.write_text('[default]\napi_key = 12345\nbase_url = "https://x.example"\n',
                            encoding="utf-8")
    with pytest.raises(nodus.ConfigurationError) as exc:
        nodus.Client()
    assert "api_key" in str(exc.value)


def test_the_minimal_parser_reads_the_documented_shape():
    """The 3.10 path, exercised on every version: tomllib is 3.11 and later."""
    parsed = config._parse_simple_toml(DOCUMENTED, "config.toml")
    assert parsed == {
        "default": {"api_key": "nk_live_from_file", "base_url": "https://file.example"}
    }


@pytest.mark.skipif(sys.version_info < (3, 11), reason="tomllib is 3.11 and later")
def test_the_minimal_parser_agrees_with_tomllib_on_the_documented_shape():
    import tomllib

    assert config._parse_simple_toml(DOCUMENTED, "config.toml") == tomllib.loads(DOCUMENTED)


def test_the_minimal_parser_refuses_what_it_cannot_read(nodus_config):
    with pytest.raises(nodus.ConfigurationError) as exc:
        config._parse_simple_toml("[default]\nports = [1, 2]\n", "config.toml")
    assert "line 2" in str(exc.value)


# -- the config file, write side -------------------------------------------


def test_saving_credentials_writes_the_documented_shape(nodus_config):
    config.save_credentials("nk_live_written", "https://written.example")
    assert nodus_config.read_text(encoding="utf-8") == (
        "[default]\n"
        'api_key = "nk_live_written"\n'
        'base_url = "https://written.example"\n'
    )


def test_saving_credentials_keeps_the_rest_of_the_file(nodus_config):
    nodus_config.parent.mkdir(parents=True, exist_ok=True)
    nodus_config.write_text('[other]\nnote = "keep me"\n', encoding="utf-8")
    config.save_credentials("nk_live_written", "https://written.example")
    text = nodus_config.read_text(encoding="utf-8")
    assert '[other]\nnote = "keep me"' in text
    assert 'api_key = "nk_live_written"' in text


@pytest.mark.skipif(os.name != "posix", reason="only POSIX has a mode bit meaning this")
def test_the_written_file_is_readable_only_by_its_owner(nodus_config):
    config.save_credentials("nk_live_written", "https://written.example")
    assert nodus_config.stat().st_mode & 0o077 == 0


@pytest.mark.skipif(os.name == "posix", reason="POSIX has a mode bit meaning this")
def test_a_platform_with_no_file_mode_says_so_rather_than_implying_safety(nodus_config):
    with pytest.warns(UserWarning, match="readable by anyone"):
        config.save_credentials("nk_live_written", "https://written.example")


def test_clearing_the_key_leaves_the_address_behind(nodus_config):
    config.save_credentials("nk_live_written", "https://written.example")
    assert config.clear_api_key() is True
    text = nodus_config.read_text(encoding="utf-8")
    assert "api_key" not in text
    assert 'base_url = "https://written.example"' in text


def test_clearing_a_key_that_is_not_there_says_so_rather_than_failing(nodus_config):
    assert config.clear_api_key() is False


# -- nodus login, end to end over HTTP -------------------------------------


def _login(console, *extra: str) -> int:
    return cli.main(["login", "--base-url", console.base_url, *extra])


def test_login_writes_a_config_the_client_then_resolves_from(console, nodus_config, capsys):
    assert _login(console) == 0
    out = capsys.readouterr().out
    assert "WXYZ-4823" in out
    assert str(nodus_config) in out
    assert "acme" in out
    assert APPROVED["api_key"] not in out, "the key itself must not land on the terminal"

    with nodus.Client() as c:
        assert c.base_url == APPROVED["base_url"]
        assert c.api_key == nodus._redact(APPROVED["api_key"])


def test_login_sends_the_device_code_it_was_given(console):
    assert _login(console) == 0
    assert console.token_calls == [{"device_code": _started()["device_code"]}]


def test_login_keeps_polling_until_the_human_approves(console, nodus_config):
    console.token = [
        (428, {"error": "authorization_pending"}),
        (428, {"error": "authorization_pending"}),
        (200, APPROVED),
    ]
    assert _login(console) == 0
    assert len(console.token_calls) == 3
    assert nodus_config.exists()


def test_a_slow_down_is_backed_off_from_not_treated_as_a_refusal(console, nodus_config):
    """429 is the console's IP limiter, not an answer about this device code."""
    console.token = [(429, {"error": "slow_down"}), (200, APPROVED)]
    assert _login(console) == 0
    assert nodus_config.exists()


def test_an_expired_code_ends_the_login_and_writes_nothing(console, nodus_config, capsys):
    console.token = [(410, {"error": "expired_token", "message": "device code expired"})]
    assert _login(console) == 2
    err = capsys.readouterr().err
    assert "expired" in err.lower()
    assert "nodus login" in err
    assert not nodus_config.exists(), "a failed login must leave no credentials behind"


def test_a_refusal_ends_the_login_rather_than_polling_on(console, nodus_config):
    """Any 4xx that is not the pending or slow-down signal is terminal."""
    console.token = [(403, {"error": "access_denied", "message": "declined in the console"})]
    assert _login(console) == 2
    assert len(console.token_calls) == 1
    assert not nodus_config.exists()


def test_the_ttl_bounds_the_wait_even_if_the_console_never_answers(console, nodus_config):
    """Nobody approved it. The client stops at the deadline the server set."""
    console.start = (201, _started(expires_in=5, interval=1))
    console.token = [(428, {"error": "authorization_pending"})]
    clock = iter([0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0])

    with nodus.login.open_http(console.base_url) as http:
        device = nodus.login.start_device_authorization(http)
        with pytest.raises(nodus.APITimeoutError) as exc:
            nodus.login.poll_for_credentials(
                http, device, console.base_url, sleep=lambda _: None, monotonic=lambda: next(clock)
            )
    assert "nodus login" in str(exc.value)
    assert not nodus_config.exists()


def test_a_token_response_without_a_key_is_a_protocol_error_not_a_login(console, nodus_config):
    """The documented name or nothing: a near-miss must not read as success."""
    console.token = [(200, {"apiKey": "nk_live_wrong_spelling", "base_url": "https://x.example"})]
    assert _login(console) == 2
    assert not nodus_config.exists()


def test_login_needs_an_address_and_says_which_flag_supplies_it(nodus_config, capsys):
    assert cli.main(["login"]) == 2
    err = capsys.readouterr().err
    assert "--base-url" in err
    assert "NODUS_BASE_URL" in err


def test_login_takes_the_address_from_the_environment_when_no_flag_is_given(
    console, nodus_config, monkeypatch
):
    monkeypatch.setenv("NODUS_BASE_URL", console.base_url)
    assert cli.main(["login"]) == 0
    assert nodus_config.exists()


def test_the_flag_is_accepted_before_or_after_the_subcommand(console):
    """``nodus login --base-url X`` is what a person types; both must work."""
    before = cli.build_parser().parse_args(["--base-url", "https://a.example", "login"])
    after = cli.build_parser().parse_args(["login", "--base-url", "https://a.example"])
    assert before.base_url == after.base_url == "https://a.example"


def test_login_opens_the_browser_and_no_browser_stops_it(console, no_browser_opens, capsys):
    assert _login(console) == 0
    assert no_browser_opens == [_started()["verification_url"]]

    no_browser_opens.clear()
    assert _login(console, "--no-browser") == 0
    assert no_browser_opens == []
    assert _started()["verification_url"] in capsys.readouterr().out


def test_a_verification_url_that_is_not_web_is_never_handed_to_the_browser(
    console, no_browser_opens, capsys
):
    """The address comes from the server, and file:// would open a local file."""
    console.start = (201, _started(verification_url="file:///etc/passwd"))
    assert _login(console) == 0
    assert no_browser_opens == []
    assert "file:///etc/passwd" in capsys.readouterr().out


def test_text_from_the_console_cannot_repaint_the_terminal(console, capsys):
    console.start = (201, _started(user_code="\x1b[31mWXYZ\x07"))
    assert _login(console) == 0
    out = capsys.readouterr().out
    assert "\x1b" not in out and "\x07" not in out


# -- nodus logout ----------------------------------------------------------


def test_logout_removes_the_stored_key_and_is_honest_about_the_server(
    console, nodus_config, capsys
):
    assert _login(console) == 0
    capsys.readouterr()

    assert cli.main(["logout"]) == 0
    out = capsys.readouterr().out
    assert str(nodus_config) in out
    assert "revoke" in out.lower()
    assert "api_key" not in nodus_config.read_text(encoding="utf-8")


def test_logout_says_nothing_about_protecting_a_key_it_just_removed(
    console, nodus_config, recwarn
):
    assert _login(console) == 0
    recwarn.clear()
    assert cli.main(["logout"]) == 0
    assert [str(w.message) for w in recwarn if "API key" in str(w.message)] == []


@pytest.mark.skipif(os.name == "posix", reason="POSIX has a mode bit meaning this")
def test_login_states_the_file_mode_caveat_as_a_sentence(console, nodus_config, capsys):
    """A caveat a person has to read is a line of prose, not a UserWarning."""
    assert _login(console) == 0
    assert "readable by anyone" in capsys.readouterr().err


def test_logout_with_nothing_stored_is_not_a_failure(nodus_config, capsys):
    assert cli.main(["logout"]) == 0
    assert "no stored key" in capsys.readouterr().out.lower()
