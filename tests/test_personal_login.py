import httpx
import pytest
import nodus
from nodus import cli, config, login


def wire(monkeypatch, handler):
    monkeypatch.setattr(login, 'open_http', lambda url: httpx.Client(
        base_url=url, transport=httpx.MockTransport(handler)))


def test_personal_session_drives_client_and_ci_key_can_override(nodus_config, monkeypatch):
    config.save_session('nc_personal', 'https://api.example', session_id='cs_one', email='me@example.com')
    assert 'api_key' not in nodus_config.read_text()
    assert config.read_metadata()['session_id'] == 'cs_one'
    with nodus.Client() as client:
        assert client.api_key == nodus._redact('nc_personal')
    monkeypatch.setenv('NODUS_API_KEY', 'nk_ci')
    with nodus.Client() as client:
        assert client.api_key == nodus._redact('nk_ci')


def test_logout_revokes_personal_session_before_clearing(nodus_config, monkeypatch, capsys):
    config.save_session('nc_personal', 'https://api.example', session_id='cs_one')
    def handle(request):
        assert request.method == 'POST'
        assert request.url.path == '/v1/session/logout'
        assert request.headers['authorization'] == 'Bearer nc_personal'
        assert config.read_credentials()[0] == 'nc_personal'
        return httpx.Response(204)
    wire(monkeypatch, handle)
    assert cli.main(['logout']) == 0
    assert config.read_credentials()[0] == ''
    assert 'Signed out' in capsys.readouterr().out


def test_failed_logout_keeps_session_for_revocation_retry(nodus_config, monkeypatch, capsys):
    config.save_session('nc_personal', 'https://api.example', session_id='cs_one')
    before = nodus_config.read_bytes()
    def handle(request):
        raise httpx.ConnectError('offline', request=request)
    wire(monkeypatch, handle)
    assert cli.main(['logout']) == 2
    assert nodus_config.read_bytes() == before
    assert 'saved sign-in remains' in capsys.readouterr().err


def test_failed_forced_login_preserves_previous_session(nodus_config, monkeypatch):
    config.save_session('nc_personal', 'https://api.example', session_id='cs_one')
    before = nodus_config.read_bytes()
    calls = []
    def handle(request):
        calls.append(request.url.path)
        assert 'authorization' not in request.headers
        return httpx.Response(503, json={'message': 'unavailable'})
    wire(monkeypatch, handle)
    assert cli.main(['login', '--force']) == 2
    assert calls == [login.START_PATH]
    assert nodus_config.read_bytes() == before


def test_legacy_api_key_reauth_is_not_reused_or_revoked(nodus_config, monkeypatch, capsys):
    config.save_credentials('nk_legacy', 'https://api.example', key_id='key_old')
    calls = []
    def handle(request):
        calls.append(request.url.path)
        assert 'authorization' not in request.headers
        return httpx.Response(503, json={'message': 'unavailable'})
    wire(monkeypatch, handle)
    assert cli.main(['login']) == 2
    assert calls == [login.START_PATH]
    assert config.read_credentials()[0] == 'nk_legacy'
    assert 'API key' in capsys.readouterr().out


def test_api_key_logout_does_not_revoke_a_team_key(nodus_config, monkeypatch):
    config.save_credentials('nk_ci', 'https://api.example')
    monkeypatch.setattr(login, 'open_http', lambda *_: pytest.fail('revoked a team key'))
    assert cli.main(['logout']) == 0
    assert config.read_credentials()[0] == ''


@pytest.mark.parametrize('revocation_status', [204, 503])
def test_successful_switch_saves_new_session_then_revokes_old(nodus_config, monkeypatch, capsys, revocation_status):
    config.save_session('nc_previous', 'https://previous.example', session_id='cs_old')
    calls = []
    def handle(request):
        calls.append((request.url.host, request.url.path))
        if request.url.path == '/v1/session/logout':
            assert config.read_credentials()[0] == 'nc_new'
            assert request.url.host == 'previous.example'
            assert request.headers['authorization'] == 'Bearer nc_previous'
            return httpx.Response(revocation_status)
        assert request.url.host == 'new.example'
        assert 'authorization' not in request.headers
        if request.url.path == login.START_PATH:
            return httpx.Response(201, json={
                'device_code': 'dc_test', 'user_code': 'ABCD-1234',
                'verification_url': 'https://console.example/device',
                'expires_in': 600, 'interval': 1,
            })
        return httpx.Response(200, json={'access_token': 'nc_new', 'session_id': 'cs_new'})
    wire(monkeypatch, handle)
    assert cli.main(['login', '--force', '--no-browser', '--base-url', 'https://new.example']) == 0
    assert config.read_credentials() == ('nc_new', 'https://new.example')
    assert calls[-1] == ('previous.example', '/v1/session/logout')
    output = capsys.readouterr()
    assert 'nc_previous' not in output.out + output.err
    assert 'nc_new' not in output.out + output.err
    if revocation_status == 503:
        assert 'previous session could not be revoked' in output.err


