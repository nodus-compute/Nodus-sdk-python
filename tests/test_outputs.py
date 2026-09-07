"""Output delivery follows internal/api/outputs.go's tenant-scoped endpoint."""
import asyncio
import hashlib

import httpx
import pytest

import nodus

DATA = b'model weights\x00\xff'
DIGEST = hashlib.sha256(DATA).hexdigest()
ROW = {'name': 'model', 'stage_id': 'train', 'sha256': DIGEST,
       'bytes': len(DATA), 'download': '/v1/workloads/wl_test/outputs/model?stage=train'}


def exercise(handler, asynchronous, action):
    cls = nodus.AsyncClient if asynchronous else nodus.Client
    client = cls(api_key='nk_test', base_url='https://nodus.invalid')
    http = httpx.AsyncClient if asynchronous else httpx.Client
    client._http = http(base_url='https://nodus.invalid', transport=httpx.MockTransport(handler),
                        headers={'Authorization': 'Bearer nk_test'})
    if asynchronous:
        async def run():
            async with client:
                return await action(client)
        return asyncio.run(run())
    with client:
        return action(client)


@pytest.mark.parametrize('asynchronous', [False, True])
def test_list_outputs(asynchronous):
    def handler(req):
        assert req.url.path == '/v1/workloads/wl_test/outputs'
        return httpx.Response(200, json={'workload_id': 'wl_test', 'outputs': [ROW]})
    rows = exercise(handler, asynchronous, lambda c: c.outputs('wl_test'))
    assert isinstance(rows[0], nodus.Output)
    assert rows[0].name == 'model'
    assert rows[0].stage_id == 'train'
    assert rows[0].sha256 == DIGEST
    assert rows[0].bytes == len(DATA)


@pytest.mark.parametrize('asynchronous', [False, True])
def test_download_output(asynchronous, tmp_path):
    def handler(req):
        assert req.url.path == '/v1/workloads/wl_test/outputs/model'
        assert req.url.params['stage'] == 'train'
        return httpx.Response(200, content=DATA, headers={'X-Nodus-SHA256': DIGEST})
    dest = tmp_path / 'model.bin'
    assert exercise(handler, asynchronous, lambda c: c.download_output('wl_test', 'model', dest, stage='train')) == dest
    assert dest.read_bytes() == DATA
    assert list(tmp_path.glob(".nodus-download-*")) == []


@pytest.mark.parametrize('asynchronous', [False, True])
@pytest.mark.parametrize('headers', [{'X-Nodus-SHA256': '0' * 64}, {},
                                     {'X-Nodus-SHA256': DIGEST, 'Content-Length': '999'}])
def test_failed_integrity_preserves_destination(asynchronous, headers, tmp_path):
    dest = tmp_path / 'model.bin'
    dest.write_bytes(b'existing')
    with pytest.raises(nodus.NodusError):
        exercise(lambda req: httpx.Response(200, content=DATA, headers=headers), asynchronous,
                 lambda c: c.download_output('wl_test', 'model', dest))
    assert dest.read_bytes() == b'existing'
    assert list(tmp_path.glob(".nodus-download-*")) == []


@pytest.mark.parametrize('asynchronous', [False, True])
def test_download_does_not_follow_redirect(asynchronous, tmp_path):
    calls = []
    def handler(req):
        calls.append(req.url.host)
        return httpx.Response(302, headers={'Location': 'https://attacker.invalid/steal'})
    with pytest.raises(nodus.NodusError):
        exercise(handler, asynchronous, lambda c: c.download_output('wl_test', 'model', tmp_path / 'model'))
    assert calls == ['nodus.invalid']


@pytest.mark.parametrize('asynchronous', [False, True])
@pytest.mark.parametrize('name', ['..', '../model', 'https://attacker.invalid/x', 'model?x=1'])
def test_invalid_output_name_rejected_before_request(asynchronous, name, tmp_path):
    def handler(req):
        pytest.fail('invalid output name reached transport')
    with pytest.raises((ValueError, nodus.NodusError)):
        exercise(handler, asynchronous, lambda c: c.download_output('wl_test', name, tmp_path / 'model'))

@pytest.mark.parametrize('asynchronous', [False, True])
def test_routing_empty(asynchronous):
    def handler(req):
        assert req.url.path == '/v1/workloads/wl_test/routing'
        return httpx.Response(200, json={'workload_id': 'wl_test', 'placements': []})
    assert exercise(handler, asynchronous, lambda c: c.routing('wl_test')) == []


def test_stage_metrics_preserve_missing_and_zero():
    stage = nodus.StageRun.from_dict({'id': 'main', 'last_loss': 0.0, 'metric_rate': 12.5, 'metric_step': 0})
    assert (stage.last_loss, stage.metric_rate, stage.metric_step) == (0.0, 12.5, 0)
    stage = nodus.StageRun.from_dict({'id': 'main'})
    assert (stage.last_loss, stage.metric_rate, stage.metric_step) == (None, None, None)

