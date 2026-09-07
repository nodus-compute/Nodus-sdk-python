"""Human-readable command output keeps machine output and error meaning intact."""
import json

import httpx
import pytest

import nodus
from nodus import cli


def fake_api(monkeypatch, body=None, code=200):
    def handler(request):
        return httpx.Response(code, json=body)
    client = nodus.Client(api_key='nk_test', base_url='https://nodus.invalid', max_retries=0)
    client._http = httpx.Client(base_url='https://nodus.invalid', transport=httpx.MockTransport(handler))
    monkeypatch.setattr(cli, 'Client', lambda **kwargs: client)


def test_list_names_every_column(monkeypatch, capsys):
    fake_api(monkeypatch, {'workloads': [{'id': 'wl_test', 'status': 'completed', 'spend_usd': .02}]})
    assert cli.main(['list']) == 0
    output = capsys.readouterr().out
    assert all(label in output for label in ('WORKLOAD', 'STATUS', 'COMPUTE', 'COST'))
    assert 'wl_test' in output and '$0.02' in output


def test_empty_list_explains_next_step(monkeypatch, capsys):
    fake_api(monkeypatch, {'workloads': []})
    assert cli.main(['list']) == 0
    assert 'No workloads' in capsys.readouterr().out


def test_status_labels_values_and_failure_next_step(monkeypatch, capsys):
    fake_api(monkeypatch, {'id': 'wl_test', 'status': 'failed', 'spend_usd': .02})
    assert cli.main(['status', 'wl_test']) == 1
    output = capsys.readouterr().out
    assert all(label in output for label in ('Workload', 'Status', 'Compute', 'Cost'))
    assert 'nodus logs wl_test' in output
    assert '\x1b' not in output


def test_json_status_remains_parseable(monkeypatch, capsys):
    payload = {'id': 'wl_test', 'status': 'completed'}
    fake_api(monkeypatch, payload)
    assert cli.main(['status', 'wl_test', '--json']) == 0
    assert json.loads(capsys.readouterr().out) == payload


def test_list_json_is_available_for_agents(monkeypatch, capsys):
    payload = {'id': 'wl_test', 'status': 'completed'}
    fake_api(monkeypatch, {'workloads': [payload]})
    assert cli.main(['list', '--json']) == 0
    assert json.loads(capsys.readouterr().out) == [payload]


def test_logs_gateway_error_never_claims_no_logs(monkeypatch, capsys):
    fake_api(monkeypatch, {'error': 'bad_gateway'}, 502)
    assert cli.main(['logs', 'wl_test']) == 2
    output = capsys.readouterr()
    assert not output.out
    assert 'temporarily unavailable' in output.err
    assert 'nodus logs wl_test' in output.err
    assert 'no logs' not in output.err.lower()
    assert 'GET /v1/' not in output.err


def test_unknown_workload_error_is_actionable(monkeypatch, capsys):
    fake_api(monkeypatch, {'error': 'not_found'}, 404)
    assert cli.main(['status', 'wl_test']) == 2
    output = capsys.readouterr().err
    assert 'not found' in output.lower() and 'nodus list' in output


def test_budget_error_does_not_ask_customer_to_estimate_runtime(monkeypatch, capsys):
    fake_api(monkeypatch, {'error': 'budget_exceeded', 'message': 'lower expected_runtime_hours', 'remaining_headroom_usd': 9.17}, 402)
    assert cli.main(['status', 'wl_test']) == 2
    output = capsys.readouterr().err
    assert 'expected_runtime_hours' not in output
    assert '$9.17' in output


def test_no_color_disables_terminal_styling(monkeypatch):
    import io
    from nodus._presentation import accent
    class Terminal(io.StringIO):
        def isatty(self):
            return True
    monkeypatch.setenv('NO_COLOR', '')
    assert accent('completed', 'green', stream=Terminal()) == 'completed'


def test_wait_json_does_not_fetch_events_or_logs(monkeypatch, capsys):
    paths = []
    def handler(request):
        paths.append(request.url.path)
        return httpx.Response(200, json={'id': 'wl_test', 'status': 'completed'})
    client = nodus.Client(api_key='nk_test', base_url='https://nodus.invalid', max_retries=0)
    client._http = httpx.Client(base_url='https://nodus.invalid', transport=httpx.MockTransport(handler))
    monkeypatch.setattr(cli, 'Client', lambda **kwargs: client)
    assert cli.main(['wait', 'wl_test', '--json']) == 0
    output = capsys.readouterr()
    assert json.loads(output.out)['status'] == 'completed'
    assert not output.err
    assert all(path == '/v1/workloads/wl_test' for path in paths)


