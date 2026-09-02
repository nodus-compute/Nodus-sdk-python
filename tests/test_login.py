"""``nodus login``: the device exchange, the file it writes, and what reads it.

The exchange runs over a real socket against the double below. What the double
answers is what this client has to survive, not a transcript of one server: a
poll that is told "not yet" (428), told to slow down (429 -- the front door's
IP limiter, which any request can meet), or refused outright (410, the one
answer covering a code that expired, was declined, or was already collected).

Nothing here reaches the network, and nothing reads or writes the operator's
own ``~/.nodus/config.toml``: the ``nodus_config`` fixture in ``conftest.py``
points the path at a tmp dir for the whole suite.
"""

from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
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
    token_headers: dict[str, str] = field(default_factory=dict)
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

    def _send(self, code: int, body: Any, headers: dict[str, str] | None = None) -> None:
        raw = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's spelling
        if self.path == "/v1/console/device/start":
            self.script.start_calls.append(self._body())
            self._send(*self.script.start)
        elif self.path == "/v1/console/device/token":
            self.script.token_calls.append(self._body())
            status, body = self.script.next_token()
            self._send(status, body, self.script.token_headers if status == 429 else None)
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


def _login(console, *extra: str) -> int:
    return cli.main(["login", "--base-url", console.base_url, *extra])


def _both_streams(capsys) -> str:
    """Everything the command printed. A leak is a leak on either stream."""
    captured = capsys.readouterr()
    return captured.out + captured.err


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
@pytest.mark.parametrize(
    "text",
    [
        DOCUMENTED,
        '[default]\napi_key = "back\\\\slash"\n',
        '[default]\napi_key = "say \\"hi\\""\n',
        '[other]\n"my key" = "spaced"\n',
        '[other]\n"has\\"quote" = "v"\n',
        '[other]\n"back\\\\slash" = "v"\n',
        '[other]\n"a=b" = "v"\n',
        '[other."a.b"]\nk = "v"\n',
        '[other]\nnote = "a = b"\n',
        '[other]\nnote = "dotted.value"\n',
    ],
)
def test_the_minimal_parser_agrees_with_tomllib(text):
    import tomllib

    assert config._parse_simple_toml(text, "config.toml") == tomllib.loads(text)


@pytest.mark.parametrize("key", ['has"quote', "back\\slash", "my key", "a=b", "a.b"])
def test_the_minimal_parser_reads_back_the_keys_it_writes(key):
    """Asked directly, because on 3.11+ tomllib is what the file goes through.

    Only the CI floor runs this parser for real, so the round-trip below would
    otherwise be green on this machine while corrupting files on that one.
    """
    text = f'[other]\n{config._key(key, "probe")} = "v"\n'
    assert config._parse_simple_toml(text, "config.toml")["other"] == {key: "v"}


@pytest.mark.parametrize("key", ['has"quote', "back\\slash", "my key", "a=b", "a.b"])
def test_a_foreign_key_survives_repeated_logins_unchanged(nodus_config, key):
    """Escaped on the way out, so it has to be unescaped on the way back in.

    Reading it verbatim doubles its backslashes on every login -- exit 0 each
    time, on the Python the CI floor runs, until nothing can read the file.
    """
    nodus_config.write_text(
        f'[other]\n{config._key(key, "probe")} = "v"\n', encoding="utf-8"
    )
    for _ in range(3):
        config.save_credentials("nk_live_written", "https://written.example")
    assert config._load(nodus_config)["other"] == {key: "v"}


def test_the_minimal_parser_refuses_what_it_cannot_read():
    with pytest.raises(nodus.ConfigurationError) as exc:
        config._parse_simple_toml("[default]\nports = [1, 2]\n", "config.toml")
    assert "line 2" in str(exc.value)


def test_the_minimal_parser_refuses_an_escape_it_does_not_understand():
    """Guessing at ``\\n`` would read back a value that is not what was written."""
    with pytest.raises(nodus.ConfigurationError):
        config._parse_simple_toml('[default]\napi_key = "a\\nb"\n', "config.toml")


# -- the config file, write side -------------------------------------------


