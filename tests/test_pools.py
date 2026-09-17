"""Pool operations preserve authenticated wire requests and token secrecy."""
import asyncio
import json

import httpx
import pytest

import nodus
from nodus import cli


def exercise(handler, asynchronous, action):
    cls = nodus.AsyncClient if asynchronous else nodus.Client
    client = cls(api_key="nk_test", base_url="https://nodus.invalid", max_retries=2)
    http = httpx.AsyncClient if asynchronous else httpx.Client
    client._http = http(base_url="https://nodus.invalid", transport=httpx.MockTransport(handler),
                        headers={"Authorization": "Bearer nk_test"})
    if asynchronous:
        async def run():
            async with client:
                return await action(client.pools)
        return asyncio.run(run())
    with client:
        return action(client.pools)


@pytest.mark.parametrize("asynchronous", [False, True])
def test_enrollment_token_is_explicit_and_repr_redacted(asynchronous):
    def handler(req):
        assert req.headers["Authorization"] == "Bearer nk_test"
        assert (req.method, req.url.path) == ("POST", "/v1/pools/pool_test/enrollment-tokens")
        assert json.loads(req.content) == {"mode": "observe"}
        return httpx.Response(201, json={"id": "pet_test", "token": "synthetic-secret",
            "mode": "observe", "expires_at": "2026-09-18T12:00:00Z"})
    result = exercise(handler, asynchronous, lambda pools: pools.enrollment_token("pool_test"))
    assert result.token == "synthetic-secret"
    assert result.mode == "observe"
    assert result.expires_at == "2026-09-18T12:00:00Z"
    assert "synthetic-secret" not in repr(result)


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("failure", ["timeout", "redirect", "unavailable"])
def test_token_creation_is_never_replayed_or_redirected(asynchronous, failure):
    calls = []
    def handler(req):
        calls.append(req.url.host)
        if failure == "timeout":
            raise httpx.ReadTimeout("unknown result", request=req)
        return httpx.Response(307 if failure == "redirect" else 503,
            headers={"Location": "https://untrusted.invalid"}, json={"error": "unavailable"})
    with pytest.raises(nodus.NodusError):
        exercise(handler, asynchronous, lambda pools: pools.enrollment_token("pool_test"))
    assert calls == ["nodus.invalid"]


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("pool_id", ["../other", "pool_test/hosts", "pool_test?x", "pool_test%2Fother", "pool_test\n"])
def test_invalid_pool_ids_never_reach_network(asynchronous, pool_id):
    def handler(req):
        pytest.fail("invalid ID reached network")
    with pytest.raises(nodus.ValidationError):
        exercise(handler, asynchronous, lambda pools: pools.enrollment_token(pool_id))


@pytest.mark.parametrize("asynchronous", [False, True])
def test_remove_host_handles_empty_204(asynchronous):
    def handler(req):
        assert (req.method, req.url.path) == ("DELETE", "/v1/pools/pool_test/hosts/host_test")
        return httpx.Response(204)
    assert exercise(handler, asynchronous, lambda pools: pools.remove_host("pool_test", "host_test")) is None


def test_pool_cli_token_is_an_explicit_secret_output(monkeypatch, capsys):
    def factory(**kwargs):
        client = nodus.Client(api_key="nk_test", base_url="https://nodus.invalid")
        def handler(req):
            assert req.url.path == "/v1/pools/pool_test/enrollment-tokens"
            return httpx.Response(201, json={"id": "pet_test", "token": "synthetic-secret",
                "mode": "observe", "expires_at": "2026-09-18T12:00:00Z"})
        client._http = httpx.Client(base_url="https://nodus.invalid", transport=httpx.MockTransport(handler))
        return client
    monkeypatch.setattr(cli, "Client", factory)
    assert cli.main(["pools", "token", "pool_test"]) == 0
    assert capsys.readouterr().out == "synthetic-secret\n"
    assert "pools" in cli.build_parser().format_help()


POOL = {
    "id": "pool_test", "name": "Research", "kind": "hosts", "predict_enabled": False,
    "route_enabled": False, "wait_policy": "after_wait", "wait_alpha": 1,
    "waiting_budget_pct": 10, "burst_approval": "auto", "burst_threshold_micros": 0,
    "burst_timeout_behaviour": "keep_waiting", "platform_rate_micros": 0,
    "owned_cost_micros_per_hour": 0, "sandbox_capable": False, "state": "active",
    "created_at": "2026-09-17T12:00:00Z", "updated_at": "2026-09-17T12:00:00Z",
}
DEVICE = {"id": "device_test", "host_id": "host_test", "pool_id": "pool_test",
          "device_index": 0, "model": "NVIDIA RTX 4090", "memory_mb": 24564,
          "device_uuid": "GPU-test", "state": "ready"}
