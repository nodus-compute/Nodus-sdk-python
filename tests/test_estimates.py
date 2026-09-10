import copy
import inspect
import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest

import nodus
from nodus import cli


def response(**overrides):
    return {"status": "estimated", "scope": "workload",
            "execution_seconds": {"low": 10, "high": 20},
            "completion_seconds": {"low": 15, "high": 30},
            "compute_cost_usd": {"low": .1, "high": .2},
            "reasons": [], "valid_until": "2030-01-01T00:00:00Z", "diagnostics": [],
            "customer_price_version": "price-1", **overrides}


def wire_client(body, asynchronous=False):
    seen = []
    def handle(request):
        seen.append(request)
        if request.url.path == '/v1/estimation/estimate':
            return httpx.Response(200, json=body)
        return httpx.Response(200, json={'workload_id': 'wl_test'})
    client = (nodus.AsyncClient if asynchronous else nodus.Client)(api_key='secret', max_retries=0)
    client._http._transport = httpx.MockTransport(handle)
    return client, seen


@pytest.mark.parametrize('asynchronous', [False, True])
@pytest.mark.asyncio
async def test_estimate_matches_submission_wire_without_creating_workload(asynchronous):
    brief = dict(command=['python', 'train.py'], image='training:latest', budget=5,
                 requirements={'dataset_bytes': 1024}, outputs={'weights': 'weights.pt'})
    original = copy.deepcopy(brief)
    client, seen = wire_client(response(), asynchronous)
    if asynchronous:
        estimate = await client.estimate(**brief)
    else:
        estimate = client.estimate(**brief)
    assert len(seen) == 1
    assert seen[0].method == 'POST'
    assert seen[0].url.path == '/v1/estimation/estimate'
    assert seen[0].headers['Authorization'] == 'Bearer secret'
    assert 'Idempotency-Key' not in seen[0].headers
    assert isinstance(estimate, nodus.Estimate)
    assert estimate.compute_cost_usd == nodus.EstimateRange(low=.1, high=.2)
    assert isinstance(estimate.valid_until, datetime)
    if asynchronous:
        await client.run(**brief)
        await client.aclose()
    else:
        client.run(**brief)
        client.close()
    assert json.loads(seen[0].content) == {'workload': json.loads(seen[1].content)}
    assert brief == original


def test_estimate_signatures_match_run():
    for cls in (nodus.Client, nodus.AsyncClient):
        run = dict(inspect.signature(cls.run).parameters)
        estimate = dict(inspect.signature(cls.estimate).parameters)
        run.pop('idempotency_key')
        assert estimate.pop('stage_id').default is None
        assert estimate == run


@pytest.mark.parametrize('asynchronous', [False, True])
@pytest.mark.asyncio
async def test_estimate_file_preserves_submission_fields_but_omits_key(tmp_path, asynchronous):
    path = tmp_path / 'nodus.toml'
    path.write_text('''image = "training:latest"
command = ["python", "train.py"]
budget = 5
idempotency_key = "training-42"
[requirements]
dataset_bytes = 1024
''')
    before = path.read_bytes()
    client, seen = wire_client(response(scope='stage', stage_id='main'), asynchronous)
    if asynchronous:
        await client.estimate_file(path, stage_id='main')
        await client.run_file(path)
        await client.aclose()
    else:
        client.estimate_file(path, stage_id='main')
        client.run_file(path)
        client.close()
    assert json.loads(seen[0].content) == {'workload': json.loads(seen[1].content), 'stage_id': 'main'}
    assert 'Idempotency-Key' not in seen[0].headers
    assert seen[1].headers['Idempotency-Key'] == 'training-42'
    assert path.read_bytes() == before


@pytest.mark.parametrize('selector', ['', True, 4, 'a/b', 'a\n', 'a' * 65])
@pytest.mark.parametrize('asynchronous', [False, True])
@pytest.mark.asyncio
async def test_invalid_stage_selector_never_sends(selector, asynchronous):
    client, seen = wire_client(response(), asynchronous)
    with pytest.raises(ValueError, match='stage_id'):
        if asynchronous:
            await client.estimate(command=['python'], stage_id=selector)
        else:
            client.estimate(command=['python'], stage_id=selector)
    assert seen == []
    if asynchronous:
        await client.aclose()
    else:
        client.close()