def test_saving_credentials_writes_every_field_the_server_sent(nodus_config):
    config.save_credentials(
        "nk_live_written",
        "https://written.example",
        key_id="key_zz",
        tenant="acme",
        expires_at="2026-11-30T00:00:00Z",
    )
    assert nodus_config.read_text(encoding="utf-8") == (
        "[default]\n"
        'api_key = "nk_live_written"\n'
        'base_url = "https://written.example"\n'
        'key_id = "key_zz"\n'
        'tenant = "acme"\n'
        'expires_at = "2026-11-30T00:00:00Z"\n'
    )


def test_a_field_the_server_did_not_send_is_removed_not_left_stale(nodus_config):
    """A key_id outliving the key it named points revocation at the wrong one."""
    config.save_credentials("nk_a", "https://a.example", key_id="key_old", tenant="acme")
    config.save_credentials("nk_b", "https://a.example")
    text = nodus_config.read_text(encoding="utf-8")
    assert "key_old" not in text and "tenant" not in text


def test_saving_credentials_keeps_the_rest_of_the_file(nodus_config):
    nodus_config.write_text('[other]\nnote = "keep me"\n', encoding="utf-8")
    config.save_credentials("nk_live_written", "https://written.example")
    text = nodus_config.read_text(encoding="utf-8")
    assert '[other]\nnote = "keep me"' in text
    assert 'api_key = "nk_live_written"' in text


def test_a_quoted_key_is_still_quoted_after_a_login(nodus_config):
    """A bare ``my key = ...`` is not readable TOML, so the next read fails."""
    nodus_config.write_text('[other]\n"my key" = "spaced"\n', encoding="utf-8")
    config.save_credentials("nk_live_written", "https://written.example")
    assert config._load(nodus_config)["other"] == {"my key": "spaced"}


def test_a_backslash_in_a_value_survives_the_round_trip(nodus_config):
    config.save_credentials("nk_live_a\\b", "https://written.example")
    assert config.read_credentials()[0] == "nk_live_a\\b"


@pytest.mark.parametrize("char", ["\n", "\r", "\t", "\x00", "\x7f"])
def test_a_value_with_a_control_character_is_refused_not_written(nodus_config, char):
    """A raw control character writes a file neither parser reads back."""
    with pytest.raises(nodus.ConfigurationError):
        config.save_credentials(f"nk_live{char}split", "https://written.example")
    assert not nodus_config.exists()


@pytest.mark.skipif(os.name != "posix", reason="only POSIX has a mode bit meaning this")
def test_the_written_file_is_readable_only_by_its_owner(nodus_config):
    config.save_credentials("nk_live_written", "https://written.example")
    assert nodus_config.stat().st_mode & 0o077 == 0


@pytest.mark.skipif(os.name == "posix", reason="POSIX has a mode bit meaning this")
def test_a_platform_with_no_file_mode_says_what_is_true_instead(nodus_config):
    """Not "anyone can read it" -- it inherits the profile directory's ACL."""
    with pytest.warns(UserWarning, match="inherits the permissions"):
        config.save_credentials("nk_live_written", "https://written.example")


@pytest.mark.skipif(os.name == "posix", reason="POSIX has a mode bit meaning this")
def test_a_login_states_the_file_mode_caveat_as_a_sentence(console, nodus_config, capsys):
    """The warning existing is not the same as a person being told.

    Pinning only the UserWarning left the line that prints it deletable with
    the suite still green.
    """
    assert _login(console) == 0
    assert "inherits the permissions" in capsys.readouterr().err


def test_a_failed_write_leaves_no_temporary_file_holding_the_key(nodus_config, monkeypatch):
    def boom(*_a, **_kw):
        raise OSError("disk full")

    monkeypatch.setattr(config.os, "replace", boom)
    with pytest.raises(OSError):
        config.save_credentials("nk_live_written", "https://written.example")
    assert list(nodus_config.parent.iterdir()) == []


def test_the_bytes_reach_the_disk_before_the_rename_points_at_them(
    nodus_config, monkeypatch
):
    """Otherwise a crash can leave the rename done and the file empty."""
    order: list[str] = []
    real_fsync, real_replace = os.fsync, os.replace
    monkeypatch.setattr(config.os, "fsync", lambda fd: order.append("fsync") or real_fsync(fd))
    monkeypatch.setattr(
        config.os, "replace", lambda a, b: order.append("replace") or real_replace(a, b)
    )
    config.save_credentials("nk_live_written", "https://written.example")
    assert order == ["fsync", "replace"]


