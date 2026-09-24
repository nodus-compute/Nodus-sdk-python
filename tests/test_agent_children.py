"""Child controls preserve journal identities and verify bounded results."""
import base64
import copy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from dataclasses import FrozenInstanceError

import httpx
import pytest

import nodus
from nodus import _agent
from nodus.agent_runtime import run


def encoded(value):
    return base64.b64encode(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).decode()


def outcome(child='child-1', sequence=1, spawn_key='part:1', result=None):
    raw = json.dumps(result, sort_keys=True, separators=(',', ':')).encode()
    return {'event_id': 'event-' + str(sequence), 'sequence': sequence, 'child_run_id': child,
            'spawn_key': spawn_key, 'status': 'completed', 'result_hash': hashlib.sha256(raw).hexdigest()}


@pytest.fixture
def coordinator(monkeypatch, tmp_path):
    state = {'calls': [], 'children': {}, 'lost': {}, 'checkpoint': False, 'wait': None,
             'result': None, 'blob': b'', 'transform': lambda action, response: response}
    rpc_class = _agent._RPC
    monkeypatch.setenv('NODUS_AGENT_SOCKET', str(tmp_path / 'private.sock'))

    def handler(request):
        action, body = request.url.path[1:], json.loads(request.content)
        state['calls'].append((action, copy.deepcopy(body)))
        if action in ('session', 'renew'):
            response = {'session_token': 'private-session', 'epoch': 1,
                'expires_at': (datetime.now(timezone.utc) + timedelta(seconds=60)).isoformat(),
                'run': {'run_id': 'parent', 'name': 'main', 'version': '1', 'status': 'active', 'input': encoded(None)}}
            if state['checkpoint']:
                response.update(recovery_policy='checkpoint-v1', checkpoint_id='cp-baseline')
        elif action == 'checkpoint_begin':
            response = {'checkpoint_id': 'cp-' + body['operation_id'], 'status': 'ready'}
        elif action == 'child_spawn':
            key = body['spawn_key']
            replay = key in state['children']
            if not replay:
                state['children'][key] = {'run_id': 'child-' + str(len(state['children']) + 1),
                    'parent_run_id': 'parent', 'spawn_key': key, 'group_id': 'group-1', 'depth': 1}
            response = {'decision': 'replay' if replay else 'admitted', 'child': state['children'][key]}
        elif action == 'children_wait':
            response = {'wait_id': body['wait_id'], 'mode': body['mode'], **copy.deepcopy(state['wait'])}
        elif action == 'child_cancel':
            response = {'decision': 'requested', 'child_run_id': body['child_run_id'], 'cancel_key': body['cancel_key'],
                        'cancel_requested_at': '2026-09-24T12:00:00Z'}
        elif action == 'child_result':
            response = {'child_run_id': body['child_run_id'], 'result_hash': body['result_hash'], 'result': encoded(state['result'])}
        elif action == 'child_blob_get':
            data, offset = state['blob'], body['offset']
            chunk = data[offset:offset + 262144]
            response = {'child_run_id': body['child_run_id'], 'reference': {
                'id': body['blob_id'], 'sha256': body['sha256'], 'bytes': body['bytes']},
                'status': 'committed', 'offset': offset, 'data': base64.b64encode(chunk).decode(),
                'eof': offset + len(chunk) == len(data)}
        elif action == 'finish':
            response = {'status': 'completed', 'result': body['result']}
        else:
            pytest.fail('unexpected private action: ' + action)
        if state['lost'].get(action, 0):
            state['lost'][action] -= 1
            raise httpx.ReadError('lost private-session acknowledgement', request=request)
        response = state['transform'](action, copy.deepcopy(response))
        return httpx.Response(200, json=response) if not isinstance(response, httpx.Response) else response

    def rpc():
        channel = rpc_class()
        channel.client.close()
        channel.client = httpx.Client(base_url='http://agent.local', transport=httpx.MockTransport(handler),
                                     follow_redirects=False, trust_env=False)
        return channel

    monkeypatch.setattr(_agent, '_RPC', rpc)
    def drive(function):
        def main(_):
            return function()
        return run(main, run_id='parent', version='1')
    return state, drive


