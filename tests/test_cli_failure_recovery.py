import json

import httpx
import pytest

import nodus
from nodus import cli


@pytest.mark.parametrize('command', ['run', 'submit'])
@pytest.mark.parametrize('failure', ['connection', 'timeout', 'server', 'invalid_response'])
def test_uncertain_submission_preserves_retry_key(command, failure, monkeypatch, tmp_path, capsys):
    workload_file = tmp_path / 'nodus.toml'
    workload_file.write_text('command = ["python", "train.py"]\nbudget = 1\n')
    requests = []

    def handler(request):
        requests.append(request)
        if failure == 'connection':
            raise httpx.ReadError('response lost', request=request)
        if failure == 'timeout':
            raise httpx.ReadTimeout('response timed out', request=request)
        if failure == 'server':
            return httpx.Response(503, json={'error': 'unavailable'})
        return httpx.Response(202, json=[])

    with nodus.Client(api_key='nk_test', base_url='https://nodus.invalid', max_retries=0) as client:
        client._http.close()
        client._http = httpx.Client(base_url='https://nodus.invalid', transport=httpx.MockTransport(handler))
        monkeypatch.setattr(cli, 'Client', lambda **kwargs: client)
        assert cli.main([command, str(workload_file)]) == 2

    error = capsys.readouterr().err
    assert 'Traceback' not in error
    assert 'Submission outcome unknown' in error
    displayed = error.split('idempotency_key = ', 1)[1]
    key, _ = json.JSONDecoder().raw_decode(displayed)
    assert key == requests[0].headers['Idempotency-Key']
    assert len(requests) == 1
    assert 'same workload file' in error


@pytest.mark.parametrize('argv', [
    ['list', '--limit', '-1'], ['list', '--limit', '0'], ['list', '--limit', '101'],
    ['logs', 'wl_test', '--tail', '-1'], ['logs', 'wl_test', '--generation', '-1'],
    ['logs', 'wl_test', '--generation', '0'],
])
def test_invalid_numeric_flags_fail_before_network(argv, monkeypatch, capsys):
    def unexpected_client(**kwargs):
        pytest.fail('Invalid flags must not open a client')

    monkeypatch.setattr(cli, 'Client', unexpected_client)
    with pytest.raises(SystemExit) as result:
        cli.main(argv)
    assert result.value.code == 2
    assert 'Traceback' not in capsys.readouterr().err