HOST = {"id": "host_test", "pool_id": "pool_test", "name": "research-node",
        "agent_version": "0.1.0", "agent_mode": "observe", "state": "ready",
        "inventory": {"hostname": "research-node", "kernel": "Linux", "memory_mb": 64000,
            "disk_mb": 128000, "devices": [{k: DEVICE[k] for k in
                ("device_index", "model", "memory_mb", "device_uuid")}]},
        "last_heartbeat_at": None, "drain_requested_at": None,
        "enrolled_at": "2026-09-17T12:00:00Z", "devices": [DEVICE]}


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("operation,args,kwargs,method,path,body,response", [
    ("create", ("Research",), {}, "POST", "/v1/pools", {"name": "Research"}, POOL),
    ("list", (), {}, "GET", "/v1/pools", None, {"pools": [POOL]}),
    ("get", ("pool_test",), {}, "GET", "/v1/pools/pool_test", None, POOL),
    ("update", ("pool_test",), {"owned_cost_micros_per_hour": 0}, "PATCH", "/v1/pools/pool_test", {"owned_cost_micros_per_hour": 0}, POOL),
    ("hosts", ("pool_test",), {}, "GET", "/v1/pools/pool_test/hosts", None, {"hosts": [HOST]}),
    ("drain_host", ("pool_test", "host_test"), {}, "POST", "/v1/pools/pool_test/hosts/host_test/drain", None, {**HOST, "state": "draining"}),
])
def test_pool_lifecycle_wire(asynchronous, operation, args, kwargs, method, path, body, response):
    def handler(req):
        assert (req.method, req.url.path) == (method, path)
        assert (json.loads(req.content) if req.content else None) == body
        assert req.headers["Authorization"] == "Bearer nk_test"
        return httpx.Response(201 if operation == "create" else 200, json=response)
    result = exercise(handler, asynchronous, lambda pools: getattr(pools, operation)(*args, **kwargs))
    row = result[0] if isinstance(result, list) else result
    assert row.id == ("host_test" if operation in ("hosts", "drain_host") else "pool_test")
    if operation == "hosts":
        assert row.last_heartbeat_at is None
        assert row.devices[0].memory_mb == 24564
        assert row.devices[0].device_index == 0
    if operation == "get":
        assert row.owned_cost_micros_per_hour == 0
        assert row.predict_enabled is False


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("kwargs", [{}, {"name": ""}, {"owned_cost_micros_per_hour": -1},
    {"owned_cost_micros_per_hour": True}, {"owned_cost_micros_per_hour": 0.5},
    {"owned_cost_micros_per_hour": 2**63}])
def test_invalid_patch_refused_before_network(asynchronous, kwargs):
    def handler(req):
        pytest.fail("invalid update reached network")
    with pytest.raises(nodus.ValidationError):
        exercise(handler, asynchronous, lambda pools: pools.update("pool_test", **kwargs))


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("body", [{}, {"pools": None}, {"pools": [{}]}, {"pools": ["bad"]}])
def test_malformed_lists_raise_sdk_error(asynchronous, body):
    with pytest.raises(nodus.APIError):
        exercise(lambda req: httpx.Response(200, json=body), asynchronous, lambda pools: pools.list())


def test_cli_create_and_hosts_follow_public_wire(monkeypatch, capsys):
    def factory(**kwargs):
        client = nodus.Client(api_key="nk_test", base_url="https://nodus.invalid")
        def handler(req):
            if req.method == "POST":
                assert json.loads(req.content) == {"name": "Research"}
                return httpx.Response(201, json=POOL)
            assert req.url.path == "/v1/pools/pool_test/hosts"
            return httpx.Response(200, json={"hosts": [HOST]})
        client._http = httpx.Client(base_url="https://nodus.invalid", transport=httpx.MockTransport(handler))
        return client
    monkeypatch.setattr(cli, "Client", factory)
    assert cli.main(["pools", "create", "Research"]) == 0
    assert capsys.readouterr().out == "pool_test\n"
    assert cli.main(["--plain", "pools", "hosts", "pool_test"]) == 0
    output = capsys.readouterr().out
    assert "host_test" in output and "research-node" in output and "ready" in output


@pytest.mark.parametrize("asynchronous", [False, True])
def test_name_limit_matches_server_utf8_bytes(asynchronous):
    def handler(req):
        pytest.fail("oversized UTF-8 name reached network")
    with pytest.raises(nodus.ValidationError):
        exercise(handler, asynchronous, lambda pools: pools.create("界" * 100))