def test_spawn_retries_and_reconstructed_driver_keep_one_immutable_child(coordinator):
    state, drive = coordinator
    state['lost']['child_spawn'] = 3
    def spawn():
        return nodus.agent.spawn_child({'part': 7}, spawn_key='part:7').to_dict()
    with pytest.raises(nodus.StepOutcomeUnknown):
        drive(spawn)
    assert state['children'] == {'part:7': {'run_id': 'child-1', 'parent_run_id': 'parent', 'spawn_key': 'part:7',
                                          'group_id': 'group-1', 'depth': 1}}
    assert drive(spawn)['run_id'] == 'child-1'
    requests = [body for action, body in state['calls'] if action == 'child_spawn']
    assert len(requests) == 4 and requests[0] == requests[1] == requests[2]
    assert {body['spawn_key'] for body in requests} == {'part:7'}
    assert all(json.loads(base64.b64decode(body['input'])) == {'part': 7} for body in requests)


def test_spawn_preserves_empty_attenuation_and_checkpoint_before_admission(coordinator):
    state, drive = coordinator
    state['checkpoint'] = True
    captured = []
    def spawn():
        child = nodus.agent.spawn_child(None, spawn_key='empty', permissions={'secrets': [], 'connections': []})
        captured.append(child)
        return child.to_dict()
    drive(spawn)
    request = next(body for action, body in state['calls'] if action == 'child_spawn')
    assert request['permissions'] == {'secrets': [], 'connections': []}
    assert request['checkpoint_id'] == 'cp-empty'
    checkpoint = next(body for action, body in state['calls'] if action == 'checkpoint_begin')
    assert checkpoint['operation'] == 'spawn' and checkpoint['operation_id'] == 'empty'
    with pytest.raises(FrozenInstanceError):
        captured[0].run_id = 'other'


@pytest.mark.parametrize('change', ['parent', 'spawn', 'depth', 'decision'])
def test_spawn_refuses_receipt_for_another_identity(coordinator, change):
    state, drive = coordinator
    def corrupt(action, response):
        if action == 'child_spawn':
            if change == 'decision':
                response['decision'] = 'waiting'
            else:
                response['child'][{'parent': 'parent_run_id', 'spawn': 'spawn_key', 'depth': 'depth'}[change]] = (
                    True if change == 'depth' else 'wrong')
        return response
    state['transform'] = corrupt
    with pytest.raises(nodus.StepOutcomeUnknown):
        drive(lambda: nodus.agent.spawn_child({}, spawn_key='part:1'))
    assert not any(action == 'finish' for action, _ in state['calls'])


def test_all_join_yields_only_after_checkpoint_and_replays_requested_order(coordinator):
    state, drive = coordinator
    state.update(checkpoint=True, wait={'decision': 'waiting'})
    def work():
        children = [nodus.agent.spawn_child(index, spawn_key=f'part:{index}') for index in (1, 2)]
        return [item.child_run_id for item in nodus.agent.await_children(children, wait_id='join')]
    assert drive(work) is None
    assert 'finish' not in [action for action, _ in state['calls']]
    request = next(body for action, body in state['calls'] if action == 'children_wait')
    assert request['child_run_ids'] == ['child-1', 'child-2'] and request['checkpoint_id'] == 'cp-join'
    state['wait'] = {'decision': 'replay', 'outcomes': [outcome(sequence=2), outcome('child-2', 1, 'part:2')],
                     'next_after': '2', 'exhausted': True}
    assert drive(work) == ['child-1', 'child-2']


def test_next_completion_page_keeps_cancelled_outcome_and_exact_cursor(coordinator):
    state, drive = coordinator
    cancelled = {'event_id': 'event-3', 'sequence': 3, 'child_run_id': 'child-3', 'spawn_key': 'part:3',
                 'status': 'cancelled', 'reason': 'customer_cancelled'}
    state['wait'] = {'decision': 'replay', 'outcomes': [cancelled], 'next_after': '3', 'exhausted': False}
    pages = []
    def work():
        page = nodus.agent.next_child_completions(after='2', wait_id='page:2', limit=10)
        pages.append(page)
        return page.next_after
    assert drive(work) == '3'
    assert pages[0].outcomes[0].status == 'cancelled' and not pages[0].exhausted
    request = next(body for action, body in state['calls'] if action == 'children_wait')
    assert request['after'] == '2' and request['limit'] == 10 and request['mode'] == 'next'


@pytest.mark.parametrize('bad', ['duplicate', 'cursor-skips', 'out-of-order', 'false-status', 'oversized',
                                 'missing-first', 'missing-middle', 'empty-unfinished'])