def test_expired_session_logout_still_clears_local_profile(nodus_config, monkeypatch):
    config.save_session('nc_expired', 'https://api.example', session_id='cs_old')
    wire(monkeypatch, lambda request: httpx.Response(401, json={'error': 'unauthorized'}))
    assert cli.main(['logout']) == 0
    assert config.read_credentials()[0] == ''


def test_denied_browser_switch_preserves_existing_session(nodus_config, monkeypatch):
    config.save_session('nc_previous', 'https://api.example', session_id='cs_old')
    before = nodus_config.read_bytes()
    paths = []
    def handle(request):
        paths.append(request.url.path)
        assert 'authorization' not in request.headers
        if request.url.path == login.START_PATH:
            return httpx.Response(201, json={
                'device_code': 'dc_test', 'user_code': 'ABCD-1234',
                'verification_url': 'https://console.example/device',
                'expires_in': 600, 'interval': 1,
            })
        return httpx.Response(410, json={'error': 'access_denied'})
    wire(monkeypatch, handle)
    assert cli.main(['login', '--force', '--no-browser']) == 2
    assert paths == [login.START_PATH, login.TOKEN_PATH]
    assert nodus_config.read_bytes() == before


def test_save_session_removes_legacy_key_without_revoking_it(nodus_config):
    config.save_credentials('nk_legacy', 'https://api.example', key_id='key_admin')
    config.save_session('nc_member', 'https://api.example', session_id='cs_member')
    assert config.read_credentials()[0] == 'nc_member'
    assert 'key_admin' not in nodus_config.read_text()
    assert 'nk_legacy' not in nodus_config.read_text()
    config.save_credentials('nk_ci', 'https://api.example')
    assert config.read_session()[0] == ''
    assert config.read_credentials()[0] == 'nk_ci'


@pytest.mark.parametrize('client_type', [nodus.Client, nodus.AsyncClient])
@pytest.mark.parametrize('source', ['argument', 'environment'])
def test_personal_token_cannot_be_forwarded_to_another_deployment(nodus_config, monkeypatch, client_type, source):
    config.save_session('nc_private', 'https://api.example', session_id='cs_private')
    monkeypatch.delenv('NODUS_API_KEY', raising=False)
    kwargs = {'base_url': 'https://other.example'}
    if source == 'environment':
        monkeypatch.setenv('NODUS_BASE_URL', 'https://other.example')
        kwargs = {}
    with pytest.raises(nodus.ConfigurationError, match='saved sign-in belongs to'):
        client_type(**kwargs)


def test_explicit_service_key_can_target_another_deployment(nodus_config):
    config.save_session('nc_private', 'https://api.example', session_id='cs_private')
    with nodus.Client(api_key='nk_ci', base_url='https://other.example') as client:
        assert client.base_url == 'https://other.example'
        assert client.api_key == nodus._redact('nk_ci')


@pytest.mark.parametrize('status', [200, 302])
def test_logout_requires_confirmed_revocation_not_frontend_success(nodus_config, monkeypatch, status):
    config.save_session('nc_personal', 'https://api.example', session_id='cs_one')
    before = nodus_config.read_bytes()
    wire(monkeypatch, lambda request: httpx.Response(status, text='<html>Sign in</html>'))
    assert cli.main(['logout']) == 2
    assert nodus_config.read_bytes() == before


@pytest.mark.parametrize('status,body', [(302, {'email': 'x@example.com', 'name': ''}), (200, {})])
def test_login_does_not_treat_invalid_identity_response_as_authenticated(nodus_config, monkeypatch, status, body):
    config.save_session('nc_personal', 'https://api.example', session_id='cs_one')
    before = nodus_config.read_bytes()
    wire(monkeypatch, lambda request: httpx.Response(status, json=body))
    assert cli.main(['login']) == 2
    assert nodus_config.read_bytes() == before
