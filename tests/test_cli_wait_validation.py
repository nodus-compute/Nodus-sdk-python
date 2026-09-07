"""Invalid observation settings must fail before a paid submission."""

import httpx
import pytest

import nodus
from nodus import cli


@pytest.mark.parametrize('command,flag', [
    ('run', '--poll'), ('run', '--timeout'),
    ('wait', '--poll'), ('wait', '--timeout'), ('events', '--poll'),
])
@pytest.mark.parametrize('value', ['-1', '0', 'nan', 'inf', '-inf', 'not-a-number'])
def test_invalid_wait_options_do_not_contact_api(command, flag, value, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / 'nodus.toml').write_text('command = ["python", "train.py"]\nbudget = 5\n')
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={'id': 'wl_test', 'status': 'completed'})

    with nodus.Client(api_key='nk_test', base_url='https://nodus.invalid') as client:
        client._http.close()
        client._http = httpx.Client(base_url='https://nodus.invalid', transport=httpx.MockTransport(handler))
        monkeypatch.setattr(cli, 'Client', lambda **kwargs: client)
        args = [command]
        if command != 'run':
            args.append('wl_test')
        args.append(f'{flag}={value}')
        with pytest.raises(SystemExit) as error:
            cli.main(args)
        assert error.value.code == 2
    assert not requests


@pytest.mark.parametrize('value', ['0.1', '2', '1e2'])
def test_positive_wait_options_are_accepted(value):
    assert cli._positive_seconds(value) == float(value)