@pytest.mark.parametrize('asynchronous', [False, True])
def test_error_response_is_bounded(asynchronous, tmp_path):
    chunks_read = []
    class ErrorStream(httpx.SyncByteStream, httpx.AsyncByteStream):
        def __iter__(self):
            for index in range(256):
                chunks_read.append(index)
                yield b'x' * 16384
        async def __aiter__(self):
            for chunk in self:
                yield chunk
    def handler(req):
        return httpx.Response(500, stream=ErrorStream())
    dest = tmp_path / 'model'
    dest.write_bytes(b'previous')
    with pytest.raises(nodus.NodusError) as caught:
        exercise(handler, asynchronous, lambda c: c.download_output('wl_test', 'model', dest))
    assert len(chunks_read) <= 5
    assert len(str(caught.value.body)) < 70000
    assert dest.read_bytes() == b'previous'

@pytest.mark.parametrize('asynchronous', [False, True])
@pytest.mark.parametrize('failure', [httpx.ReadTimeout('timeout'), httpx.ReadError('broken pipe')])
def test_interrupted_stream_preserves_destination(asynchronous, failure, tmp_path):
    class Interrupted(httpx.SyncByteStream, httpx.AsyncByteStream):
        def __iter__(self):
            yield DATA
            raise failure
        async def __aiter__(self):
            for chunk in self:
                yield chunk
    dest = tmp_path / 'model'
    dest.write_bytes(b'previous')
    with pytest.raises((nodus.APITimeoutError, nodus.APIConnectionError)):
        exercise(lambda req: httpx.Response(200, headers={'X-Nodus-SHA256': DIGEST}, stream=Interrupted()),
                 asynchronous, lambda c: c.download_output('wl_test', 'model', dest))
    assert dest.read_bytes() == b'previous'
    assert list(tmp_path.glob('.nodus-download-*')) == []


def test_async_cancellation_removes_temporary_file(tmp_path):
    class Cancelled(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield DATA
            raise asyncio.CancelledError()
    dest = tmp_path / 'model'
    dest.write_bytes(b'previous')
    with pytest.raises(asyncio.CancelledError):
        exercise(lambda req: httpx.Response(200, headers={'X-Nodus-SHA256': DIGEST}, stream=Cancelled()),
                 True, lambda c: c.download_output('wl_test', 'model', dest))
    assert dest.read_bytes() == b'previous'
    assert list(tmp_path.glob('.nodus-download-*')) == []


@pytest.mark.parametrize('asynchronous', [False, True])
def test_stage_query_cannot_inject_headers_or_query(asynchronous, tmp_path):
    stage = 'train&stage=other#fragment\r\nAuthorization: stolen'
    def handler(req):
        assert list(req.url.params.items()) == [('stage', stage)]
        assert req.url.host == 'nodus.invalid'
        assert req.headers['Authorization'] == 'Bearer nk_test'
        assert req.headers['Accept-Encoding'] == 'identity'
        return httpx.Response(200, content=DATA, headers={'X-Nodus-SHA256': DIGEST,
                              'Content-Disposition': 'attachment; filename="../../attacker"'})
    existing = set(tmp_path.iterdir())
    dest = tmp_path / 'model'
    exercise(handler, asynchronous, lambda c: c.download_output('wl_test', 'model', dest, stage=stage))
    assert dest.read_bytes() == DATA
    assert set(tmp_path.iterdir()) - existing == {dest}


@pytest.mark.parametrize('asynchronous', [False, True])
def test_download_all_uses_stage_names_and_verified_endpoint(asynchronous, tmp_path):
    def handler(req):
        if req.url.path.endswith('/outputs'):
            return httpx.Response(200, json={'outputs': [dict(ROW, download='https://attacker.invalid/steal')]})
        assert req.url.host == 'nodus.invalid'
        assert req.url.params['stage'] == 'train'
        return httpx.Response(200, content=DATA, headers={'X-Nodus-SHA256': DIGEST})
    def action(c):
        w = nodus.AsyncWorkload(c) if asynchronous else nodus.Workload(c)
        w.id = 'wl_test'
        return w.download(tmp_path)
    result = exercise(handler, asynchronous, action)
    assert result == [tmp_path / 'train' / 'model']
    assert result[0].read_bytes() == DATA


@pytest.mark.parametrize('asynchronous', [False, True])
@pytest.mark.parametrize('row', [dict(ROW, name='../escape'), dict(ROW, stage_id='../escape'),
                                 dict(ROW, name='CON'), dict(ROW, name='model.'), dict(ROW, name='x:y')])
def test_download_all_rejects_unsafe_server_paths(asynchronous, row, tmp_path):
    def handler(req):
        assert req.url.path.endswith('/outputs')
        return httpx.Response(200, json={'outputs': [row]})
    def action(c):
        w = nodus.AsyncWorkload(c) if asynchronous else nodus.Workload(c)
        w.id = 'wl_test'
        return w.download(tmp_path)
    existing = set(tmp_path.iterdir())
    with pytest.raises(nodus.NodusError):
        exercise(handler, asynchronous, action)
    assert set(tmp_path.iterdir()) == existing