def test_clearing_the_key_leaves_the_address_behind(nodus_config):
    config.save_credentials("nk_live_written", "https://written.example", key_id="key_zz")
    assert config.clear_api_key() == {"key_id": "key_zz"}
    text = nodus_config.read_text(encoding="utf-8")
    assert "api_key" not in text and "key_zz" not in text
    assert 'base_url = "https://written.example"' in text


def test_clearing_a_key_that_is_not_there_says_so_rather_than_failing(nodus_config):
    assert config.clear_api_key() is None


@pytest.mark.skipif(os.name != "posix", reason="a symlinked home needs symlink support")
def test_a_symlinked_config_directory_is_refused(nodus_config, tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    shutil.rmtree(nodus_config.parent)
    nodus_config.parent.symlink_to(elsewhere, target_is_directory=True)
    with pytest.raises(nodus.ConfigurationError, match="redirects elsewhere"):
        config.save_credentials("nk_live_written", "https://written.example")
    assert list(elsewhere.iterdir()) == []


@pytest.mark.skipif(os.name != "nt", reason="a junction is an NTFS thing")
def test_a_junction_config_directory_is_refused(console, nodus_config, tmp_path):
    """``mklink /J`` needs no privilege, and ``is_symlink`` is False for one.

    So the symlink question alone leaves the guard unexercised on the platform
    where the redirect is easiest to make.
    """
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    shutil.rmtree(nodus_config.parent)
    made = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(nodus_config.parent), str(elsewhere)],
        capture_output=True, text=True, timeout=30,
    )
    if made.returncode != 0:  # pragma: no cover - depends on the filesystem
        # A skip here is how the guard went unexercised in the first place, so
        # on a runner that must support junctions this is a failure. Locally it
        # can legitimately fail on a filesystem that has none.
        detail = f"mklink /J unavailable: {made.stdout}{made.stderr}".strip()
        if os.environ.get("CI"):
            pytest.fail(f"{detail} -- CI must exercise this guard")
        pytest.skip(detail)

    assert _login(console) == 2
    assert console.start_calls == [], "a key was minted for a directory we refuse"
    assert list(elsewhere.iterdir()) == [], "the key was written through the junction"


# -- refusing before anything is minted ------------------------------------


def _break_config(nodus_config, kind: str) -> None:
    if kind == "directory-is-a-file":
        shutil.rmtree(nodus_config.parent)
        nodus_config.parent.write_text("not a directory", encoding="utf-8")
    elif kind == "file-is-a-directory":
        nodus_config.mkdir()
    elif kind == "corrupt-toml":
        nodus_config.write_text("[default\napi_key = ", encoding="utf-8")
    elif kind == "unwritable-value":
        nodus_config.write_text("[other]\ncount = 3\n", encoding="utf-8")
    elif kind == "default-not-a-table":
        # Valid TOML that re-serialises cleanly, so only the attempt to store a
        # key under it fails -- which is after the key exists.
        nodus_config.write_text('default = "hello"\n', encoding="utf-8")
    elif kind == "credential-is-a-table":
        # Assigning a string over this would delete the section silently.
        nodus_config.write_text('[default.api_key]\nnote = "mine"\n', encoding="utf-8")
    else:  # pragma: no cover - a typo in the parametrize list
        raise AssertionError(kind)


@pytest.mark.parametrize(
    "kind",
    [
        "directory-is-a-file",
        "file-is-a-directory",
        "corrupt-toml",
        "unwritable-value",
        "default-not-a-table",
        "credential-is-a-table",
    ],
)
def test_an_unwritable_config_refuses_before_a_key_is_ever_minted(
    console, nodus_config, capsys, kind
):
    """The console mints the key inside the call that releases it.

    So the write has to be proven possible while there is still nothing to
    lose: once /token answers, a failed write orphans a live key.
    """
    _break_config(nodus_config, kind)
    assert _login(console) == 2
    assert console.start_calls == [], "a key was minted for a config that cannot hold it"
    assert console.token_calls == []
    assert "error:" in capsys.readouterr().err


