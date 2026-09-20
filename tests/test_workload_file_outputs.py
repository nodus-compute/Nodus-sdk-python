"""File-based submissions carry validated sink declarations to the API."""

import asyncio
import json

import httpx
import pytest

import nodus
from nodus import cli


def workload_file(tmp_path, staged, outputs):
    path = tmp_path / 'nodus.toml'
    source = ('[[stages]]\nid = "train"\n'
              'source = {image = "training:v1", command = ["python", "train.py"]}\n'
              if staged else 'image = "training:v1"\ncommand = ["python", "train.py"]\n')
    path.write_text('budget = 2\n' + source + 'outputs = ' + outputs + '\n')
    return path


def exercise_file(entrypoint, path, handler, monkeypatch):
    if entrypoint == 'async':
        async def run():
            async with nodus.AsyncClient(api_key='nk_test', base_url='https://nodus.invalid') as client:
                await client._http.aclose()
                client._http = httpx.AsyncClient(
                    base_url='https://nodus.invalid', transport=httpx.MockTransport(handler))
                return (await client.run_file(path)).id
        return asyncio.run(run())
    with nodus.Client(api_key='nk_test', base_url='https://nodus.invalid') as client:
        client._http.close()
        client._http = httpx.Client(
            base_url='https://nodus.invalid', transport=httpx.MockTransport(handler))
        if entrypoint in ('run', 'submit'):
            monkeypatch.setattr(cli, 'Client', lambda **kwargs: client)
            return cli.main(['--plain', entrypoint, str(path)])
        return client.run_file(path).id


@pytest.mark.parametrize('entrypoint', ['sync', 'async', 'run', 'submit'])
@pytest.mark.parametrize('staged', [False, True], ids=['top-level', 'staged'])
@pytest.mark.parametrize('declaration, expected', [
    ('{model = "model.bin"}', {'model': 'model.bin'}),
    ('{model = {path = "model.bin"}}', {'model': {'path': 'model.bin'}}),
    ('{scores = {path = "results.csv", sink = {connection = "lab-db", table = "eval_results"}}, model = "model.bin"}',
     {'scores': {'path': 'results.csv', 'sink': {'connection': 'lab-db', 'table': 'eval_results'}}, 'model': 'model.bin'}),
    ('{scores = {path = "results.jsonl", sink = {connection = "lab-db", table = "eval_results"}}}',
     {'scores': {'path': 'results.jsonl', 'sink': {'connection': 'lab-db', 'table': 'eval_results'}}}),
    ('{scores = {path = "results.parquet", sink = {connection = "lab-db", table = "eval_results"}}}',
     {'scores': {'path': 'results.parquet', 'sink': {'connection': 'lab-db', 'table': 'eval_results'}}}),
], ids=['legacy', 'path-object', 'csv-mixed', 'jsonl', 'parquet'])
def test_file_outputs_reach_submission_api(entrypoint, staged, declaration, expected, tmp_path, monkeypatch, capsys):
    path = workload_file(tmp_path, staged, declaration)
    methods = []

    def handler(request):
        methods.append(request.method)
        if request.method == 'POST':
            assert request.url.path == '/v1/workloads'
            payload = json.loads(request.content)
            assert payload['stages'][0]['outputs'] == expected
            return httpx.Response(202, json={'workload_id': 'wl_sink', 'status': 'accepted'})
        assert request.method == 'GET'
        assert request.url.path == '/v1/workloads/wl_sink'
        return httpx.Response(200, json={'id': 'wl_sink', 'status': 'completed'})

    result = exercise_file(entrypoint, path, handler, monkeypatch)
    assert result == (0 if entrypoint in ('run', 'submit') else 'wl_sink')
    assert methods == (['POST', 'GET'] if entrypoint == 'run' else ['POST'])
    if entrypoint in ('run', 'submit'):
        assert capsys.readouterr().out.startswith('wl_sink\n')


@pytest.mark.parametrize('entrypoint', ['sync', 'async', 'run', 'submit'])
@pytest.mark.parametrize('staged', [False, True], ids=['top-level', 'staged'])
@pytest.mark.parametrize('declaration', [
    '{scores = 123}',
    '{scores = " "}',
    '{scores = {path = " "}}',
    '{scores = {sink = {connection = "lab-db", table = "eval_results"}}}',
    '{scores = {path = "../results.csv"}}',
    '{scores = {path = "results.csv", extra = true}}',
    '{scores = {path = "results.bin", sink = {connection = "lab-db", table = "eval_results"}}}',
    '{scores = {path = "results.csv", sink = {connection = "", table = "eval_results"}}}',
    '{scores = {path = "results.csv", sink = {connection = "lab-db", table = "public.results"}}}',
    '{scores = {path = "results.csv", sink = {connection = "lab-db", table = "nodus_results"}}}',
    '{scores = {path = "results.csv", sink = {connection = "lab-db", table = "results", extra = true}}}',
    '{a = {path = "a.csv", sink = {connection = "lab-db", table = "results"}}, b = {path = "b.csv", sink = {connection = "lab-db", table = "RESULTS"}}}',
])
def test_invalid_file_outputs_never_submit(entrypoint, staged, declaration, tmp_path, monkeypatch, capsys):
    path = workload_file(tmp_path, staged, declaration)

    def forbidden(request):
        pytest.fail('Invalid output declaration must not reach the API')

    if entrypoint in ('run', 'submit'):
        assert exercise_file(entrypoint, path, forbidden, monkeypatch) == 2
        error = capsys.readouterr().err
        assert 'Error:' in error
        assert 'Traceback' not in error
    else:
        with pytest.raises((ValueError, TypeError)):
            exercise_file(entrypoint, path, forbidden, monkeypatch)