def test_hostile_error_detail_cannot_forge_output():
    from nodus._presentation import error_message
    error = nodus.ValidationError('invalid', body={'message': 'bad\nApproved\x1b[32m\u202e'})
    output = error_message(error, command='run')
    assert output == 'error: badApproved'


def test_status_strips_ansi_and_direction_controls(monkeypatch, capsys):
    fake_api(monkeypatch, {'id': 'wl_\x1b[32mtest\u202e', 'status': 'completed'})
    assert cli.main(['status', 'wl_test']) == 0
    output = capsys.readouterr().out
    assert 'wl_test' in output
    assert '\x1b' not in output and '\u202e' not in output


def test_signin_timeout_does_not_claim_a_workload_is_running():
    from nodus._presentation import error_message
    output = error_message(nodus.APITimeoutError('timed out'), command='login')
    assert 'nodus login' in output
    assert 'workload' not in output.lower()


def test_cli_cancellation_failure_is_reported_once_without_python_warning(capsys):
    import warnings
    class FailedCancellation:
        def cancel(self, workload_id):
            raise nodus.APIError('unavailable', status_code=503)
    client = FailedCancellation()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter('always')
        with pytest.raises(KeyboardInterrupt):
            with cli._cancel_on_interrupt(client, 'wl_test'):
                with nodus._cancel_wait_on_interrupt(client, 'wl_test'):
                    warnings.warn('An unrelated warning', RuntimeWarning)
                    raise KeyboardInterrupt()
    output = capsys.readouterr().err
    assert output.count('Cancellation not confirmed') == 1
    assert 'nodus cancel wl_test' in output
    assert [str(w.message) for w in caught] == ['An unrelated warning']
    with pytest.warns(RuntimeWarning, match='Cancellation not confirmed'):
        with pytest.raises(KeyboardInterrupt):
            with nodus._cancel_wait_on_interrupt(client, 'wl_test'):
                raise KeyboardInterrupt()


def test_initial_reservation_explain_never_claims_zero_runtime_or_total_cost(monkeypatch, capsys):
    from types import SimpleNamespace
    route = SimpleNamespace(
        sku='nodus:A100', fit_class='accelerator', resources={}, memory_gb=40,
        region='us-local', price_usd_hour=1.2, expected_hours=0, expected_cost_usd=0,
        remaining_budget_usd=5, cost_basis='initial_reservation', initial_reservation_usd=.30,
    )
    lines = '\n'.join(cli._fmt_route(route))
    assert 'Initial reservation' in lines and '$0.30' in lines
    assert 'expected hours' not in lines and 'expected cost' not in lines
    class Fake:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def get(self, workload_id):
            return SimpleNamespace(id=workload_id, route=route)
    monkeypatch.setattr(cli, 'Client', lambda **kwargs: Fake())
    assert cli.main(['explain', 'wl_test']) == 0
    output = capsys.readouterr().out
    assert 'cost to completion' not in output
    assert 'final cost' in output.lower()


def test_route_parses_initial_reservation_without_fabricating_total_estimate():
    from nodus.types import Route
    route = Route.from_dict({'offer_id': 'nodus:A100', 'cost_basis': 'initial_reservation', 'initial_reservation_usd': .30})
    assert route.cost_basis == 'initial_reservation'
    assert route.initial_reservation_usd == .30
    lines = '\n'.join(cli._fmt_route(route))
    assert 'Initial reservation' in lines and '$0.30' in lines
    assert 'expected cost' not in lines and 'expected hours' not in lines
    legacy = Route.from_dict({'offer_id': 'nodus:A100'})
    assert legacy.cost_basis == '' and legacy.initial_reservation_usd == 0


def test_missing_initial_reservation_is_not_rendered_as_free():
    from nodus.types import Route
    route = Route.from_dict({'offer_id': 'nodus:A100', 'cost_basis': 'initial_reservation'})
    lines = cli._fmt_route(route)
    reservation = next(line for line in lines if 'Initial reservation' in line)
    assert '$0.00' not in reservation
    assert 'Not reported' in reservation