def test_a_probe_that_cannot_be_tidied_away_does_not_fail_the_login(
    console, nodus_config, monkeypatch
):
    """The probe has proved its point the moment it exists.

    Antivirus holding it open is not a reason to refuse a login, and must not
    escape ``main`` as a traceback.
    """
    real_unlink = os.unlink
    refusing = {"now": True}

    def refuse(target, *args, **kwargs):
        if refusing["now"] and ".probe-" in str(target):
            raise PermissionError("held open by another process")
        return real_unlink(target, *args, **kwargs)

    # Never monkeypatch.undo() here: it would revert the whole session's
    # patches, the config path redirect included, and the next login would
    # write a key into the operator's real home.
    monkeypatch.setattr(config.os, "unlink", refuse)
    assert _login(console) == 0
    assert list(nodus_config.parent.glob(".probe-*")) != []

    # And the next login sweeps what the last one could not remove, rather
    # than leaving one more beside the credential every time.
    refusing["now"] = False
    assert _login(console) == 0
    assert list(nodus_config.parent.glob(".probe-*")) == []


def test_a_table_where_a_credential_goes_is_refused_not_overwritten(nodus_config):
    """Assigning a string over it would delete the section without a word."""
    nodus_config.write_text('[default.api_key]\nnote = "mine"\n', encoding="utf-8")
    with pytest.raises(nodus.ConfigurationError, match="api_key"):
        config.save_credentials("nk_live_written", "https://written.example")
    assert 'note = "mine"' in nodus_config.read_text(encoding="utf-8")


def test_a_write_that_fails_anyway_shows_the_key_once_rather_than_losing_it(
    console, nodus_config, monkeypatch, capsys
):
    """The backstop for whatever the pre-flight could not see coming."""

    def boom(*_a, **_kw):
        raise OSError("disk full")

    monkeypatch.setattr(config, "save_credentials", boom)
    assert _login(console) == 2
    err = capsys.readouterr().err
    assert err.count(APPROVED["api_key"]) == 1
    assert "revoke it in the console" in err


def test_the_key_shown_after_a_failed_write_cannot_act_on_the_terminal(
    console, nodus_config, capsys
):
    """A key carrying escapes is exactly what forces this branch.

    ``_quote`` refuses those characters, so the write fails and the key is
    printed -- and printing it raw hands the screen to whoever sent it.
    """
    console.token = [(200, dict(APPROVED, api_key="nk_live_\x1b[2J\x1b[Hgotcha\x07"))]
    assert _login(console) == 2
    printed = _both_streams(capsys)
    assert "\x1b" not in printed and "\x07" not in printed
    assert "nk_live_" in printed, "the key still has to be recoverable"


# -- nodus login, end to end over HTTP -------------------------------------


def test_login_writes_a_config_the_client_then_resolves_from(console, nodus_config, capsys):
    assert _login(console) == 0
    printed = _both_streams(capsys)
    assert "WXYZ-4823" in printed
    assert str(nodus_config) in printed
    assert "acme" in printed
    assert APPROVED["api_key"] not in printed, "the key itself must not be printed"

    with nodus.Client() as c:
        assert c.base_url == APPROVED["base_url"]
        assert c.api_key == nodus._redact(APPROVED["api_key"])


def test_login_stores_what_names_the_key_for_revocation(console, nodus_config):
    assert _login(console) == 0
    assert config.read_metadata() == {
        "key_id": APPROVED["key_id"],
        "tenant": APPROVED["tenant"],
        "expires_at": APPROVED["expires_at"],
    }


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
    """429 is the front door's IP limiter, not an answer about this code."""
    console.token = [(429, {"error": "slow_down"}), (200, APPROVED)]
    assert _login(console) == 0
    assert nodus_config.exists()


def test_a_slow_down_waits_at_least_as_long_as_retry_after_asks(console):
    """The transport honours Retry-After; a poll that ignored it would not."""
    console.token = [(429, {"error": "slow_down"}), (200, APPROVED)]
    console.token_headers = {"Retry-After": "30"}
    slept: list[float] = []

    with login.open_http(console.base_url) as http:
        device = login.start_device_authorization(http)
        creds = login.poll_for_credentials(
            http, device, console.base_url, sleep=slept.append
        )
    assert creds.api_key == APPROVED["api_key"]
    assert slept and slept[0] == 30.0