@pytest.mark.parametrize("asynchronous", [False, True])
def test_token_response_cannot_inject_terminal_controls(asynchronous):
    def handler(req):
        return httpx.Response(201, json={"id": "pet_test", "token": "secret\u009b31m",
            "mode": "observe", "expires_at": "2026-09-18T12:00:00Z"})
    with pytest.raises(nodus.APIError):
        exercise(handler, asynchronous, lambda pools: pools.enrollment_token("pool_test"))


METRICS = {
    "capacity_seconds": 3600, "unknown_seconds": 0, "allocated_seconds": 1800,
    "busy_seconds": 900, "idle_allocated_seconds": 900, "stranded_seconds": 1800,
    "foreign_seconds": 1800, "allocation_pct": 50, "busy_pct": 25,
    "busy_of_allocated_pct": 50, "fragmentation_seconds": None,
    "queued_seconds": None, "burst_seconds": None, "data_status": "complete",
}
UTILIZATION = {
    "pool_id": "pool_test", "from": "2026-09-10T12:00:00Z", "to": "2026-09-10T13:00:00Z",
    "bucket": "hour", "summary": METRICS,
    "hosts": [{"host_id": "host_test", "name": "Research", "device_count": 1,
        "summary": METRICS, "buckets": [{"start": "2026-09-10T12:00:00Z",
            "end": "2026-09-10T13:00:00Z", "metrics": METRICS,
            "foreign_device_ids": ["device_test"]}]}],
}


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("status", ["complete", "partial", "no_data"])
def test_utilization_preserves_known_zero_unknown_and_foreign_identity(asynchronous, status):
    import copy
    body = copy.deepcopy(UTILIZATION)
    if status != "complete":
        metrics = {key: None for key in METRICS}
        metrics.update(capacity_seconds=3600 if status == "partial" else 0,
                       unknown_seconds=3600 if status == "partial" else 0, data_status=status)
        body["summary"] = metrics
        body["hosts"][0]["summary"] = metrics
        body["hosts"][0]["buckets"][0]["metrics"] = metrics
        if status == "no_data":
            body["hosts"][0]["buckets"][0]["foreign_device_ids"] = []
    else:
        body["summary"].update(busy_pct=0, busy_seconds=0, idle_allocated_seconds=1800, busy_of_allocated_pct=0)
    def handler(req):
        assert (req.method, req.url.path) == ("GET", "/v1/pools/pool_test/utilization")
        assert dict(req.url.params) == {"from": "2026-09-10T12:00:00+00:00", "to": UTILIZATION["to"], "bucket": "hour"}
        assert req.headers["Authorization"] == "Bearer nk_test"
        return httpx.Response(200, json=body)
    result = exercise(handler, asynchronous, lambda pools: pools.utilization("pool_test",
        from_="2026-09-10T12:00:00+00:00", to=UTILIZATION["to"], bucket="hour"))
    assert isinstance(result, nodus.PoolUtilization)
    assert result.from_ == UTILIZATION["from"]
    assert result.summary.busy_pct == (0 if status == "complete" else None)
    assert result.summary.data_status == status
    assert result.summary.fragmentation_seconds is None
    assert result.hosts[0].buckets[0].foreign_device_ids == ([] if status == "no_data" else ["device_test"])


@pytest.mark.parametrize("asynchronous", [False, True])
def test_utilization_defaults_are_server_owned(asynchronous):
    def handler(req):
        assert not req.url.params
        return httpx.Response(200, json={**UTILIZATION, "hosts": []})
    assert exercise(handler, asynchronous, lambda pools: pools.utilization("pool_test")).hosts == []


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("kwargs", [{"bucket": "minute"}, {"from_": 42}, {"to": ""}])
def test_invalid_utilization_query_is_refused(asynchronous, kwargs):
    def handler(req):
        pytest.fail("invalid query reached the network")
    with pytest.raises(nodus.ValidationError):
        exercise(handler, asynchronous, lambda pools: pools.utilization("pool_test", **kwargs))


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("metric,value", [("busy_pct", "0"), ("busy_pct", 101), ("capacity_seconds", None),
    ("unknown_seconds", -1), ("allocated_seconds", True), ("data_status", "idle")])
def test_malformed_utilization_metrics_raise_sdk_error(asynchronous, metric, value):
    body = {**UTILIZATION, "summary": {**METRICS, metric: value}}
    with pytest.raises(nodus.APIError):
        exercise(lambda req: httpx.Response(200, json=body), asynchronous,
                 lambda pools: pools.utilization("pool_test"))


