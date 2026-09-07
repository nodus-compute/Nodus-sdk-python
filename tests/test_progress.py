import asyncio
import io
import threading
from unittest.mock import Mock, AsyncMock

import pytest
import nodus

WID = 'wl_00000001-0000-4000-8000-000000000000'


def mock_preview(monkeypatch):
    from nodus._progress import Progress
    def fetch(self, client, kind, deadline):
        return client.events(self.workload_id, after=self.after) if kind == 'events' else client.logs(self.workload_id)
    monkeypatch.setattr(Progress, '_fetch', fetch)


def handle(client, status):
    wl = nodus.Workload(client)
    wl._absorb({'id': WID, 'status': status, 'spend_usd': 0.02})
    return wl


@pytest.mark.parametrize('progress', [None, False])
def test_automation_is_silent_and_does_not_fetch_logs(monkeypatch, progress):
    output = io.StringIO()
    monkeypatch.setattr('sys.stderr', output)
    with nodus.Client(api_key='test') as client:
        client.get = Mock(return_value=handle(client, 'completed'))
        client.events = Mock()
        client.logs = Mock()
        assert client.wait(WID, progress=progress).succeeded
        client.events.assert_not_called()
        client.logs.assert_not_called()
    assert output.getvalue() == ''


def test_wait_reports_state_events_and_only_new_logs(monkeypatch):
    mock_preview(monkeypatch)
    output = io.StringIO()
    monkeypatch.setattr('sys.stderr', output)
    with nodus.Client(api_key='test') as client:
        client.get = Mock(side_effect=[handle(client, 'running'), handle(client, 'running'), handle(client, 'completed')])
        event = nodus.Event(seq=1, type='stage.started')
        client.events = Mock(return_value=[event])
        client.logs = Mock(side_effect=['hello\n', 'hello\nfinished\n'])
        assert client.wait(WID, progress=True, poll_seconds=0.001).succeeded
    text = output.getvalue()
    assert 'Running' in text and 'Completed' in text
    assert text.count('hello') == text.count('finished') == 1
    assert text.count('Stage started') == 1
    assert '\r' not in text and '\x1b' not in text


def test_async_wait_progress_and_handle_identity(monkeypatch):
    output = io.StringIO()
    monkeypatch.setattr('sys.stderr', output)
    async def run():
        async with nodus.AsyncClient(api_key='test') as client:
            wl = nodus.AsyncWorkload(client)
            wl._absorb({'id': WID, 'status': 'completed'})
            wl.refresh = AsyncMock(return_value=wl)
            client.events = AsyncMock(return_value=[])
            client.logs = AsyncMock(return_value='result\n')
            assert await wl.wait(progress=True) is wl
    asyncio.run(run())
    assert 'Completed' in output.getvalue() and 'Final output: nodus logs' in output.getvalue()


def test_missing_progress_logs_does_not_fail_workload(monkeypatch):
    monkeypatch.setattr('sys.stderr', io.StringIO())
    with nodus.Client(api_key='test') as client:
        client.get = Mock(return_value=handle(client, 'completed'))
        client.events = Mock(return_value=[])
        client.logs = Mock(side_effect=nodus.NodusError('unavailable'))
        assert client.wait(WID, progress=True).succeeded


def test_progress_thread_stops_on_exception(monkeypatch):
    from nodus._progress import Progress
    class Terminal(io.StringIO):
        def isatty(self):
            return True
    monkeypatch.setattr('sys.stderr', Terminal())
    with pytest.raises(RuntimeError):
        with Progress(WID, True):
            raise RuntimeError('stop')
    assert not any(t.name == 'nodus-progress' for t in threading.enumerate())


def test_heartbeat_keeps_moving_during_a_slow_request(monkeypatch):
    from nodus._progress import Progress
    tick = threading.Event()
    class Terminal(io.StringIO):
        def isatty(self):
            return True
        def write(self, value):
            result = super().write(value)
            if 's elapsed' in value and '0s elapsed' not in value:
                tick.set()
            return result
    output = Terminal()
    monkeypatch.setattr('sys.stderr', output)
    with Progress(WID):
        assert tick.wait(3), 'Heartbeat stopped advancing while the request was pending'
    assert 's elapsed' in output.getvalue()


def test_progress_sanitizes_remote_control_codes_and_deduplicates_snapshots(monkeypatch):
    from nodus._progress import Progress
    output = io.StringIO()
    monkeypatch.setattr('sys.stderr', output)
    with Progress(WID, True) as display:
        display.logs('hello\x1b[2J\x1b]0;bad title\x07\n')
        display.logs('hello\x1b[2J\x1b]0;bad title\x07\n')
    assert output.getvalue().count('hello') == 1
    assert '\x1b' not in output.getvalue()
    assert 'bad title' not in output.getvalue()


def test_sync_handle_progress_preserves_identity(monkeypatch):
    monkeypatch.setattr('sys.stderr', io.StringIO())
    with nodus.Client(api_key='test') as client:
        wl = handle(client, 'completed')
        wl.refresh = Mock(return_value=wl)
        client.events = Mock(return_value=[])
        client.logs = Mock(return_value='')
        assert wl.wait(progress=True) is wl


def test_async_client_wait_emits_progress(monkeypatch):
    output = io.StringIO()
    monkeypatch.setattr('sys.stderr', output)
    async def run():
        async with nodus.AsyncClient(api_key='test') as client:
            wl = nodus.AsyncWorkload(client)
            wl._absorb({'id': WID, 'status': 'completed'})
            client.get = AsyncMock(return_value=wl)
            client.events = AsyncMock(return_value=[])
            client.logs = AsyncMock(return_value='')
            assert await client.wait(WID, progress=True) is wl
    asyncio.run(run())
    assert 'Completed' in output.getvalue()


