"""Freeze preserves saved files without claiming memory or storage price."""
import asyncio

import httpx
import pytest
import nodus

FREEZE = {"id": "freeze_test", "workload_id": "wl_test", "stage_id": "main", "generation": 1,
    "state": "frozen", "manifest_id": "manifest_test", "requested_at": "2026-09-17T12:00:00Z",
    "frozen_at": "2026-09-17T12:01:00Z", "retained_bytes": 1024, "storage_charge_micros": None,
    "storage_billing_status": "retained_storage_not_separately_metered"}


def exercise(asynchronous, handler, call):
    if asynchronous:
        async def run():
            async with nodus.AsyncClient(api_key="nk_test", base_url="https://nodus.invalid") as client:
                await client._http.aclose()
                client._http = httpx.AsyncClient(base_url="https://nodus.invalid", transport=httpx.MockTransport(handler))
                return await call(client)
        return asyncio.run(run())
    with nodus.Client(api_key="nk_test", base_url="https://nodus.invalid") as client:
        client._http.close()
        client._http = httpx.Client(base_url="https://nodus.invalid", transport=httpx.MockTransport(handler))
        return call(client)

@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("method,verb,path,state", [("freeze", "POST", "freeze", "requested"), ("freeze_status", "GET", "freeze", "frozen"), ("resume", "POST", "resume", "resuming")])
def test_freeze_methods_keep_wire_state_and_unpriced_storage(asynchronous, method, verb, path, state):
    def handler(req):
        assert (req.method, req.url.path) == (verb, "/v1/workloads/wl_test/" + path)
        return httpx.Response(200 if verb == "GET" else 202, json={**FREEZE, "state": state, "frozen_at": None if state == "requested" else FREEZE["frozen_at"]})
    result = exercise(asynchronous, handler, lambda c: getattr(c, method)("wl_test"))
    assert isinstance(result, nodus.WorkloadFreeze)
    assert result.state == state and result.storage_charge_micros is None and result.retained_bytes == 1024

@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("change", [{"workload_id": "wl_other"}, {"state": "unknown"}, {"generation": True}, {"retained_bytes": -1},
    {"storage_charge_micros": 0}, {"frozen_at": None}, {"requested_at": "invalid"}, {"manifest_id": ""}])
def test_freeze_response_fails_closed(asynchronous, change):
    with pytest.raises(nodus.APIError):
        exercise(asynchronous, lambda req: httpx.Response(200, json={**FREEZE, **change}), lambda c: c.freeze_status("wl_test"))


def test_freezing_and_frozen_are_known_nonterminal_states():
    assert nodus.WorkloadStatus.FREEZING.value == "freezing"
    assert nodus.WorkloadStatus.FROZEN.value == "frozen"
    assert "frozen" not in nodus.TERMINAL

def test_freeze_cli_reports_unknown_storage_without_zero(monkeypatch, capsys):
    from nodus import cli
    def factory(**kwargs):
        client = nodus.Client(api_key="nk_test", base_url="https://nodus.invalid")
        client._http = httpx.Client(base_url="https://nodus.invalid", transport=httpx.MockTransport(lambda req: httpx.Response(200, json=FREEZE)))
        return client
    monkeypatch.setattr(cli, "Client", factory)
    assert cli.main(["freeze-status", "wl_test"]) == 0
    output = capsys.readouterr().out
    assert "1024 bytes" in output and "not separately metered" in output and "$0" not in output

@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("state", ["resuming", "resumed", "failed"])
def test_freeze_ack_must_confirm_freeze_intent(asynchronous, state):
    with pytest.raises(nodus.APIError):
        exercise(asynchronous, lambda req: httpx.Response(202, json={**FREEZE, "state": state}), lambda c: c.freeze("wl_test"))
