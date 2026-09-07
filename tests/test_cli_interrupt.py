"""Interrupts cancel only the workload attached to a waiting command."""
import httpx
import pytest

import nodus
from nodus import cli


@pytest.mark.parametrize("argv", [["run"], ["wait", "wl_test"], ["events", "wl_test", "--follow"]])
@pytest.mark.parametrize("cancel_result", [202, 403, "interrupt"])
def test_interrupt_requests_remote_cancel(argv, cancel_result, monkeypatch, capsys, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "nodus.toml").write_text('image = "training:v1"\ncommand = ["python", "train.py"]\nbudget = 5\n')
    requests = []
    reads = 0
    def handler(req):
        nonlocal reads
        requests.append((req.method, req.url.path))
        if req.url.path.endswith('/cancel'):
            if cancel_result == 'interrupt':
                raise KeyboardInterrupt()
            return httpx.Response(cancel_result, json={"error": "forbidden"} if cancel_result == 403 else {})
        if req.method == 'POST':
            return httpx.Response(202, json={"id": "wl_test", "status": "accepted"})
        reads += 1
        if argv[0] == 'wait' and reads == 1:
            return httpx.Response(200, json={"id": "wl_test", "status": "running"})
        raise KeyboardInterrupt()
    client = nodus.Client(api_key='nk_test', base_url='https://nodus.invalid', max_retries=0)
    client._http = httpx.Client(base_url='https://nodus.invalid', transport=httpx.MockTransport(handler))
    monkeypatch.setattr(cli, 'Client', lambda **kwargs: client)
    assert cli.main(argv) == 130
    assert requests.count(('POST', '/v1/workloads/wl_test/cancel')) == 1
    stderr = capsys.readouterr().err
    assert 'wl_test' in stderr
    if cancel_result == 202:
        assert 'Cancellation requested' in stderr
        assert 'destroyed' not in stderr
    else:
        assert 'not confirmed' in stderr
        assert 'nodus cancel wl_test' in stderr


@pytest.mark.parametrize('status', [200, 404, 503])
def test_log_404_checks_workload_exists(status, monkeypatch, capsys):
    requests = []
    def handler(req):
        requests.append(req.url.path)
        if req.url.path.endswith('/logs'):
            return httpx.Response(404, json={'error': 'not_found'})
        return httpx.Response(status, json={'id': 'wl_test', 'status': 'running'} if status == 200 else {'error': 'unavailable'})
    client = nodus.Client(api_key='nk_test', base_url='https://nodus.invalid', max_retries=0)
    client._http = httpx.Client(base_url='https://nodus.invalid', transport=httpx.MockTransport(handler))
    monkeypatch.setattr(cli, 'Client', lambda **kwargs: client)
    assert cli.main(['logs', 'wl_test']) == (1 if status == 200 else 2)
    assert requests == ['/v1/workloads/wl_test/logs', '/v1/workloads/wl_test']
    output = capsys.readouterr()
    assert ('No logs are available' in output.out) == (status == 200)


def test_wait_activity_only_uses_terminal_stderr(monkeypatch):
    import io
    import threading
    class Terminal(io.StringIO):
        def isatty(self):
            return True
    stream = Terminal()
    monkeypatch.setattr(cli.sys, 'stderr', stream)
    with cli._wait_activity('wl_test'):
        pass
    assert 'Waiting for wl_test' in stream.getvalue()
    assert 'elapsed' in stream.getvalue()
    assert not any(t.name == 'nodus-wait' for t in threading.enumerate())
    stream = io.StringIO()
    monkeypatch.setattr(cli.sys, 'stderr', stream)
    with cli._wait_activity('wl_test'):
        pass
    assert stream.getvalue() == ''


@pytest.mark.parametrize('handle', [False, True])
def test_sdk_wait_interrupt_cancels(handle):
    requests = []
    def handler(req):
        requests.append((req.method, req.url.path))
        if req.method == 'POST':
            return httpx.Response(202, json={})
        raise KeyboardInterrupt()
    with nodus.Client(api_key='nk_test', base_url='https://nodus.invalid') as client:
        client._http = httpx.Client(base_url='https://nodus.invalid', transport=httpx.MockTransport(handler))
        with pytest.raises(KeyboardInterrupt):
            if handle:
                workload = nodus.Workload(client)
                workload._absorb({'id': 'wl_test', 'status': 'accepted'})
                workload.wait()
            else:
                client.wait('wl_test')
    assert requests[-1] == ('POST', '/v1/workloads/wl_test/cancel')


@pytest.mark.parametrize("key", [None, "custom'$(unsafe)`key`"] )
def test_submit_interrupt_exposes_recovery_key_without_replaying(key, monkeypatch, capsys, tmp_path):
    import json
    import re
    monkeypatch.chdir(tmp_path)
    content = 'image = "training:v1"\ncommand = ["python", "train.py"]\nbudget = 5\n'
    if key is not None:
        content += "idempotency_key = " + json.dumps(key) + "\n"
    (tmp_path / "nodus.toml").write_text(content)
    requests = []
    def handler(req):
        requests.append(req)
        raise KeyboardInterrupt()
    client = nodus.Client(api_key='nk_test', base_url='https://nodus.invalid')
    client._http = httpx.Client(base_url='https://nodus.invalid', transport=httpx.MockTransport(handler))
    monkeypatch.setattr(cli, 'Client', lambda **kwargs: client)
    argv = ['run']
    assert cli.main(argv) == 130
    assert len(requests) == 1
    stderr = capsys.readouterr().err
    assert 'Submission outcome unknown' in stderr
    displayed = stderr.split('idempotency_key = ', 1)[1]
    decoded, _ = json.JSONDecoder().raw_decode(displayed)
    assert decoded == requests[0].headers['Idempotency-Key']
    assert 'same workload file' in stderr


def test_cancel_failure_warning_filter_preserves_interrupt():
    import warnings
    def handler(req):
        if req.method == 'POST':
            return httpx.Response(403, json={'error': 'forbidden'})
        raise KeyboardInterrupt()
    with nodus.Client(api_key='nk_test', base_url='https://nodus.invalid') as client:
        client._http = httpx.Client(base_url='https://nodus.invalid', transport=httpx.MockTransport(handler))
        with warnings.catch_warnings():
            warnings.simplefilter('error', RuntimeWarning)
            with pytest.raises(KeyboardInterrupt):
                client.wait('wl_test')
