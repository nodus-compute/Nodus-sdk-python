"""RL recipe requests stay reviewed and retry-safe."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

import nodus


RECIPE = {
    "id": "reasoning-gym-leg-counting",
    "version": "1.0.0",
    "environment_id": "reasoning-gym",
    "name": "Leg counting",
    "model": "Qwen/Qwen3-0.6B",
    "model_revision": "a" * 40,
    "taskset": "leg_counting:v1",
    "modes": ["evaluate", "train"],
    "available": False,
    "unavailable_reason": "RL runtime is not available",
    "defaults": {"evaluation_tasks": 16, "training_steps": 20},
    "limits": {"evaluation_tasks": {"minimum": 4, "maximum": 64}},
    "outputs": [{"name": "results.json", "required": True}],
    "future_field": "preserved",
}

CONFIGURATION = {
    "recipe_id": "reasoning-gym-leg-counting",
    "recipe_version": "1.0.0",
    "mode": "evaluate",
    "evaluation_tasks": 16,
    "training_steps": 20,
    "max_cost_usd": 5,
    "seed": 42,
    "include_traces": False,
}

PREVIEW = {
    "recipe": RECIPE,
    "configuration": CONFIGURATION,
    "review_token": "review_abc123",
    "phases": [{"id": "evaluation", "name": "Held-out evaluation"}],
    "outputs": [{"name": "results.json", "required": True}],
    "estimate": {"maximum_cost_usd": 5, "estimated_minutes": 8},
    "launchable": True,
    "blocking_reasons": [],
}


def sync_client(handler, *, max_retries=0):
    client = nodus.Client(
        api_key="nk_test",
        base_url="https://nodus.invalid",
        max_retries=max_retries,
    )
    client._http = httpx.Client(
        base_url="https://nodus.invalid",
        transport=httpx.MockTransport(handler),
        headers={"Authorization": "Bearer nk_test"},
    )
    return client


def async_client(handler, *, max_retries=0):
    client = nodus.AsyncClient(
        api_key="nk_test",
        base_url="https://nodus.invalid",
        max_retries=max_retries,
    )
    client._http = httpx.AsyncClient(
        base_url="https://nodus.invalid",
        transport=httpx.MockTransport(handler),
        headers={"Authorization": "Bearer nk_test"},
    )
    return client


@pytest.mark.parametrize("asynchronous", [False, True])
def test_recipe_discovery_preserves_availability_and_wire_fields(asynchronous):
    def handler(request):
        assert request.method == "GET"
        assert request.url.path == "/v1/rl-recipes"
        return httpx.Response(200, json={"recipes": [RECIPE]})

    if asynchronous:
        async def run():
            async with async_client(handler) as client:
                return await client.rl.list_recipes()

        recipes = asyncio.run(run())
    else:
        with sync_client(handler) as client:
            recipes = client.rl.list_recipes()

    assert recipes[0].id == "reasoning-gym-leg-counting"
    assert recipes[0].available is False
    assert recipes[0].unavailable_reason == "RL runtime is not available"
    assert recipes[0].raw["future_field"] == "preserved"


@pytest.mark.parametrize("asynchronous", [False, True])
def test_preview_sends_only_the_explicit_configuration(asynchronous):
    supplied = {
        "recipe_id": "reasoning-gym-leg-counting",
        "recipe_version": "1.0.0",
        "mode": "evaluate",
        "evaluation_tasks": 16,
        "seed": 42,
    }

    def handler(request):
        assert request.method == "POST"
        assert request.url.path == "/v1/rl-runs/preview"
        assert json.loads(request.content) == supplied
        assert "Idempotency-Key" not in request.headers
        return httpx.Response(200, json=PREVIEW)

    if asynchronous:
        async def run():
            async with async_client(handler) as client:
                return await client.rl.preview(supplied)

        preview = asyncio.run(run())
    else:
        with sync_client(handler) as client:
            preview = client.rl.preview(supplied)

    assert preview.configuration == CONFIGURATION
    assert preview.review_token == "review_abc123"
    assert preview.launchable is True
    assert preview.estimate["maximum_cost_usd"] == 5


@pytest.mark.parametrize("asynchronous", [False, True])
def test_launch_uses_reviewed_configuration_and_returns_workload(asynchronous):
    def handler(request):
        assert request.method == "POST"
        assert request.url.path == "/v1/rl-runs"
        assert request.headers["Idempotency-Key"] == "customer-run-20260919-001"
        assert json.loads(request.content) == {
            **CONFIGURATION,
            "review_token": "review_abc123",
        }
        return httpx.Response(
            201,
            json={"workload_id": "wl_rl_123", "status": "queued"},
            headers={"Idempotent-Replayed": "true"},
        )

    if asynchronous:
        async def run():
            async with async_client(handler) as client:
                return await client.rl.launch(
                    configuration=CONFIGURATION,
                    review_token="review_abc123",
                    idempotency_key="customer-run-20260919-001",
                )

        workload = asyncio.run(run())
        assert isinstance(workload, nodus.AsyncWorkload)
    else:
        with sync_client(handler) as client:
            workload = client.rl.launch(
                configuration=CONFIGURATION,
                review_token="review_abc123",
                idempotency_key="customer-run-20260919-001",
            )
        assert isinstance(workload, nodus.Workload)

    assert workload.id == "wl_rl_123"
    assert workload.replayed is True


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize(
    "configuration,review_token,idempotency_key",
    [
        (None, "review_abc123", "run-1"),
        (CONFIGURATION, "", "run-1"),
        (CONFIGURATION, "review_abc123", ""),
        ({**CONFIGURATION, "review_token": "hidden"}, "review_abc123", "run-1"),
    ],
)
def test_launch_requires_separate_explicit_review_inputs(
    asynchronous, configuration, review_token, idempotency_key
):
    def handler(request):
        pytest.fail("invalid launch reached the network")

    if asynchronous:
        async def run():
            async with async_client(handler) as client:
                await client.rl.launch(
                    configuration=configuration,
                    review_token=review_token,
                    idempotency_key=idempotency_key,
                )

        with pytest.raises(nodus.ValidationError):
            asyncio.run(run())
    else:
        with sync_client(handler) as client:
            with pytest.raises(nodus.ValidationError):
                client.rl.launch(
                    configuration=configuration,
                    review_token=review_token,
                    idempotency_key=idempotency_key,
                )


@pytest.mark.parametrize("asynchronous", [False, True])
def test_launch_reuses_idempotency_key_on_ambiguous_retry(monkeypatch, asynchronous):
    requests = []

    def handler(request):
        requests.append(request)
        if len(requests) == 1:
            raise httpx.ReadTimeout("ambiguous result", request=request)
        return httpx.Response(201, json={"workload_id": "wl_retry"})

    if asynchronous:
        async def no_sleep(_):
            return None

        async def run():
            monkeypatch.setattr(nodus.asyncio, "sleep", no_sleep)
            async with async_client(handler, max_retries=1) as client:
                return await client.rl.launch(
                    configuration=CONFIGURATION,
                    review_token="review_abc123",
                    idempotency_key="stable-across-retry",
                )

        workload = asyncio.run(run())
    else:
        monkeypatch.setattr(nodus.time, "sleep", lambda _: None)
        with sync_client(handler, max_retries=1) as client:
            workload = client.rl.launch(
                configuration=CONFIGURATION,
                review_token="review_abc123",
                idempotency_key="stable-across-retry",
            )

    assert workload.id == "wl_retry"
    assert len(requests) == 2
    assert [request.headers["Idempotency-Key"] for request in requests] == [
        "stable-across-retry",
        "stable-across-retry",
    ]
    assert [json.loads(request.content) for request in requests] == [
        {**CONFIGURATION, "review_token": "review_abc123"},
        {**CONFIGURATION, "review_token": "review_abc123"},
    ]