def test_bad_completion_page_never_advances_the_driver(coordinator, bad):
    state, drive = coordinator
    rows = [outcome(sequence=1), outcome('child-2', 2, 'part:2')]
    state['wait'] = {'decision': 'replay', 'outcomes': rows, 'next_after': '2', 'exhausted': False}
    if bad == 'duplicate':
        rows[1] = copy.deepcopy(rows[0])
    elif bad == 'cursor-skips':
        state['wait']['next_after'] = '3'
    elif bad == 'out-of-order':
        rows.reverse()
    elif bad == 'false-status':
        rows[0]['status'] = 'running'
    elif bad == 'missing-first':
        state['wait']['outcomes'] = [rows[1]]
    elif bad == 'missing-middle':
        rows[1] = outcome('child-3', 3, 'part:3')
        state['wait']['next_after'] = '3'
    elif bad == 'empty-unfinished':
        state['wait']['outcomes'] = []
        state['wait']['next_after'] = '0'
    else:
        state['wait']['padding'] = 'x' * 65536
    with pytest.raises(nodus.StepOutcomeUnknown):
        drive(lambda: nodus.agent.next_child_completions(after='0', wait_id='page', limit=2).next_after)
    assert not any(action == 'finish' for action, _ in state['calls'])


def test_child_result_and_blob_verify_origin_and_content(coordinator):
    state, drive = coordinator
    state['result'], state['blob'] = {'answer': 42}, bytes(range(256)) * 2048
    digest = hashlib.sha256(state['blob']).hexdigest()
    reference = {'id': 'bl_' + digest, 'sha256': digest, 'bytes': len(state['blob'])}
    state['wait'] = {'decision': 'replay', 'outcomes': [outcome(result=state['result'])], 'next_after': '1', 'exhausted': True}
    def work():
        child = nodus.agent.spawn_child(None, spawn_key='part:1')
        result = nodus.agent.await_children([child], wait_id='joined')[0]
        assert nodus.agent.get_child_blob(child, reference) == state['blob']
        return nodus.agent.child_result(result)
    assert drive(work) == {'answer': 42}
    calls = [body for action, body in state['calls'] if action == 'child_blob_get']
    assert [body['offset'] for body in calls] == [0, 262144]
    assert {body['child_run_id'] for body in calls} == {'child-1'}


@pytest.mark.parametrize('bad', ['child', 'result-hash', 'blob-child', 'blob-hash'])
def test_child_reads_refuse_cross_child_or_corrupted_payload(coordinator, bad):
    state, drive = coordinator
    state.update(result={'answer': 42}, blob=b'original')
    digest = hashlib.sha256(state['blob']).hexdigest()
    reference = {'id': 'bl_' + digest, 'sha256': digest, 'bytes': 8}
    state['wait'] = {'decision': 'replay', 'outcomes': [outcome(result=state['result'])], 'next_after': '1', 'exhausted': True}
    def corrupt(action, response):
        if action == 'child_result' and bad in ('child', 'result-hash'):
            response['child_run_id' if bad == 'child' else 'result'] = 'wrong' if bad == 'child' else encoded({'answer': 0})
        if action == 'child_blob_get' and bad in ('blob-child', 'blob-hash'):
            response['child_run_id' if bad == 'blob-child' else 'data'] = 'grandchild' if bad == 'blob-child' else base64.b64encode(b'corrupt!').decode()
        return response
    state['transform'] = corrupt
    def work():
        child = nodus.agent.spawn_child(None, spawn_key='part:1')
        completed = nodus.agent.await_children([child], wait_id='joined')[0]
        return nodus.agent.get_child_blob(child, reference) if bad.startswith('blob') else nodus.agent.child_result(completed)
    with pytest.raises(nodus.StepOutcomeUnknown):
        drive(work)


def test_cancel_receipt_reports_request_without_claiming_cleanup(coordinator):
    state, drive = coordinator
    receipts = []
    def work():
        child = nodus.agent.spawn_child(None, spawn_key='part:1')
        receipt = nodus.agent.cancel_child(child, cancel_key='stop:1')
        receipts.append(receipt)
        return receipt.child_run_id
    assert drive(work) == 'child-1'
    assert receipts[0].decision == 'requested'
    assert receipts[0].cancel_requested_at == datetime(2026, 9, 24, 12, tzinfo=timezone.utc)


def test_controls_refuse_parallel_or_nested_step_use(coordinator):
    state, drive = coordinator
    def work():
        session = _agent._current.get()
        session.guard.acquire()
        try:
            with pytest.raises(nodus.ValidationError, match='serial'):
                nodus.agent.spawn_child(None, spawn_key='part:1')
        finally:
            session.guard.release()
        token = _agent._step_context.set(object())
        try:
            with pytest.raises(nodus.ValidationError):
                nodus.agent.next_child_completions(after='0', wait_id='join')
        finally:
            _agent._step_context.reset(token)
        return None
    drive(work)
    assert not any(action.startswith('child') for action, _ in state['calls'])


