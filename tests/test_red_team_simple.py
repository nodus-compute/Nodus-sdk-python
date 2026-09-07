"""Adversarial checks for paid submission and output publication."""
import hashlib
import json

import httpx
import pytest

import nodus
from nodus import cli


def test_download_does_not_replace_file_created_during_transfer(tmp_path):
    destination = tmp_path / 'main' / 'result'
    data = b'new result'
    def handler(request):
        if request.url.path.endswith('/outputs'):
            return httpx.Response(200, json={'outputs': [{'stage_id': 'main', 'name': 'result'}]})
        destination.write_bytes(b'other process data')
        return httpx.Response(200, content=data, headers={'X-Nodus-SHA256': hashlib.sha256(data).hexdigest()})
    with nodus.Client(api_key='nk_test', base_url='https://nodus.invalid') as client:
        client._http.close()
        client._http = httpx.Client(base_url='https://nodus.invalid', transport=httpx.MockTransport(handler))
        workload = nodus.Workload(client)
        workload.id = 'wl_test'
        try:
            workload.download(tmp_path)
        except (nodus.NodusError, FileExistsError):
            pass
    assert destination.read_bytes() == b'other process data'


@pytest.mark.parametrize('flag,value', [('--poll', '-1'), ('--poll', 'nan'), ('--timeout', '-1'), ('--timeout', 'nan')])
def test_invalid_observation_settings_never_submit(flag, value, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / 'nodus.toml').write_text('command = ["python", "train.py"]\nbudget = 5\n')
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={'id': 'wl_test', 'status': 'completed'})
    client = nodus.Client(api_key='nk_test', base_url='https://nodus.invalid')
    client._http.close()
    client._http = httpx.Client(base_url='https://nodus.invalid', transport=httpx.MockTransport(handler))
    monkeypatch.setattr(cli, 'Client', lambda **kwargs: client)
    try:
        cli.main(['run', flag, value])
    except SystemExit:
        pass
    assert not requests, 'Invalid observation options caused a paid API submission'


def test_download_rejects_parent_symlink_created_during_transfer(tmp_path):
    outside = tmp_path / 'outside'
    outside.mkdir()
    root = tmp_path / 'downloads'
    root.mkdir()
    data = b'escaped result'
    def handler(request):
        if request.url.path.endswith('/outputs'):
            return httpx.Response(200, json={'outputs': [{'stage_id': 'main', 'name': 'result'}]})
        parent = root / 'main'
        parent.rmdir()
        try:
            parent.symlink_to(outside, target_is_directory=True)
        except OSError:
            pytest.skip('Creating symlinks is unavailable on this platform')
        return httpx.Response(200, content=data, headers={'X-Nodus-SHA256': hashlib.sha256(data).hexdigest()})
    with nodus.Client(api_key='nk_test', base_url='https://nodus.invalid') as client:
        client._http.close()
        client._http = httpx.Client(base_url='https://nodus.invalid', transport=httpx.MockTransport(handler))
        workload = nodus.Workload(client)
        workload.id = 'wl_test'
        try:
            workload.download(root)
        except (nodus.NodusError, OSError):
            pass
    assert not (outside / 'result').exists()


def test_run_file_keeps_same_key_and_budget_on_transport_retry(tmp_path, monkeypatch):
    path = tmp_path / 'train.toml'
    path.write_text('command = ["python", "train.py"]\nbudget = 5\nidempotency_key = "replay-key"\n')
    calls = []
    def handler(request):
        calls.append((request.headers['Idempotency-Key'], request.content))
        if len(calls) == 1:
            raise httpx.ReadTimeout('response lost', request=request)
        return httpx.Response(200, json={'id': 'wl_test', 'status': 'accepted'})
    monkeypatch.setattr(nodus.time, 'sleep', lambda seconds: None)
    with nodus.Client(api_key='nk_test', base_url='https://nodus.invalid', max_retries=1) as client:
        client._http.close()
        client._http = httpx.Client(base_url='https://nodus.invalid', transport=httpx.MockTransport(handler))
        assert client.run_file(path).id == 'wl_test'
    assert len(calls) == 2
    assert calls[0] == calls[1]
    assert calls[0][0] == 'replay-key'
    assert json.loads(calls[0][1])['outcome']['max_cost_usd'] == 5


def test_asset_redirect_cannot_forward_import_credential():
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(307, headers={'Location': 'https://attacker.invalid/collect'})
    with nodus.Client(api_key='nk_account', base_url='https://nodus.invalid') as client:
        client._http.close()
        client._http = httpx.Client(base_url='https://nodus.invalid', transport=httpx.MockTransport(handler), follow_redirects=True)
        with pytest.raises(nodus.APIError, match='redirect'):
            client.assets.import_github('owner/repo', token='ghp_private_import_credential')
    assert len(calls) == 1
    assert calls[0].url.host == 'nodus.invalid'


def test_asset_transport_exception_does_not_expose_import_token():
    secret = 'ghp_private_import_credential'
    def handler(request):
        raise httpx.ConnectError('upstream rejected ' + secret, request=request)
    with nodus.Client(api_key='nk_account', base_url='https://nodus.invalid') as client:
        client._http.close()
        client._http = httpx.Client(base_url='https://nodus.invalid', transport=httpx.MockTransport(handler))
        with pytest.raises(nodus.APIConnectionError) as caught:
            client.assets.import_github('owner/repo', token=secret)
    assert secret not in str(caught.value)
    assert caught.value.__suppress_context__


@pytest.mark.parametrize('names', [('model', 'MODEL'), ('Result.bin', 'result.BIN')])
def test_download_rejects_case_collisions_before_http(names, tmp_path):
    existing = set(tmp_path.iterdir())
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={'outputs': [{'stage_id': 'main', 'name': name} for name in names]})
    with nodus.Client(api_key='nk_test', base_url='https://nodus.invalid') as client:
        client._http.close()
        client._http = httpx.Client(base_url='https://nodus.invalid', transport=httpx.MockTransport(handler))
        workload = nodus.Workload(client)
        workload.id = 'wl_test'
        with pytest.raises(nodus.ValidationError, match='collide'):
            workload.download(tmp_path)
    assert len(calls) == 1
    assert set(tmp_path.iterdir()) == existing


def test_download_no_clobber_when_file_appears_while_streaming(tmp_path):
    from nodus._outputs import verified_file
    destination = tmp_path / 'result'
    data = b'result'
    with pytest.raises(nodus.ValidationError, match='already exists'):
        with verified_file(destination, {'X-Nodus-SHA256': hashlib.sha256(data).hexdigest()}, overwrite=False) as write:
            write(data)
            destination.write_bytes(b'other process data')
    assert destination.read_bytes() == b'other process data'
    assert list(tmp_path.glob('.nodus-download-*')) == []
