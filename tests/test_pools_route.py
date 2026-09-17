"""Route consent and settings cross the authenticated public wire boundary."""
import json
import math

import httpx
import pytest
import nodus
from test_pools import POOL, exercise


@pytest.mark.parametrize("asynchronous", [False, True])
def test_route_activation_and_zero_settings(asynchronous):
    payload = {"route_enabled": True, "accepted_route_rate_version": "route-platform-v1",
               "accepted_route_rate_micros": 20000, "wait_policy": "never", "wait_alpha": 0,
               "waiting_budget_pct": 0, "burst_approval": "always", "burst_threshold_micros": 0,
               "burst_timeout_behaviour": "cancel"}
    def handler(req):
        assert (req.method, req.url.path) == ("PATCH", "/v1/pools/pool_test")
        assert json.loads(req.content) == payload
        return httpx.Response(200, json={**POOL, **payload, "platform_rate_micros": 20000})
    result = exercise(handler, asynchronous, lambda pools: pools.set_route("pool_test", True,
        accepted_rate_version="route-platform-v1", accepted_rate_micros=20000,
        wait_policy="never", wait_alpha=0, waiting_budget_pct=0, burst_approval="always",
        burst_threshold_micros=0, burst_timeout_behaviour="cancel"))
    assert result.route_enabled is True
    assert result.wait_alpha == 0
    assert result.waiting_budget_pct == 0


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("settings", [{"wait_policy": "unknown"}, {"wait_alpha": math.inf},
    {"wait_alpha": math.nan}, {"wait_alpha": 10**500}, {"wait_alpha": -1}, {"wait_alpha": True},
    {"waiting_budget_pct": 101}, {"burst_threshold_micros": -1}, {"burst_threshold_micros": True},
    {"burst_threshold_micros": 2**63}, {"burst_approval": "unknown"},
    {"burst_timeout_behaviour": "unknown"}, {}])
def test_invalid_route_settings_never_reach_network(asynchronous, settings):
    def handler(req):
        pytest.fail("invalid settings reached network")
    with pytest.raises(nodus.ValidationError):
        exercise(handler, asynchronous, lambda pools: pools.update_route_settings("pool_test", **settings))


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("enabled,consent", [(True, {}), (True, {"accepted_rate_version":"old", "accepted_rate_micros":20000}),
    (False,{"accepted_rate_version":"route-platform-v1"}), (1,{})])
def test_route_consent_is_explicit(asynchronous, enabled, consent):
    with pytest.raises(nodus.ValidationError):
        exercise(lambda req: pytest.fail("invalid consent reached network"), asynchronous,
                 lambda pools: pools.set_route("pool_test", enabled, **consent))


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("wrong", [{"id":"pool_other"}, {"route_enabled": False}, {"platform_rate_micros":0}])
def test_route_response_cannot_confirm_another_pool_or_rate(asynchronous, wrong):
    with pytest.raises(nodus.APIError):
        exercise(lambda req: httpx.Response(200,json={**POOL,"route_enabled":True,"platform_rate_micros":20000,**wrong}),
            asynchronous,lambda pools: pools.set_route("pool_test",True,accepted_rate_version="route-platform-v1",accepted_rate_micros=20000))


@pytest.mark.parametrize("asynchronous", [False, True])
def test_execute_token_requires_bound_host_and_preserves_secret(asynchronous):
    def handler(req):
        assert json.loads(req.content)=={"mode":"execute","host_id":"host_test"}
        return httpx.Response(201,json={"id":"pet_test","token":"synthetic-secret","mode":"execute","expires_at":"2026-09-18T12:00:00Z"})
    token=exercise(handler,asynchronous,lambda pools:pools.enrollment_token("pool_test",mode="execute",host_id="host_test"))
    assert token.mode=="execute" and "synthetic-secret" not in repr(token)


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("kwargs", [{"mode":"execute"},{"host_id":"host_test"},{"mode":"execute","host_id":"../host"},{"mode":"other"}])
def test_invalid_execute_binding_never_reaches_network(asynchronous,kwargs):
    with pytest.raises(nodus.ValidationError):
        exercise(lambda req:pytest.fail("invalid enrollment reached network"),asynchronous,
            lambda pools:pools.enrollment_token("pool_test",**kwargs))


def test_route_cli_consent_and_execute_token(monkeypatch, capsys):
    from nodus import cli
    calls=[]
    def factory(**kwargs):
        client=nodus.Client(api_key="nk_test",base_url="https://nodus.invalid")
        def handler(req):
            calls.append(json.loads(req.content))
            if req.url.path.endswith("enrollment-tokens"):
                return httpx.Response(201,json={"id":"pet_test","token":"synthetic-secret","mode":"execute","expires_at":"2026-09-18T12:00:00Z"})
            return httpx.Response(200,json={**POOL,**calls[-1],"platform_rate_micros":20000})
        client._http=httpx.Client(base_url="https://nodus.invalid",transport=httpx.MockTransport(handler))
        return client
    monkeypatch.setattr(cli,"Client",factory)
    assert cli.main(["pools","route","pool_test","on","--accept-rate-version","route-platform-v1","--accept-rate-micros","20000"])==0
    assert calls[-1]=={"route_enabled":True,"accepted_route_rate_version":"route-platform-v1","accepted_route_rate_micros":20000}
    assert "Route enabled" in capsys.readouterr().out
    assert cli.main(["pools","route-settings","pool_test","--wait-alpha","0","--wait-policy","never"])==0
    assert calls[-1]=={"wait_alpha":0.0,"wait_policy":"never"}
    capsys.readouterr()
    assert cli.main(["pools","token","pool_test","--mode","execute","--host-id","host_test"])==0
    assert calls[-1]=={"mode":"execute","host_id":"host_test"}
    assert capsys.readouterr().out=="synthetic-secret\n"

@pytest.mark.parametrize("asynchronous", [False, True])
def test_cheaper_policy_is_explicit_and_server_authorized(asynchronous):
    def handler(req):
        assert json.loads(req.content) == {"wait_policy": "cheaper"}
        return httpx.Response(200, json={**POOL, "wait_policy": "cheaper"})
    result = exercise(handler, asynchronous, lambda p: p.update_route_settings("pool_test", wait_policy="cheaper"))
    assert result.wait_policy == "cheaper"