def test_status_heartbeat_uses_shared_safe_text(monkeypatch):
    from nodus._progress import Progress
    from types import SimpleNamespace
    monkeypatch.setattr('sys.stderr', io.StringIO())
    with Progress(WID, True) as display:
        display.status(SimpleNamespace(status='runn\x1b[2J\u202eing\x9b', cost_now_usd=0, stages=[]))
        assert display.label == 'Running'


def test_stage_progress_only_uses_reported_units(monkeypatch):
    from nodus._progress import Progress
    from types import SimpleNamespace
    output = io.StringIO()
    monkeypatch.setattr('sys.stderr', output)
    stage = nodus.StageRun(id='train', status='running', completed_units=2, total_units=10)
    wl = SimpleNamespace(status='running', cost_now_usd=0, stages=[stage])
    with Progress(WID, True) as display:
        display.status(wl)
        display.status(wl)
        stage.completed_units = 3
        display.status(wl)
    assert output.getvalue().count('2/10 units') == 1
    assert 'train' in output.getvalue() and '3/10 units' in output.getvalue()


def test_partial_log_lines_are_buffered_until_complete(monkeypatch):
    from nodus._progress import Progress
    output = io.StringIO()
    monkeypatch.setattr('sys.stderr', output)
    with Progress(WID, True) as display:
        display.logs('hel')
        display.logs('hello\nnext')
        display.logs('hello\nnext')
    assert 'hello\nnext\n' in output.getvalue()
    assert 'hel\n' not in output.getvalue()


def test_missing_logs_are_not_described_as_service_failure(monkeypatch):
    from nodus._progress import Progress
    mock_preview(monkeypatch)
    output = io.StringIO()
    monkeypatch.setattr('sys.stderr', output)
    with nodus.Client(api_key='test') as client:
        client.events = Mock(return_value=[])
        client.logs = Mock(side_effect=nodus.NotFoundError('missing', status_code=404))
        with Progress(WID, True) as display:
            display.update(client, handle(client, 'running'))
    assert 'No logs yet' in output.getvalue()
    assert 'temporarily unavailable' not in output.getvalue()


def test_oversized_automatic_logs_stop_polling_and_point_to_logs_command(monkeypatch):
    mock_preview(monkeypatch)
    from nodus._progress import Progress
    output = io.StringIO()
    monkeypatch.setattr('sys.stderr', output)
    client = Mock()
    client.events.return_value = []
    client.logs.return_value = 'x' * (1024 * 1024 + 1)
    wl = handle(client, 'running')
    with Progress(WID, True) as display:
        display.update(client, wl)
        display.update(client, wl)
    assert client.logs.call_count == 1
    assert f'nodus logs {WID}' in output.getvalue()
    assert len(output.getvalue()) < 10000


def test_terminal_status_returns_without_waiting_for_optional_network(monkeypatch):
    monkeypatch.setattr('sys.stderr', io.StringIO())
    with nodus.Client(api_key='test') as client:
        client.get = Mock(return_value=handle(client, 'completed'))
        client.events = Mock(side_effect=AssertionError('terminal events must not be fetched'))
        client.logs = Mock(side_effect=AssertionError('terminal logs must not be fetched'))
        assert client.wait(WID, progress=True).succeeded


def test_slow_optional_network_cannot_hold_wait_deadline(monkeypatch):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import time
    class Slow(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass
        def do_GET(self):
            time.sleep(.35)
            self.send_response(200)
            self.end_headers()
    server = ThreadingHTTPServer(('127.0.0.1', 0), Slow)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr('sys.stderr', io.StringIO())
    try:
        with nodus.Client(api_key='test', base_url=f'http://127.0.0.1:{server.server_port}') as client:
            client.get = Mock(return_value=handle(client, 'running'))
            started = time.monotonic()
            with pytest.raises(nodus.APITimeoutError):
                client.wait(WID, timeout_seconds=.02, poll_seconds=.001, progress=True)
            assert time.monotonic() - started < .15
    finally:
        server.shutdown()
        server.server_close()


def test_optional_transport_does_not_retry_service_errors(monkeypatch):
    import httpx
    calls = []
    def respond(request):
        calls.append(request.url.path)
        assert max(request.extensions['timeout'].values()) <= .251
        return httpx.Response(502, json={'error': 'unavailable'})
    output = io.StringIO()
    monkeypatch.setattr('sys.stderr', output)
    with nodus.Client(api_key='test', max_retries=10) as client:
        client._http.close()
        client._http = httpx.Client(base_url='https://test.invalid', transport=httpx.MockTransport(respond))
        client.get = Mock(side_effect=[handle(client, 'running'), handle(client, 'completed')])
        assert client.wait(WID, progress=True, poll_seconds=.001).succeeded
    assert len(calls) == 2
    assert 'temporarily unavailable' in output.getvalue()


def test_async_optional_network_respects_wait_deadline(monkeypatch):
    import httpx
    import time
    monkeypatch.setattr('sys.stderr', io.StringIO())
    async def respond(request):
        await asyncio.sleep(.35)
        return httpx.Response(200, json={'events': []})
    async def run():
        async with nodus.AsyncClient(api_key='test') as client:
            await client._http.aclose()
            client._http = httpx.AsyncClient(base_url='https://test.invalid', transport=httpx.MockTransport(respond))
            wl = nodus.AsyncWorkload(client)
            wl._absorb({'id': WID, 'status': 'running'})
            client.get = AsyncMock(return_value=wl)
            started = time.monotonic()
            with pytest.raises(nodus.APITimeoutError):
                await client.wait(WID, timeout_seconds=.02, poll_seconds=.001, progress=True)
            assert time.monotonic() - started < .15
    asyncio.run(run())