INVALID = [None, [], {}, response(status='running'), response(scope=None), response(scope='other'),
           response(reasons=None), response(reasons=[3]), response(diagnostics=None),
           response(diagnostics=[{'code': 'x', 'message': 3, 'action': 'retry'}]),
           response(valid_until=3), response(valid_until=True), response(valid_until='tomorrow'),
           response(valid_until='2030-01-01'), response(estimator_version=4),
           response(stages={}), response(scope='stage'), response(stage_id='main'),
           response(status='unavailable'), response(execution_seconds=None),
           response(status='partial', execution_seconds=None, completion_seconds=None, compute_cost_usd=None)]
for field in ('execution_seconds', 'completion_seconds', 'compute_cost_usd'):
    for malformed in ({}, [], 3, {'low': True, 'high': 2}, {'low': '1', 'high': 2},
                      {'low': -1, 'high': 2}, {'low': 2, 'high': 1},
                      {'low': float('nan'), 'high': 2}, {'low': 1, 'high': float('inf')},
                      {'low': 0, 'high': 10 ** 400}):
        INVALID.append(response(**{field: malformed}))
    missing = response()
    missing.pop(field)
    INVALID.append(missing)


@pytest.mark.parametrize('body', INVALID)
@pytest.mark.parametrize('asynchronous', [False, True])
@pytest.mark.asyncio
async def test_malformed_response_rejected_at_wire(body, asynchronous):
    client, seen = wire_client(None, asynchronous)
    client._http._transport = httpx.MockTransport(lambda request: httpx.Response(
        200, content=json.dumps(body), headers={'content-type': 'application/json'}))
    with pytest.raises(nodus.NodusError, match='estimate'):
        if asynchronous:
            await client.estimate(command=['python'])
        else:
            client.estimate(command=['python'])
    if asynchronous:
        await client.aclose()
    else:
        client.close()


def test_nulls_legacy_fields_and_partial_stage_evidence():
    body = response(status='unavailable', execution_seconds=None, completion_seconds=None,
                    compute_cost_usd=None, valid_until=None, reasons=['no_match'])
    for key in ('scope', 'diagnostics'):
        body.pop(key)
    client, seen = wire_client(body)
    with client:
        estimate = client.estimate(command=['python'])
    assert estimate.scope == 'workload'
    assert estimate.compute_cost_usd is None
    assert estimate.valid_until is None
    assert estimate.diagnostics == []
    assert estimate.stages == []
    assert estimate.raw == body
    assert 'scope' not in estimate.raw
    stages = [response(scope='stage', stage_id='train')]
    partial = {**body, 'scope': 'workload', 'status': 'partial', 'stages': stages,
               'valid_until': '2030-01-01T00:00:00Z'}
    client, seen = wire_client(partial)
    with client:
        estimate = client.estimate(command=['python'])
    assert estimate.execution_seconds is None
    assert estimate.stages[0].compute_cost_usd.low == .1


def test_completion_can_be_less_than_execution_for_parallel_workload():
    client, _ = wire_client(response(completion_seconds={'low': 5, 'high': 10}))
    with client:
        assert client.estimate(command=['python']).completion_seconds.low == 5


@pytest.mark.parametrize('json_args', [[], ['--json']])
def test_cli_estimate_wire_and_safe_display(tmp_path, monkeypatch, capsys, json_args):
    path = tmp_path / 'nodus.toml'
    path.write_text('image="training:latest"\ncommand=["python", "train.py"]\n')
    body = response(status='unavailable', scope='stage', stage_id='main', execution_seconds=None,
                    completion_seconds=None, compute_cost_usd=None, valid_until=None,
                    reasons=['missing\nFORGED\x1b[2J'], diagnostics=[{
                        'code': 'missing', 'message': 'Need data\nFORGED\x1b[2J', 'action': 'Provide dataset_bytes'}])
    client, seen = wire_client(body)
    monkeypatch.setattr(cli, 'Client', lambda **kwargs: client)
    args = ['estimate', str(path), '--stage', 'main']
    args = ['--json', *args] if json_args == ['global'] else [*args, *json_args]
    assert cli.main(args) == 0
    out = capsys.readouterr().out
    assert len(seen) == 1 and json.loads(seen[0].content)['stage_id'] == 'main'
    if json_args:
        assert json.loads(out) == body
    else:
        assert 'unavailable' in out and 'Provide dataset_bytes' in out
        assert '\x1b' not in out and '\nFORGED' not in out
        assert '$0' not in out


