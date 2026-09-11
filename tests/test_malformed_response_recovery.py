import asyncio
from contextlib import contextmanager
import json
import traceback

import httpx
import pytest

import nodus
from nodus import cli


MALFORMED = [b'{"private":"synthetic-private-body",', b'<html>synthetic-private-body</html>']


@pytest.fixture(autouse=True)
def mock_transport_only(monkeypatch):
    def reject_network(*args, **kwargs):
        raise AssertionError('This regression may use only synthetic mock transport')

    monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', reject_network)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, 'handle_async_request', reject_network)


@contextmanager
def synthetic_client(handler):
    with nodus.Client(api_key='synthetic-key', base_url='https://response.test') as client:
        client._http.close()
        client._http = httpx.Client(base_url='https://response.test', transport=httpx.MockTransport(handler))
        yield client


def exercise(handler, asynchronous, action):
    if not asynchronous:
        with synthetic_client(handler) as client:
            return action(client)

    async def run():
        async with nodus.AsyncClient(api_key='synthetic-key', base_url='https://response.test') as client:
            await client._http.aclose()
            client._http = httpx.AsyncClient(base_url='https://response.test', transport=httpx.MockTransport(handler))
            return await action(client)

    return asyncio.run(run())


@pytest.mark.parametrize('asynchronous', [False, True], ids=['sync', 'async'])
@pytest.mark.parametrize('body', MALFORMED, ids=['truncated-json', 'html'])
@pytest.mark.parametrize('key', [None, 'synthetic-saved-submission-key'], ids=['generated-key', 'explicit-key'])
def test_malformed_accepted_response_retains_exact_key_without_retry_or_body(asynchronous, body, key):
    requests = []

    def handler(request):
        assert request.method == 'POST' and request.url.path == '/v1/workloads'
        requests.append(request)
        return httpx.Response(202, content=body)

    with pytest.raises(nodus.APIError) as raised:
        exercise(handler, asynchronous, lambda client: client.run(
            command=['python', 'synthetic.py'], budget=1, idempotency_key=key))

    assert len(requests) == 1
    sent_key = requests[0].headers['Idempotency-Key']
    assert sent_key
    if key is not None:
        assert sent_key == key
    assert raised.value.payload['idempotency_key'] == sent_key
    assert sent_key in str(raised.value)
    assert raised.value.status_code is None
    rendered = ''.join(traceback.format_exception(type(raised.value), raised.value, raised.value.__traceback__))
    assert 'synthetic-private-body' not in str(raised.value)
    assert 'synthetic-private-body' not in repr(raised.value.payload)
    assert 'synthetic-private-body' not in rendered


@pytest.mark.parametrize('command', ['run', 'submit'])
@pytest.mark.parametrize('body', MALFORMED, ids=['truncated-json', 'html'])
def test_cli_malformed_accepted_response_preserves_generated_key(command, body, tmp_path, monkeypatch, capsys):
    workload_file = tmp_path / 'synthetic.toml'
    workload_file.write_text('command = ["python", "synthetic.py"]\nbudget = 1\n')
    requests = []

    def handler(request):
        assert request.method == 'POST' and request.url.path == '/v1/workloads'
        requests.append(request)
        return httpx.Response(202, content=body)

    with synthetic_client(handler) as client:
        monkeypatch.setattr(cli, 'Client', lambda **kwargs: client)
        assert cli.main([command, str(workload_file)]) == 2

    output = capsys.readouterr()
    assert len(requests) == 1
    assert 'Submission outcome unknown' in output.err
    displayed = output.err.split('idempotency_key = ', 1)[1]
    key, _ = json.JSONDecoder().raw_decode(displayed)
    assert key == requests[0].headers['Idempotency-Key']
    assert 'same workload file' in output.err
    assert 'synthetic-private-body' not in output.err
    assert 'Traceback' not in output.err
    assert output.out == ''


@pytest.mark.parametrize('asynchronous', [False, True], ids=['sync', 'async'])
def test_plain_text_logs_still_return_verbatim(asynchronous):
    text = 'step 1\nnot JSON: {\n'
    requests = []

    def handler(request):
        assert request.url.path == '/v1/workloads/wl_synthetic/logs'
        requests.append(request)
        return httpx.Response(200, text=text)

    assert exercise(handler, asynchronous, lambda client: client.logs('wl_synthetic')) == text
    assert len(requests) == 1


@pytest.mark.parametrize('asynchronous', [False, True], ids=['sync', 'async'])
def test_malformed_read_response_has_typed_error_without_submission_key(asynchronous):
    requests = []

    def handler(request):
        assert request.method == 'GET'
        requests.append(request)
        return httpx.Response(200, content=b'{')

    with pytest.raises(nodus.APIError) as raised:
        exercise(handler, asynchronous, lambda client: client.get('wl_synthetic'))
    assert len(requests) == 1
    assert raised.value.payload == {}
    assert 'idempotency_key' not in str(raised.value)
