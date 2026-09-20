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
    "storage_settled_usd": 0.0,
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


def test_storage_is_a_distinct_account_component_without_recalculating_totals():
    meter = nodus.Meter.from_dict({
        **COMPONENTS, "subscription_settled_usd": 7.25, "storage_settled_usd": 4.0,
        "settled_usd": 19.0, "accruing_usd": 1.27, "total_now_usd": 20.27,
    })
    assert meter.storage_settled_usd == 4.0
    assert meter.subscription_settled_usd == 7.25
    assert meter.compute_settled_usd == 2.5
    assert meter.platform_fee_settled_usd == 0.08
    assert meter.settled_usd == 19.0
    assert meter.total_now_usd == 20.27


@pytest.mark.parametrize("wire_value, expected", [
    (0, 0.0), (4, 4.0), (4.25, 4.25), ("4.25", 4.25), (-2.25, -2.25),
])
def test_storage_uses_the_existing_numeric_component_handling(wire_value, expected):
    meter = nodus.Meter.from_dict({"storage_settled_usd": wire_value})
    assert meter.storage_settled_usd == expected


@pytest.mark.parametrize("invalid", [
    None, True, False, "invalid", float("inf"), float("-inf"), float("nan"),
    "Infinity", "NaN", 2 ** 4096,
], ids=[
    "null", "true", "false", "text", "infinity", "negative_infinity", "nan",
    "infinity_text", "nan_text", "oversized_integer",
])
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
    assert meter.storage_settled_usd == 0.0


def test_storage_preserves_all_existing_positional_meter_fields():
    meter = nodus.Meter(2.58, 1.27, 0.02, 3.85, None, {"legacy": True},
                       2.5, 0.08, 99.0, 1.25, 0.02)
    assert meter.settled_usd == 2.58
    assert meter.accruing_usd == 1.27
    assert meter.accruing_rate_usd_hour == 0.02
    assert meter.total_now_usd == 3.85
    assert meter.as_of is None
    assert meter.raw == {"legacy": True}
    assert meter.compute_settled_usd == 2.5
    assert meter.platform_fee_settled_usd == 0.08
    assert meter.subscription_settled_usd == 99.0
    assert meter.compute_accruing_usd == 1.25
    assert meter.platform_fee_accruing_usd == 0.02
    assert meter.storage_settled_usd == 0.0
