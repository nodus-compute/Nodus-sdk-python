"""Burst approvals preserve exact server amounts and observable intent states."""
import json

import httpx
import pytest
import nodus
from test_pools import exercise

PROPOSAL = {"id": "prop_test", "pool_id": "pool_test", "kind": "burst", "workload_id": "wl_test", "stage_id": "main",
    "envelope_version": 1, "generation": 1, "device_count": 2, "expected_cost_micros": 12500000,
    "approved_cost_micros": None, "state": "pending", "reason": "approval_required",
    "created_at": "2026-09-17T12:00:00Z", "expires_at": "2026-09-17T12:30:00Z", "decided_at": None, "applied_at": None}


@pytest.mark.parametrize("asynchronous", [False, True])
def test_proposal_page_keeps_server_money_state_and_cursor(asynchronous):
    def handler(req):
        assert (req.method, req.url.path) == ("GET", "/v1/pools/pool_test/proposals")
        assert dict(req.url.params) == {"limit": "1", "cursor": "opaque", "state": "pending"}
        return httpx.Response(200, json={"pool_id": "pool_test", "proposals": [PROPOSAL], "next_cursor": "next"})
    page = exercise(handler, asynchronous, lambda p: p.proposals("pool_test", limit=1, cursor="opaque", state="pending"))
    assert isinstance(page, nodus.PoolProposals)
    assert page.proposals[0].expected_cost_micros == 12500000
    assert page.proposals[0].approved_cost_micros is None
    assert page.next_cursor == "next"


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("action,state", [("approve", "approved"), ("approve", "applying"), ("approve", "applied"), ("reject", "rejected")])
def test_decision_sends_no_invented_amount_and_keeps_actual_state(asynchronous, action, state):
    result = {**PROPOSAL, "state": state, "reason": "customer_approved" if action == "approve" else "customer_rejected",
        "approved_cost_micros": 12500000 if action == "approve" else None, "decided_at": "2026-09-17T12:01:00Z",
        "applied_at": "2026-09-17T12:02:00Z" if state == "applied" else None}
    def handler(req):
        assert (req.method, req.url.path) == ("POST", "/v1/pools/pool_test/proposals/prop_test/" + action)
        assert json.loads(req.content) == {}
        return httpx.Response(200, json=result)
    proposal = exercise(handler, asynchronous, lambda p: getattr(p, action + "_proposal")("pool_test", "prop_test"))
    assert proposal.state == state
    assert (proposal.applied_at is not None) == (state == "applied")


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("change", [{"pool_id": "pool_other"}, {"expected_cost_micros": 0}, {"expected_cost_micros": True},
    {"expected_cost_micros": 2**63}, {"device_count": 0}, {"generation": 0}, {"state": "unknown"},
    {"approved_cost_micros": 1}, {"state": "approved"}, {"applied_at": "2026-09-17T12:02:00Z"},
    {"expires_at": "invalid"}, {"expires_at": "2026-09-17T11:00:00Z"}, {"decided_at": "2026-09-17T11:00:00Z"}])
def test_inconsistent_proposal_wire_fails_closed(asynchronous, change):
    with pytest.raises(nodus.APIError):
        exercise(lambda req: httpx.Response(200, json={"pool_id": "pool_test", "proposals": [{**PROPOSAL, **change}], "next_cursor": None}),
            asynchronous, lambda p: p.proposals("pool_test"))


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("action", ["approve", "reject"])
@pytest.mark.parametrize("change", [{"id": "prop_other"}, {"pool_id": "pool_other"}, {"state": "pending"}])
def test_decision_acknowledgement_is_bound_to_exact_request(asynchronous, action, change):
    body = {**PROPOSAL, **change}
    with pytest.raises(nodus.APIError):
        exercise(lambda req: httpx.Response(200, json=body), asynchronous,
            lambda p: getattr(p, action + "_proposal")("pool_test", "prop_test"))


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("query", [{"limit": 0}, {"limit": 101}, {"limit": True}, {"cursor": ""}, {"cursor": "x" * 2049}, {"state": "open"}])
def test_invalid_page_query_never_reaches_network(asynchronous, query):
    def handler(req):
        pytest.fail("invalid query reached network")
    with pytest.raises(nodus.ValidationError):
        exercise(handler, asynchronous, lambda p: p.proposals("pool_test", **query))


def test_proposal_cli_prints_intent_and_keeps_payload_empty(monkeypatch, capsys):
    from nodus import cli
    calls = []
    def factory(**kwargs):
        client = nodus.Client(api_key="nk_test", base_url="https://nodus.invalid")
        def handler(req):
            calls.append(req)
            if req.method == "GET":
                return httpx.Response(200, json={"pool_id": "pool_test", "proposals": [PROPOSAL], "next_cursor": "next"})
            return httpx.Response(200, json={**PROPOSAL, "state": "approved", "reason": "customer_approved", "approved_cost_micros": 12500000, "decided_at": "2026-09-17T12:01:00Z"})
        client._http = httpx.Client(base_url="https://nodus.invalid", transport=httpx.MockTransport(handler))
        return client
    monkeypatch.setattr(cli, "Client", factory)
    assert cli.main(["pools", "proposals", "pool_test", "--state", "pending", "--limit", "1", "--json"]) == 0
    assert dict(calls[-1].url.params) == {"state": "pending", "limit": "1"}
    assert cli.main(["pools", "approve", "pool_test", "prop_test"]) == 0
    assert json.loads(calls[-1].content) == {}
    output = capsys.readouterr().out
    assert "approved" in output
    assert "does not itself rent" in output


def test_proposal_types_are_public_exports():
    assert "PoolProposal" in nodus.__all__
    assert "PoolProposals" in nodus.__all__


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("page", [
    {"pool_id": "pool_other", "proposals": [], "next_cursor": None},
    {"pool_id": "pool_test", "proposals": [], "next_cursor": "next"},
    {"pool_id": "pool_test", "proposals": [PROPOSAL, PROPOSAL], "next_cursor": None},
    {"pool_id": "pool_test", "proposals": [PROPOSAL]},
])
def test_proposal_page_cannot_cross_pool_or_invent_continuation(asynchronous, page):
    with pytest.raises(nodus.APIError):
        exercise(lambda req: httpx.Response(200, json=page), asynchronous, lambda p: p.proposals("pool_test"))
