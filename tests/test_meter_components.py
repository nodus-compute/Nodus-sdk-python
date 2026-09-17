"""Read the server's cost components without double-counting account charges."""
import asyncio

import httpx
import pytest

import nodus


COMPONENTS = {
    "compute_settled_usd": 2.5,
    "platform_fee_settled_usd": 0.08,
    "subscription_settled_usd": 0.0,
    "compute_accruing_usd": 1.25,
    "platform_fee_accruing_usd": 0.02,
}


def read_meter(asynchronous, meter):
    cls = nodus.AsyncClient if asynchronous else nodus.Client
    client = cls(api_key="nk_synthetic", base_url="https://nodus.invalid")
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={
        "id": "wl_test", "status": "running", "spend_usd": 2.58, "meter": meter,
    }))
    http = httpx.AsyncClient if asynchronous else httpx.Client
    client._http = http(base_url="https://nodus.invalid", transport=transport)
    if asynchronous:
        async def run():
            async with client:
                return await client.get("wl_test")
        return asyncio.run(run())
    with client:
        return client.get("wl_test")


@pytest.mark.parametrize("asynchronous", [False, True])
def test_components_preserve_wire_values_and_authoritative_total(asynchronous):
    workload = read_meter(asynchronous, {
        **COMPONENTS, "settled_usd": 2.58, "accruing_usd": 1.27,
        "total_now_usd": 3.85, "as_of": "2026-09-17T12:00:00Z",
    })
    assert workload.meter is not None
    for field, expected in COMPONENTS.items():
        assert getattr(workload.meter, field) == expected
    assert workload.meter.total_now_usd == 3.85
    assert workload.cost_now_usd == 3.85


@pytest.mark.parametrize("asynchronous", [False, True])
def test_older_server_components_default_zero_without_changing_total(asynchronous):
    workload = read_meter(asynchronous, {
        "settled_usd": 2.58, "accruing_usd": 1.27, "total_now_usd": 3.85,
    })
    assert workload.meter is not None
    for field in COMPONENTS:
        assert getattr(workload.meter, field) == 0.0
    assert workload.cost_now_usd == 3.85


def test_subscription_is_read_as_account_component_without_inference():
    meter = nodus.Meter.from_dict({
        **COMPONENTS, "subscription_settled_usd": 99,
        "settled_usd": 101.58, "accruing_usd": 1.27, "total_now_usd": 102.85,
    })
    assert meter.subscription_settled_usd == 99
    assert meter.compute_settled_usd == 2.5
    assert meter.platform_fee_settled_usd == 0.08
    assert meter.total_now_usd == 102.85


@pytest.mark.parametrize("invalid", [None, True, "invalid", float("inf"), 2 ** 4096],
                         ids=["null", "boolean", "text", "infinity", "oversized_integer"])
def test_unusable_components_do_not_interrupt_status_polling(invalid):
    meter = nodus.Meter.from_dict({field: invalid for field in COMPONENTS})
    assert meter is not None
    for field in COMPONENTS:
        assert getattr(meter, field) == 0.0


def test_additive_components_preserve_existing_positional_meter_constructor():
    meter = nodus.Meter(2.58, 1.27, 0.02, 3.85, None, {"legacy": True})
    assert meter.settled_usd == 2.58
    assert meter.raw == {"legacy": True}
    assert meter.compute_settled_usd == 0.0