def test_cli_estimate_prints_numeric_ranges(tmp_path, monkeypatch, capsys):
    path = tmp_path / 'nodus.toml'
    path.write_text('command=["python"]\n')
    client, _ = wire_client(response())
    monkeypatch.setattr(cli, 'Client', lambda **kwargs: client)
    assert cli.main(['estimate', str(path)]) == 0
    out = capsys.readouterr().out
    assert '10' in out and '20' in out and 'USD' in out


@pytest.mark.parametrize('asynchronous', [False, True])
@pytest.mark.asyncio
async def test_stage_validation_error_is_mapped_from_http(asynchronous):
    client, seen = wire_client(None, asynchronous)
    def handle(request):
        seen.append(request)
        return httpx.Response(400, json={'error': 'invalid_estimate_stage', 'message': 'Select a stage from the compiled workload'})
    client._http._transport = httpx.MockTransport(handle)
    with pytest.raises(nodus.ValidationError):
        if asynchronous:
            await client.estimate(command=['python'], stage_id='absent')
        else:
            client.estimate(command=['python'], stage_id='absent')
    assert len(seen) == 1
    if asynchronous:
        await client.aclose()
    else:
        client.close()


@pytest.mark.parametrize('kwargs', [{'idempotency_key': 'submission'}, {'budget': True},
                                    {'budget': float('nan')}, {'budget': float('inf')}])
def test_invalid_preview_arguments_never_send(kwargs):
    client, seen = wire_client(response())
    with client, pytest.raises((ValueError, TypeError)):
        client.estimate(command=['python'], **kwargs)
    assert seen == []


def test_expired_response_is_exposed_and_input_is_not_mutated():
    body = response(valid_until='2020-01-01T00:00:00Z')
    before = copy.deepcopy(body)
    result = nodus.Estimate.from_dict(body)
    assert result.valid_until.year == 2020
    result.raw['reasons'].append('local')
    assert body == before


@pytest.mark.parametrize('metric', ['execution_seconds', 'completion_seconds', 'compute_cost_usd'])
@pytest.mark.parametrize('asynchronous', [False, True])
@pytest.mark.asyncio
async def test_aggregate_cannot_claim_metric_missing_from_stage(metric, asynchronous):
    child = response(scope='stage', stage_id='train', status='partial', **{metric: None})
    body = response(stages=[child, response(scope='stage', stage_id='evaluate')])
    client, _ = wire_client(body, asynchronous)
    with pytest.raises(nodus.NodusError, match='estimate'):
        if asynchronous:
            await client.estimate(command=['python'])
        else:
            client.estimate(command=['python'])
    if asynchronous:
        await client.aclose()
    else:
        client.close()


@pytest.mark.parametrize('body', [
    response(stages=[response(status='unavailable', scope='stage', stage_id='train',
                             execution_seconds=None, completion_seconds=None,
                             compute_cost_usd=None, valid_until=None)]),
    response(status='partial', stages=[response(scope='stage', stage_id='train')]),
])
def test_contradictory_aggregate_status_is_rejected(body):
    client, _ = wire_client(body)
    with client, pytest.raises(nodus.NodusError, match='estimate'):
        client.estimate(command=['python'])


@pytest.mark.parametrize('expiry', [
    '2030-01-01T00:00:00+00:60', '2030-01-01T00:00:00+05',
    '2030-01-01T00:00:00+01:00:30', '2030-01-01T00:00:00,5Z',
    '2030-01-01T00:00:00-00:60', '2030-01-01T00:00:00+24:00',
    '2030-01-01T00:00:00Z\n', '2030-01-01T00:00:00+0000',
    '2030-01-01T00:00:00.123+01:00:00.5',
])
@pytest.mark.parametrize('asynchronous', [False, True])
@pytest.mark.asyncio
async def test_estimate_expiry_requires_complete_rfc3339(expiry, asynchronous):
    client, _ = wire_client(response(valid_until=expiry), asynchronous)
    with pytest.raises(nodus.NodusError, match='estimate'):
        if asynchronous:
            await client.estimate(command=['python'])
        else:
            client.estimate(command=['python'])
    if asynchronous:
        await client.aclose()
    else:
        client.close()


