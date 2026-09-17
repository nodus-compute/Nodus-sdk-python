"""Act approval is intent and outcomes retain their distinct evidence source."""
import json

import httpx
import pytest
import nodus
from test_pools import exercise

ACT = {"id": "act_test", "pool_id": "pool_test", "recommendation_id": "rec_test", "kind": "drain_window", "level": "approve",
    "recommendation": {"kind": "drain_window", "evidence": {"method": "host_seasonal_weekly_p90_below_one"}},
    "created_at": "2026-09-17T12:00:00Z", "expires_at": "2026-09-17T13:00:00Z", "state": "pending", "reason": "", "decided_at": None, "outcome": None}

@pytest.mark.parametrize("asynchronous", [False, True])
def test_act_list_and_approval_preserve_intent(asynchronous):
    def handler(req):
        if req.method == "GET":
            assert dict(req.url.params) == {"limit": "1", "kind": "drain_window"}
            return httpx.Response(200, json={"pool_id": "pool_test", "proposals": [ACT], "next_cursor": None})
        assert req.url.path.endswith('/action-proposals/act_test/approve')
        assert json.loads(req.content) == {}
        return httpx.Response(200, json={**ACT, "state": "approved", "decided_at": "2026-09-17T12:01:00Z"})
    page = exercise(handler, asynchronous, lambda p: p.action_proposals("pool_test", limit=1, kind="drain_window"))
    assert isinstance(page, nodus.PoolActProposals) and page.proposals[0].outcome is None
    result = exercise(handler, asynchronous, lambda p: p.approve_action_proposal("pool_test", "act_test"))
    assert result.state == "approved" and result.outcome is None

@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("change", [{"pool_id": "pool_other"}, {"id": "act_other"}, {"state": "pending"},
    {"state": "applied"}, {"kind": "burst"}, {"expires_at": "2026-09-16T12:00:00Z"}])
def test_act_ack_cannot_change_identity_or_invent_application(asynchronous, change):
    with pytest.raises(nodus.APIError):
        exercise(lambda req: httpx.Response(200, json={**ACT, "state": "approved", "decided_at": "2026-09-17T12:01:00Z", **change}),
            asynchronous, lambda p: p.approve_action_proposal("pool_test", "act_test"))

@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("source,measured,reported", [("customer_reported", 100, None), ("server_observed", None, 100), ("made_up", None, None)])
def test_act_outcome_cannot_mix_customer_report_and_measurement(asynchronous, source, measured, reported):
    outcome = {"source": source, "result": "applied", "recorded_at": "2026-09-17T12:02:00Z", "measured_saving_micros": measured, "reported_saving_micros": reported}
    with pytest.raises(nodus.APIError):
        exercise(lambda req: httpx.Response(200, json={"pool_id": "pool_test", "proposals": [{**ACT, "state": "applied", "outcome": outcome}], "next_cursor": None}), asynchronous, lambda p: p.action_proposals("pool_test"))

def test_act_cli_approval_sends_only_empty_body(monkeypatch, capsys):
    from nodus import cli
    def factory(**kwargs):
        client = nodus.Client(api_key="nk_test", base_url="https://nodus.invalid")
        def handler(req):
            assert json.loads(req.content) == {}
            return httpx.Response(200, json={**ACT, "state": "approved", "decided_at": "2026-09-17T12:01:00Z"})
        client._http = httpx.Client(base_url="https://nodus.invalid", transport=httpx.MockTransport(handler))
        return client
    monkeypatch.setattr(cli, "Client", factory)
    assert cli.main(["pools", "approve-action", "pool_test", "act_test"]) == 0
    assert "does not confirm application" in capsys.readouterr().out

@pytest.mark.parametrize("asynchronous", [False, True])
def test_observed_platform_fee_reduction_retains_narrow_measurement_basis(asynchronous):
    outcome = {"source": "server_observed", "result": "applied", "recorded_at": "2026-09-17T12:02:00Z", "measured_saving_micros": 100, "reported_saving_micros": None, "measurement_basis": "observed_platform_fee_reduction_30m_v1"}
    def call(value):
        return exercise(lambda req: httpx.Response(200, json={"pool_id": "pool_test", "proposals": [{**ACT, "state": "applied", "outcome": value}], "next_cursor": None}), asynchronous, lambda p: p.action_proposals("pool_test"))
    assert call(outcome).proposals[0].outcome.measurement_basis == "observed_platform_fee_reduction_30m_v1"
    for basis in (None, "total_saving"):
        with pytest.raises(nodus.APIError):
            call({**outcome, "measurement_basis": basis})

@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("source,measured,reported", [
    ("customer_reported", None, 100),
    ("server_observed", None, None),
])
def test_act_measurement_basis_requires_actual_server_measurement(asynchronous, source, measured, reported):
    outcome = {"source": source, "result": "no_op", "recorded_at": "2026-09-17T12:02:00Z",
        "measured_saving_micros": measured, "reported_saving_micros": reported,
        "measurement_basis": "observed_platform_fee_reduction_30m_v1"}
    # no_op accepts either source, so this checks the measurement contract itself.
    with pytest.raises(nodus.APIError):
        exercise(lambda req: httpx.Response(200, json={"pool_id": "pool_test", "proposals": [
            {**ACT, "state": "no_op", "outcome": outcome}], "next_cursor": None}),
            asynchronous, lambda p: p.action_proposals("pool_test"))

@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("measured", [None, 0])
def test_act_unknown_measurement_and_measured_zero_remain_distinct(asynchronous, measured):
    basis = None if measured is None else "observed_platform_fee_reduction_30m_v1"
    outcome = {"source": "server_observed", "result": "applied", "recorded_at": "2026-09-17T12:02:00Z",
        "measured_saving_micros": measured, "reported_saving_micros": None, "measurement_basis": basis}
    page = exercise(lambda req: httpx.Response(200, json={"pool_id": "pool_test", "proposals": [
        {**ACT, "state": "applied", "outcome": outcome}], "next_cursor": None}),
        asynchronous, lambda p: p.action_proposals("pool_test"))
    assert page.proposals[0].outcome.measured_saving_micros == measured
    assert page.proposals[0].outcome.measurement_basis == basis