def test_retry_after_never_outlives_the_deadline(console):
    """Sleeping past the TTL only delays the refusal it cannot prevent."""
    console.start = (201, _started(expires_in=5, interval=1))
    console.token = [(429, {"error": "slow_down"})]
    console.token_headers = {"Retry-After": "600"}
    slept: list[float] = []
    clock = iter([0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0])

    with login.open_http(console.base_url) as http:
        device = login.start_device_authorization(http)
        with pytest.raises(nodus.APITimeoutError):
            login.poll_for_credentials(
                http, device, console.base_url,
                sleep=slept.append, monotonic=lambda: next(clock),
            )
    assert slept and max(slept) <= 5.0


def test_a_refused_code_does_not_claim_to_know_which_way_it_died(
    console, nodus_config, capsys
):
    """410 is one answer for expired, declined, collected, and never-existed."""
    console.token = [(410, {"error": "expired_token", "message": "gone"})]
    assert _login(console) == 2
    err = capsys.readouterr().err
    assert "may have expired, been declined, or already been collected" in err
    assert "no key was issued" not in err
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

    with login.open_http(console.base_url) as http:
        device = login.start_device_authorization(http)
        with pytest.raises(nodus.APITimeoutError) as exc:
            login.poll_for_credentials(
                http, device, console.base_url,
                sleep=lambda _: None, monotonic=lambda: next(clock),
            )
    assert "nodus login" in str(exc.value)
    assert not nodus_config.exists()


@pytest.mark.parametrize("ttl", [float("nan"), float("inf"), 0, -1, "600", None])
def test_a_ttl_that_cannot_bound_anything_is_refused(console, nodus_config, ttl):
    """NaN passes every ``<= 0`` check, and a deadline built on it never ends."""
    console.start = (201, _started(expires_in=ttl))
    assert _login(console) == 2
    assert console.token_calls == [], "polled against a deadline it can never reach"
    assert not nodus_config.exists()


def test_the_timeout_message_names_both_deadlines(console):
    """The 900-second ceiling is this client's, not the console's.

    Blaming the console for a limit this SDK imposed sends someone looking at
    the wrong system.
    """
    console.start = (201, _started(expires_in=5, interval=1))
    console.token = [(428, {"error": "authorization_pending"})]
    clock = iter([0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0])

    with login.open_http(console.base_url) as http:
        device = login.start_device_authorization(http)
        with pytest.raises(nodus.APITimeoutError) as exc:
            login.poll_for_credentials(
                http, device, console.base_url,
                sleep=lambda _: None, monotonic=lambda: next(clock),
            )
    message = str(exc.value)
    assert "15-minute" in message and "console" in message


def test_the_ttl_is_capped_however_long_the_server_says():
    assert login._seconds({"expires_in": 10**9}, "expires_in", "/x") == login._MAX_TTL
    assert login._seconds({"expires_in": 600}, "expires_in", "/x") == 600.0


@pytest.mark.parametrize(
    "sent, expected",
    [
        (10_000, login._MAX_INTERVAL),
        (0.000001, login._MIN_INTERVAL),
        (float("nan"), login._DEFAULT_INTERVAL),
        (float("inf"), login._DEFAULT_INTERVAL),
        (0, login._DEFAULT_INTERVAL),
        ("2", login._DEFAULT_INTERVAL),
        (True, login._DEFAULT_INTERVAL),
    ],
)
def test_the_poll_cadence_is_clamped_to_something_survivable(sent, expected):
    assert login._interval({"interval": sent}) == expected


def test_json_really_does_admit_nan_so_the_guard_is_not_theoretical():
    """The fixture behind the NaN cases: json accepts the bare literal."""
    assert math.isnan(json.loads('{"expires_in": NaN}')["expires_in"])


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


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "javascript:alert(1)",
        "https://ok.example/\x1b[31m",
        "https://ok.example/a\nb",
        "https://ok.example/a\tb",
        "https://ok.example/a\x7fb",
    ],
)
def test_an_address_that_is_not_a_plain_web_url_is_never_opened(
    console, no_browser_opens, url
):
    """It comes from the server, and the platform handler acts on anything."""
    console.start = (201, _started(verification_url=url))
    assert _login(console) == 0
    assert no_browser_opens == []