@pytest.mark.parametrize('direction_control', ['\u061c', '\u200e', '\u200f', '\u202a', '\u202b',
                                              '\u202c', '\u202d', '\u202e', '\u2066', '\u2067', '\u2068', '\u2069'])
def test_cli_estimate_strips_direction_controls(tmp_path, monkeypatch, capsys, direction_control):
    path = tmp_path / 'nodus.toml'
    path.write_text('command=["python"]\n')
    body = response(status='unavailable', execution_seconds=None, completion_seconds=None,
                    compute_cost_usd=None, valid_until=None, reasons=[f'x{direction_control}reason'],
                    diagnostics=[{'code': f'x{direction_control}code',
                                  'message': f'x{direction_control}message',
                                  'action': f'x{direction_control}action'}])
    client, _ = wire_client(body)
    monkeypatch.setattr(cli, 'Client', lambda **kwargs: client)
    assert cli.main(['estimate', str(path)]) == 0
    assert direction_control not in capsys.readouterr().out
    client, _ = wire_client(body)
    monkeypatch.setattr(cli, 'Client', lambda **kwargs: client)
    assert cli.main(['estimate', str(path), '--json']) == 0
    assert json.loads(capsys.readouterr().out) == body


@pytest.mark.parametrize('expiry,expected', [
    ('2020-01-01T00:00:00Z', datetime(2020, 1, 1, tzinfo=timezone.utc)),
    ('2030-01-01T00:00:00.123456789Z', datetime(2030, 1, 1, microsecond=123456, tzinfo=timezone.utc)),
    ('2030-01-01T00:00:00.123456789+05:30',
     datetime(2030, 1, 1, microsecond=123456, tzinfo=timezone(timedelta(hours=5, minutes=30)))),
    ('2030-01-01T00:00:00-07:00', datetime(2030, 1, 1, tzinfo=timezone(timedelta(hours=-7)))),
])
def test_rfc3339_expiry_preserves_valid_wire_value(expiry, expected):
    client, _ = wire_client(response(valid_until=expiry))
    with client:
        result = client.estimate(command=['python'])
    assert result.valid_until == expected
    assert result.raw['valid_until'] == expiry


def test_partial_aggregate_preserves_known_ranges_without_synthesizing_totals():
    body = response(status='partial', execution_seconds={'low': 20, 'high': 40},
                    completion_seconds=None, compute_cost_usd=None,
                    stages=[response(scope='stage', stage_id='train'),
                            response(scope='stage', stage_id='evaluate', status='partial', compute_cost_usd=None)])
    client, _ = wire_client(body)
    with client:
        result = client.estimate(command=['python'])
    assert result.execution_seconds == nodus.EstimateRange(20, 40)
    assert result.completion_seconds is None
    assert result.compute_cost_usd is None
    assert result.raw == body


@pytest.mark.parametrize('digits', range(1, 10))
@pytest.mark.parametrize('offset,zone', [('Z', timezone.utc),
                                       ('+05:30', timezone(timedelta(hours=5, minutes=30))),
                                       ('-07:00', timezone(timedelta(hours=-7)))])
@pytest.mark.parametrize('asynchronous', [False, True])
@pytest.mark.asyncio
async def test_rfc3339_fraction_lengths_preserve_raw_and_datetime(digits, offset, zone, asynchronous):
    fraction = '123456789'[:digits]
    expiry = f'2030-01-01T00:00:00.{fraction}{offset}'
    client, _ = wire_client(response(valid_until=expiry), asynchronous)
    if asynchronous:
        result = await client.estimate(command=['python'])
        await client.aclose()
    else:
        with client:
            result = client.estimate(command=['python'])
    expected = datetime(2030, 1, 1, microsecond=int(fraction[:6].ljust(6, '0')), tzinfo=zone)
    assert result.valid_until == expected
    assert result.raw['valid_until'] == expiry
