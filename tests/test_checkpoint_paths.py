import asyncio
import json

import httpx
import pytest

import nodus
from nodus._brief import build_payload
from nodus._workload_file import load_workload_file
from nodus.requests import ContinuitySpec


@pytest.mark.parametrize('stage', [False, True])
@pytest.mark.parametrize('paths', [[], ['state'], ['.'], [' '], ['state', 'training progress.json']])
def test_checkpoint_scope_round_trips_workload_file(tmp_path, stage, paths):
    prefix = ('[[stages]]\nid="train"\nsource={command=["python", "train.py"]}\n'
              '[stages.continuity]\n' if stage else
              'command=["python", "train.py"]\n[continuity]\n')
    source = tmp_path / 'workload.toml'
    source.write_text(prefix + 'checkpoint_paths=' + json.dumps(paths), encoding='utf-8')
    values = load_workload_file(source)
    payload = build_payload(**values)
    continuity = payload['stages'][0]['continuity'] if stage else payload['continuity']
    assert continuity['checkpoint_paths'] == paths


@pytest.mark.parametrize('paths', [
    'state', [1], [''], ['../outside'], ['/outside'], ['a/../b'],
    ['.nodus/cache'], ['a\\b'], ['C:/state'], ['a//b'], ['a/'],
    ['a\n'], ['a\u0085'], ['x' * 513], ['é' * 257], ['state'] * 65,
])
def test_invalid_checkpoint_scope_is_rejected_before_submission(tmp_path, paths):
    source = tmp_path / 'workload.toml'
    source.write_text('command=["python", "train.py"]\n[continuity]\n'
                      'checkpoint_paths=' + json.dumps(paths), encoding='utf-8')
    with pytest.raises(ValueError, match='checkpoint_paths'):
        load_workload_file(source)


def test_checkpoint_paths_are_part_of_typed_continuity_contract():
    assert 'checkpoint_paths' in ContinuitySpec.__annotations__


@pytest.mark.parametrize('paths', [[], ['state'], ['.'], ['checkpoints']])
def test_framework_workload_scope_is_preserved_for_server_generated_stages(paths):
    payload = build_payload(framework='pytorch', continuity=ContinuitySpec(checkpoint_paths=paths))
    assert payload['continuity']['checkpoint_paths'] == paths
    assert payload['framework'] == 'pytorch'


@pytest.mark.parametrize('asynchronous', [False, True])
@pytest.mark.parametrize('stage', [False, True])
@pytest.mark.parametrize('paths', [None, [], ['state'], ['.'], ['state', 'progress.json']])
def test_workload_file_preserves_scope_for_server_resolution(tmp_path, asynchronous, stage, paths):
    source = tmp_path / 'workload.toml'
    prefix = ('[continuity]\ncheckpoint_paths=["custom-state"]\n'
              '[[stages]]\nid="train"\nsource={command=["python", "train.py"]}\n'
              '[stages.continuity]\n' if stage else
              'command=["python", "train.py"]\n[continuity]\n')
    selection = '' if paths is None else 'checkpoint_paths=' + json.dumps(paths)
    source.write_text(prefix + selection, encoding='utf-8')
    requests = []

    def handler(request):
        assert request.method == 'POST'
        assert request.url.path == '/v1/workloads'
        requests.append(json.loads(request.content))
        return httpx.Response(202, json={'id': 'wl_fixture', 'status': 'accepted'})

    if asynchronous:
        async def submit():
            async with nodus.AsyncClient(api_key='fixture', base_url='https://nodus.invalid') as client:
                await client._http.aclose()
                client._http = httpx.AsyncClient(base_url='https://nodus.invalid', transport=httpx.MockTransport(handler))
                await client.run_file(source)
        asyncio.run(submit())
    else:
        with nodus.Client(api_key='fixture', base_url='https://nodus.invalid') as client:
            client._http.close()
            client._http = httpx.Client(base_url='https://nodus.invalid', transport=httpx.MockTransport(handler))
            client.run_file(source)
    assert len(requests) == 1
    payload = requests[0]
    continuity = payload['stages'][0]['continuity'] if stage else payload['continuity']
    if paths is None:
        assert 'checkpoint_paths' not in continuity
    else:
        assert continuity['checkpoint_paths'] == paths
    if stage:
        assert payload['continuity']['checkpoint_paths'] == ['custom-state']