@pytest.mark.parametrize("field", ["user_code", "verification_url"])
@pytest.mark.parametrize("hostile", ["\x1b[31mDANGER\x07", "x\ty", "x\rz"])
def test_text_from_the_console_cannot_repaint_the_terminal(
    console, capsys, field, hostile
):
    console.start = (201, _started(**{field: hostile}))
    assert _login(console) == 0
    printed = _both_streams(capsys)
    assert not any(char < " " and char != "\n" for char in printed)
    assert "\x7f" not in printed


def test_a_refusal_from_the_console_cannot_forge_a_sign_in_block(
    console, nodus_config, capsys
):
    """The error path prints the server's own wording for these endpoints.

    They are unauthenticated and run before any credential exists, so whoever
    answers them can write to the terminal unless the detail is cleaned where
    it is interpolated.
    """
    console.start = (
        400,
        {
            "error": "bad_request",
            "message": (
                "no\nEnter it at: https://evil.example\n"
                "Your sign-in code is 9999\x1b[32m"
            ),
        },
    )
    assert _login(console) == 2
    printed = _both_streams(capsys)
    assert "\nEnter it at: https://evil.example" not in printed
    assert "\x1b" not in printed
    assert len([ln for ln in printed.splitlines() if ln.strip()]) == 1


def test_the_error_detail_is_cleaned_where_the_server_wording_is_read():
    """At the source, so every command that prints an error is covered."""
    err = nodus.errors.error_from_response(
        "POST", "/v1/x", 400, {"error": "bad_request", "message": "a\nb\x1b[31m\x7f"}
    )
    assert str(err) == "POST /v1/x failed (400): ab[31m"


def test_the_sdks_own_remedy_lines_still_span_lines():
    """Only the server's wording is flattened; the SDK's guidance is prose."""
    err = nodus.errors.error_from_response(
        "POST", "/v1/x", 400, {"error": "invalid_compute_class"}, request_id="req_1"
    )
    assert str(err).count("\n") >= 2
    assert "request id: req_1" in str(err)


@pytest.mark.parametrize("field", ["user_code", "verification_url"])
def test_a_forged_newline_cannot_add_a_line_of_its_own(console, capsys, field):
    """A fake "Enter it at:" line reads exactly like the real one."""
    console.start = (201, _started(**{field: "0000\nEnter it at: https://evil.example"}))
    assert _login(console) == 0
    printed = _both_streams(capsys)
    assert "\nEnter it at: https://evil.example" not in printed


# -- what the environment still outranks -----------------------------------


@pytest.mark.parametrize("name", ["NODUS_API_KEY", "NODUS_BASE_URL"])
def test_login_says_when_the_environment_outranks_what_it_just_wrote(
    console, nodus_config, monkeypatch, capsys, name
):
    """"Signed in" is a lie if an env var is what the next call will use."""
    monkeypatch.setenv(name, "nk_live_env" if name.endswith("KEY") else "https://env.example")
    assert _login(console) == 0
    err = capsys.readouterr().err
    assert name in err and "outranks" in err


def test_logout_says_the_environment_still_holds_a_key(
    console, nodus_config, monkeypatch, capsys
):
    """Proven the hard way: the client kept sending the env key afterwards."""
    assert _login(console) == 0
    monkeypatch.setenv("NODUS_API_KEY", "nk_live_from_env")
    capsys.readouterr()

    assert cli.main(["logout"]) == 0
    err = capsys.readouterr().err
    assert "NODUS_API_KEY" in err and "finish logging out" in err
    with nodus.Client(base_url="https://x.example") as c:
        assert c.api_key == nodus._redact("nk_live_from_env")


# -- nodus logout ----------------------------------------------------------


def test_logout_names_the_key_it_removed_and_is_honest_about_the_server(
    console, nodus_config, capsys
):
    assert _login(console) == 0
    capsys.readouterr()

    assert cli.main(["logout"]) == 0
    out = capsys.readouterr().out
    assert APPROVED["key_id"] in out, "the only handle the console revokes by"
    assert str(nodus_config) in out
    assert "revoke" in out.lower()
    assert "api_key" not in nodus_config.read_text(encoding="utf-8")


def test_logout_with_nothing_stored_is_not_a_failure(nodus_config, capsys):
    assert cli.main(["logout"]) == 0
    assert "no stored key" in capsys.readouterr().out.lower()