def test_unavailable_children_never_fall_back_to_account_submission(coordinator):
    state, drive = coordinator
    state['transform'] = lambda action, response: (
        httpx.Response(503, json={'code': 'managed_agent_children_unavailable'}) if action == 'child_spawn' else response)
    with pytest.raises(nodus.AgentChildrenUnavailable):
        drive(lambda: nodus.agent.spawn_child(None, spawn_key='part:1'))
    assert len([action for action, _ in state['calls'] if action == 'child_spawn']) == 1


@pytest.mark.parametrize('permissions', [
    {'secrets': ['key'] * 2}, {'secrets': [str(i) for i in range(65)]},
    {'secrets': ['é' * 128]}, {'egress_allow': ['Example.com', 'example.com']},
    {'connections': ['\nprivate']}, {'secret_refs': 'all'}, {'command': []},
])
def test_invalid_permissions_refuse_before_checkpoint_or_admission(coordinator, permissions):
    state, drive = coordinator
    state['checkpoint'] = True
    with pytest.raises(nodus.ValidationError):
        drive(lambda: nodus.agent.spawn_child(None, spawn_key='part:1', permissions=permissions))
    assert [action for action, _ in state['calls']] == ['session']


@pytest.mark.parametrize('after,limit', [('01', 1), ('-1', 1), ('1e3', 1), ('0', True), ('0', 101), (str(2**63), 1)])
def test_invalid_page_bounds_refuse_before_any_control_rpc(coordinator, after, limit):
    state, drive = coordinator
    with pytest.raises(nodus.ValidationError):
        drive(lambda: nodus.agent.next_child_completions(after=after, wait_id='page', limit=limit))
    assert [action for action, _ in state['calls']] == ['session']


def test_empty_all_join_is_a_replayable_receipt_and_wrong_child_is_refused(coordinator):
    state, drive = coordinator
    state['wait'] = {'decision': 'replay', 'outcomes': [], 'next_after': '0', 'exhausted': True}
    assert drive(lambda: list(nodus.agent.await_children([], wait_id='empty'))) == []
    state['calls'].clear()
    foreign = {'run_id': 'child', 'parent_run_id': 'another-parent', 'spawn_key': 'part:1', 'group_id': 'group', 'depth': 1}
    with pytest.raises(nodus.ValidationError):
        drive(lambda: nodus.agent.await_children([foreign], wait_id='foreign'))
    assert [action for action, _ in state['calls']] == ['session']


def test_cancelled_child_is_not_silently_a_successful_null_result(coordinator):
    state, drive = coordinator
    cancelled = {'event_id': 'event-1', 'sequence': 1, 'child_run_id': 'child-1', 'spawn_key': 'part:1',
                 'status': 'cancelled', 'reason': 'parent_cancelled'}
    with pytest.raises(nodus.ValidationError):
        drive(lambda: nodus.agent.child_result(cancelled))
    assert [action for action, _ in state['calls']] == ['session']


def test_expired_child_result_does_not_reexecute_or_report_null(coordinator):
    state, drive = coordinator
    state['transform'] = lambda action, response: (
        httpx.Response(410, json={'code': 'managed_agent_child_result_expired'}) if action == 'child_result' else response)
    with pytest.raises(nodus.StepResultExpired):
        drive(lambda: nodus.agent.child_result(outcome()))
    assert [action for action, _ in state['calls']] == ['session', 'child_result']


def test_legacy_broker_returns_explicit_child_feature_unavailable(coordinator):
    state, drive = coordinator
    state['transform'] = lambda action, response: (
        httpx.Response(400, json={'error': 'agent_bridge_unavailable'}) if action == 'child_spawn' else response)
    with pytest.raises(nodus.AgentChildrenUnavailable):
        drive(lambda: nodus.agent.spawn_child(None, spawn_key='part:1'))
    assert [action for action, _ in state['calls']] == ['session', 'child_spawn']


def test_wait_identity_mismatch_does_not_acknowledge_yield(coordinator):
    state, drive = coordinator
    state['wait'] = {'decision': 'waiting', 'wait_id': 'another-wait'}
    with pytest.raises(nodus.StepOutcomeUnknown):
        drive(lambda: nodus.agent.next_child_completions(wait_id='page'))


def test_caught_unknown_child_result_poisons_the_driver_before_finish(coordinator):
    state, drive = coordinator
    state['transform'] = lambda action, response: (
        {**response, 'result': encoded('wrong')} if action == 'child_result' else response)
    def work():
        with pytest.raises(nodus.StepOutcomeUnknown):
            nodus.agent.child_result(outcome())
        return 'must not finish'
    with pytest.raises(nodus.StepOutcomeUnknown):
        drive(work)
    assert not any(action == 'finish' for action, _ in state['calls'])