def test_utilization_cli_preserves_nulls_and_query(monkeypatch, capsys):
    def factory(**kwargs):
        client = nodus.Client(api_key="nk_test", base_url="https://nodus.invalid")
        def handler(req):
            assert req.url.path == "/v1/pools/pool_test/utilization"
            assert dict(req.url.params) == {"from": UTILIZATION["from"], "to": UTILIZATION["to"], "bucket": "hour"}
            return httpx.Response(200, json=UTILIZATION)
        client._http = httpx.Client(base_url="https://nodus.invalid", transport=httpx.MockTransport(handler))
        return client
    monkeypatch.setattr(cli, "Client", factory)
    arguments = ["pools", "utilization", "pool_test", "--from", UTILIZATION["from"], "--to", UTILIZATION["to"], "--bucket", "hour"]
    assert cli.main([*arguments, "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == UTILIZATION
    assert cli.main(["--plain", *arguments]) == 0
    output = capsys.readouterr().out
    assert "25%" in output and "50%" in output and "complete" in output
    assert "Not available" in output


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("updates", [
    {"unknown_seconds": 3600}, {"data_status": "partial", "unknown_seconds": 1},
    {"data_status": "no_data"}, {"allocated_seconds": 3601}, {"busy_seconds": 1801},
    {"idle_allocated_seconds": 1}, {"stranded_seconds": 1}, {"foreign_seconds": 1801},
    {"allocation_pct": 0}, {"busy_of_allocated_pct": None},
])
def test_contradictory_utilization_metrics_fail_closed(asynchronous, updates):
    body = {**UTILIZATION, "summary": {**METRICS, **updates}}
    with pytest.raises(nodus.APIError):
        exercise(lambda req: httpx.Response(200, json=body), asynchronous,
                 lambda pools: pools.utilization("pool_test"))


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("field,value", [("from", "not-a-timestamp"), ("from", "2026-09-10T12:00:00"),
    ("from", "2026-09-10T12:30:00Z"), ("from", "2026-09-10T13:00:00+00:60"), ("from", "2026-09-10T14:00:00Z"),
    ("from", "2026-07-10T12:00:00Z"), ("to", "2026-09-10T13:00:00.000000001Z")])
def test_utilization_response_window_must_be_valid(asynchronous, field, value):
    with pytest.raises(nodus.APIError):
        exercise(lambda req: httpx.Response(200, json={**UTILIZATION, field: value}), asynchronous,
                 lambda pools: pools.utilization("pool_test"))


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("change", ["missing", "out_of_window", "invalid_time"])
def test_host_buckets_must_cover_the_reported_window(asynchronous, change):
    import copy
    body = copy.deepcopy(UTILIZATION)
    buckets = body["hosts"][0]["buckets"]
    if change == "missing":
        buckets.clear()
    elif change == "out_of_window":
        buckets[0]["end"] = "2026-09-10T14:00:00Z"
    else:
        buckets[0]["start"] = "tomorrow"
    with pytest.raises(nodus.APIError):
        exercise(lambda req: httpx.Response(200, json=body), asynchronous,
                 lambda pools: pools.utilization("pool_test"))


@pytest.mark.parametrize("asynchronous", [False, True])
def test_daily_utilization_accepts_partial_day_boundaries(asynchronous):
    def handler(req):
        assert dict(req.url.params) == {"bucket": "day"}
        return httpx.Response(200, json={**UTILIZATION, "bucket": "day"})
    result = exercise(handler, asynchronous, lambda pools: pools.utilization("pool_test", bucket="day"))
    assert result.hosts[0].buckets[0].start == UTILIZATION["from"]


@pytest.mark.parametrize("asynchronous", [False, True])
def test_complete_zero_capacity_has_no_percentage_denominator(asynchronous):
    metrics = {key: (0 if key.endswith("_seconds") and key not in
        ("fragmentation_seconds", "queued_seconds", "burst_seconds") else None) for key in METRICS}
    metrics["data_status"] = "complete"
    body = {**UTILIZATION, "summary": metrics, "hosts": []}
    result = exercise(lambda req: httpx.Response(200, json=body), asynchronous,
                      lambda pools: pools.utilization("pool_test"))
    assert result.summary.busy_seconds == 0
    assert result.summary.busy_pct is None


@pytest.mark.parametrize("cost", [None, 0, 1234567])
def test_utilization_preserves_settled_burst_cost(cost):
    from nodus import UtilizationMetrics
    metrics = UtilizationMetrics.from_dict({**METRICS, "burst_cost_micros": cost})
    assert metrics.burst_cost_micros == cost


@pytest.mark.parametrize("cost", [-1, True, 1.5, "10", 2**63])
def test_utilization_rejects_invalid_burst_money(cost):
    from nodus import UtilizationMetrics
    with pytest.raises(nodus.APIError):
        UtilizationMetrics.from_dict({**METRICS, "burst_cost_micros": cost})
