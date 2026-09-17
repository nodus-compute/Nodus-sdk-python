"""Action settings distinguish policy intent from trusted automatic readiness."""
import copy
import json

import httpx
import pytest
import nodus
from test_pools import exercise

KINDS = ("idle_reclaim", "defragment", "drain_window", "wait_tuning")
POLICY = {"kind": "idle_reclaim", "level": "recommend", "window_cron": "* * * * *", "parallelism_cap": 1}
SETTINGS = {"pool_id": "pool_test", "kill_switch": False, "policies": [
    {**POLICY, "kind": kind, "updated_at": "2026-09-17T12:00:00Z", "shadow_qualified": False} for kind in KINDS]}
SHADOW = {"id": "shadow_test", "pool_id": "pool_test", "kind": "idle_reclaim", "window_cron": "* * * * *", "parallelism_cap": 1,
    "cycle_start": "2026-09-10T12:00:00Z", "cycle_end": "2026-09-17T12:00:00Z", "state": "running", "ended_at": None,
    "trusted_hours": 167, "elapsed_hours": 168, "gap_hours": 1, "would_have_acted": 0, "producer_available": True, "qualifies_auto": False}

@pytest.mark.parametrize("asynchronous", [False, True])
def test_action_settings_and_writes_preserve_exact_ack(asynchronous):
    def handler(req):
        response = copy.deepcopy(SETTINGS)
        if req.method == "PUT":
            body = json.loads(req.content)
            if req.url.path.endswith("act-kill-switch"):
                assert body == {"enabled": True}
                response["kill_switch"] = True
            else:
                assert body == {**POLICY, "level": "off"}
                response["policies"][0]["level"] = "off"
        return httpx.Response(200, json=response)
    settings = exercise(handler, asynchronous, lambda p: p.action_policies("pool_test"))
    assert isinstance(settings, nodus.PoolActionSettings)
    assert len(settings.policies) == 4
    changed = exercise(handler, asynchronous, lambda p: p.set_action_policy("pool_test", **{**POLICY, "level": "off"}))
    assert changed.policies[0].level == "off"
    assert exercise(handler, asynchronous, lambda p: p.set_act_kill_switch("pool_test", True)).kill_switch

@pytest.mark.parametrize("asynchronous", [False, True])
def test_shadow_pages_and_start_do_not_invent_qualification(asynchronous):
    def handler(req):
        if req.method == "POST":
            assert json.loads(req.content) == POLICY
            return httpx.Response(201, json=SHADOW)
        assert dict(req.url.params) == {"limit": "1", "cursor": "opaque", "kind": "idle_reclaim"}
        return httpx.Response(200, json={"pool_id": "pool_test", "runs": [SHADOW], "next_cursor": "next"})
    run = exercise(handler, asynchronous, lambda p: p.start_shadow("pool_test", **POLICY))
    assert run.gap_hours == 1 and not run.qualifies_auto
    page = exercise(handler, asynchronous, lambda p: p.shadow_runs("pool_test", limit=1, cursor="opaque", kind="idle_reclaim"))
    assert page.next_cursor == "next" and page.runs[0].trusted_hours == 167

@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("change", [{"kind": "burst"}, {"level": "apply"}, {"window_cron": "*/5 * * * *"}, {"window_cron": "0 2 * * 1,1"},
    {"parallelism_cap": True}, {"parallelism_cap": 0}, {"parallelism_cap": 33}])
def test_invalid_policy_never_reaches_server(asynchronous, change):
    def handler(req):
        pytest.fail("invalid policy reached network")
    with pytest.raises(nodus.ValidationError):
        exercise(handler, asynchronous, lambda p: p.set_action_policy("pool_test", **{**POLICY, **change}))

@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("change", [{"pool_id": "pool_other"}, {"trusted_hours": 169}, {"trusted_hours": True}, {"gap_hours": 0},
    {"qualifies_auto": True}, {"producer_available": False}, {"cycle_end": "2026-09-16T12:00:00Z"}, {"would_have_acted": -1}])
def test_inconsistent_shadow_wire_fails_closed(asynchronous, change):
    with pytest.raises(nodus.APIError):
        exercise(lambda req: httpx.Response(201, json={**SHADOW, **change}), asynchronous,
            lambda p: p.start_shadow("pool_test", **POLICY))

@pytest.mark.parametrize("asynchronous", [False, True])
def test_changed_policy_or_kill_ack_cannot_claim_confirmation(asynchronous):
    with pytest.raises(nodus.APIError):
        exercise(lambda req: httpx.Response(200, json=SETTINGS), asynchronous,
            lambda p: p.set_action_policy("pool_test", **{**POLICY, "level": "off"}))
    with pytest.raises(nodus.APIError):
        exercise(lambda req: httpx.Response(200, json=SETTINGS), asynchronous,
            lambda p: p.set_act_kill_switch("pool_test", True))

def test_action_cli_preserves_complete_policy_and_reports_gaps(monkeypatch, capsys):
    from nodus import cli
    calls = []
    def factory(**kwargs):
        client = nodus.Client(api_key="nk_test", base_url="https://nodus.invalid")
        def handler(req):
            calls.append(req)
            if req.url.path.endswith("shadow-runs"):
                return httpx.Response(200, json={"pool_id": "pool_test", "runs": [SHADOW], "next_cursor": None})
            return httpx.Response(200, json=SETTINGS)
        client._http = httpx.Client(base_url="https://nodus.invalid", transport=httpx.MockTransport(handler))
        return client
    monkeypatch.setattr(cli, "Client", factory)
    assert cli.main(["pools", "action-policy", "pool_test", "idle_reclaim", "recommend", "--window-cron", "* * * * *", "--parallelism-cap", "1"]) == 0
    assert json.loads(calls[-1].content) == POLICY
    assert cli.main(["pools", "shadows", "pool_test"]) == 0
    assert "167/168" in capsys.readouterr().out
