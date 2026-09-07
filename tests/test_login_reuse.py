"""Saved authentication is checked without minting another key."""
import httpx
import pytest

from nodus import cli, config, login
from nodus.errors import APIConnectionError


def test_valid_saved_login_does_not_start_browser(nodus_config, monkeypatch, capsys):
    config.save_credentials('nk_live_saved', 'https://api.example')
    seen = []
    def identity(http, key):
        seen.append(key)
        return {'email': 'viswa@example.com', 'name': 'Viswa'}
    monkeypatch.setattr(login, 'fetch_identity', identity)
    monkeypatch.setattr(login, 'start_device_authorization', lambda *_: pytest.fail('opened new login'))
    assert cli.main(['login']) == 0
    assert seen == ['nk_live_saved']
    out = capsys.readouterr().out
    assert 'viswa@example.com' in out
    assert 'Welcome back' in out
    assert '--force' in out


def test_network_error_keeps_saved_credentials(nodus_config, monkeypatch):
    config.save_credentials('nk_live_saved', 'https://api.example')
    before = nodus_config.read_bytes()
    def offline(*_):
        raise APIConnectionError('Cannot reach Nodus. Try again.')
    monkeypatch.setattr(login, 'fetch_identity', offline)
    monkeypatch.setattr(login, 'start_device_authorization', lambda *_: pytest.fail('opened new login'))
    assert cli.main(['login']) == 2
    assert nodus_config.read_bytes() == before


def test_identity_http_and_unauthorized():
    requests = []
    def handle(request):
        requests.append(request)
        return httpx.Response(200, json={'email': 'v@example.com', 'name': 'Viswa'})
    with httpx.Client(base_url='https://api.example', transport=httpx.MockTransport(handle)) as http:
        assert login.fetch_identity(http, 'nk_saved')['email'] == 'v@example.com'
    assert requests[0].headers['authorization'] == 'Bearer nk_saved'
    assert requests[0].url.path == '/v1/identity'


def test_identity_fields_survive_save_and_clear(nodus_config):
    config.save_credentials('nk_saved', 'https://api.example', email='v@example.com', name='Viswa')
    assert config.read_metadata()['email'] == 'v@example.com'
    config.clear_api_key()
    assert config.read_metadata() == {}


@pytest.mark.parametrize("force", [False, True])
def test_expired_or_forced_login_starts_new_flow(nodus_config, monkeypatch, force):
    from nodus.errors import AuthenticationError
    config.save_credentials("nk_saved", "https://api.example")
    seen = []
    def identity(*_):
        if force:
            pytest.fail("force should bypass saved-login validation")
        raise AuthenticationError("expired", status_code=401)
    def start(*_):
        seen.append(True)
        raise APIConnectionError("stopped before minting a key")
    monkeypatch.setattr(login, "fetch_identity", identity)
    monkeypatch.setattr(login, "start_device_authorization", start)
    assert cli.main(["login"] + (["--force"] if force else [])) == 2
    assert seen == [True]
    assert config.read_credentials()[0] == "nk_saved"


def test_override_url_never_receives_saved_key(nodus_config, monkeypatch):
    config.save_credentials("nk_saved", "https://api.example")
    monkeypatch.setattr(login, "fetch_identity", lambda *_: pytest.fail("sent key to another server"))
    def start(*_):
        raise APIConnectionError("stop")
    monkeypatch.setattr(login, "start_device_authorization", start)
    assert cli.main(["login", "--base-url", "https://another.example"]) == 2


@pytest.mark.parametrize('status', [403, 429, 502])
def test_server_refusal_preserves_saved_login(nodus_config, monkeypatch, status):
    config.save_credentials('nk_saved', 'https://api.example')
    before = nodus_config.read_bytes()
    requests = []
    def handle(request):
        requests.append(request.url.path)
        return httpx.Response(status, json={'message': 'temporarily unavailable'})
    monkeypatch.setattr(login, 'open_http', lambda url: httpx.Client(
        base_url=url, transport=httpx.MockTransport(handle)))
    assert cli.main(['login']) == 2
    assert requests == ['/v1/identity']
    assert nodus_config.read_bytes() == before


def test_expired_key_completes_browser_refresh(nodus_config, monkeypatch, capsys):
    config.save_credentials('nk_old', 'https://api.example')
    paths = []
    def handle(request):
        paths.append(request.url.path)
        if request.url.path == '/v1/identity':
            assert request.headers['authorization'] == 'Bearer nk_old'
            return httpx.Response(401, json={'error': 'unauthorized'})
        assert 'authorization' not in request.headers
        if request.url.path == login.START_PATH:
            return httpx.Response(201, json={
                'device_code': 'dc_test', 'user_code': 'ABCD-1234',
                'verification_url': 'https://console.example/device',
                'expires_in': 600, 'interval': 1,
            })
        return httpx.Response(200, json={
            'api_key': 'nk_new', 'email': 'v@example.com', 'name': 'Viswa',
        })
    monkeypatch.setattr(login, 'open_http', lambda url: httpx.Client(
        base_url=url, transport=httpx.MockTransport(handle)))
    assert cli.main(['login', '--no-browser']) == 0
    assert paths == ['/v1/identity', login.START_PATH, login.TOKEN_PATH]
    assert config.read_credentials() == ('nk_new', 'https://api.example')
    assert 'Welcome, v@example.com!' in capsys.readouterr().out
