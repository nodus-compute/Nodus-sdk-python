"""RL submissions compose with live connections and structured outputs."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

import nodus
from test_rl import CONFIGURATION, async_client, sync_client


@pytest.mark.parametrize("asynchronous", [False, True])
def test_custom_rl_composition_survives_lost_response(monkeypatch, asynchronous):
    requests = []
    arguments = {
        "image": "pytorch/pytorch:2.6.0-cuda12.4-cudnn9-runtime",
        "command": ["python", "train.py"],
        "compute_class": "accelerator",
        "budget": 3,
        "idempotency_key": "custom-rl-composition",
        "connections": ["metrics"],
        "outputs": {
            "scores": {
                "path": "outputs/scores.csv",
                "sink": {"connection": "warehouse", "table": "rl_scores"},
            }
        },
        "extra": {
            "rl": {
                "schema_version": 1,
                "environment_id": "custom",
                "mode": "train",
                "model": "example/synthetic-policy",
                "planned_tasks": 32,
            }
        },
    }

    def handler(request):
        assert request.method == "POST"
        assert request.url.path == "/v1/workloads"
        requests.append(request)
        if len(requests) == 1:
            raise httpx.ReadTimeout("response lost after submission", request=request)
        return httpx.Response(
            202,
            json={"workload_id": "wl_custom_rl", "status": "queued"},
            headers={"Idempotent-Replayed": "true"},
        )

    if asynchronous:
        async def no_sleep(_):
            return None

        async def run():
            monkeypatch.setattr(nodus.asyncio, "sleep", no_sleep)
            async with async_client(handler, max_retries=1) as client:
                return await client.run(**arguments)

        workload = asyncio.run(run())
        assert isinstance(workload, nodus.AsyncWorkload)
    else:
        monkeypatch.setattr(nodus.time, "sleep", lambda _: None)
        with sync_client(handler, max_retries=1) as client:
            workload = client.run(**arguments)
        assert isinstance(workload, nodus.Workload)

    assert workload.id == "wl_custom_rl"
    assert workload.replayed is True
    assert len(requests) == 2
    assert [request.headers["Idempotency-Key"] for request in requests] == [
        "custom-rl-composition",
        "custom-rl-composition",
    ]
    assert requests[0].content == requests[1].content
    assert json.loads(requests[0].content) == {
        "requirements": {"compute_class": "accelerator"},
        "outcome": {"max_cost_usd": 3.0},
        "continuity": {"mode": "checkpointed", "resume_on_interruption": True},
        "stages": [{
            "id": "main",
            "source": {
                "image": "pytorch/pytorch:2.6.0-cuda12.4-cudnn9-runtime",
                "command": ["python", "train.py"],
            },
            "outputs": {
                "scores": {
                    "path": "outputs/scores.csv",
                    "sink": {"connection": "warehouse", "table": "rl_scores"},
                }
            },
        }],
        "connections": ["metrics"],
        "rl": {
            "schema_version": 1,
            "environment_id": "custom",
            "mode": "train",
            "model": "example/synthetic-policy",
            "planned_tasks": 32,
        },
    }


@pytest.mark.parametrize("asynchronous", [False, True])
def test_managed_rl_handle_refreshes_links_and_sink_metadata(asynchronous):
    requests = []
    states = []
    link = nodus.WorkloadLink(
        kind="wandb", url="https://wandb.ai/example/project/runs/synthetic"
    )
    refreshes = iter([
        {
            "id": "wl_managed_composition",
            "status": "completed",
            "links": [{"kind": link.kind, "url": link.url}],
            "sink_error": "Synthetic sink delivery failed",
        },
        {"id": "wl_managed_composition", "status": "completed", "links": []},
    ])
    arguments = {
        "configuration": dict(CONFIGURATION),
        "review_token": "review_composition",
        "idempotency_key": "managed-rl-composition",
    }

    def handler(request):
        requests.append((request.method, request.url.path))
        if request.method == "POST":
            assert len(requests) == 1
            assert request.url.path == "/v1/rl-runs"
            assert request.headers["Idempotency-Key"] == "managed-rl-composition"
            assert json.loads(request.content) == {
                **CONFIGURATION,
                "review_token": "review_composition",
            }
            return httpx.Response(
                201,
                json={"workload_id": "wl_managed_composition", "status": "queued"},
            )
        assert request.method == "GET"
        assert request.url.path == "/v1/workloads/wl_managed_composition"
        return httpx.Response(200, json=next(refreshes))

    if asynchronous:
        async def run():
            async with async_client(handler) as client:
                workload = await client.rl.launch(**arguments)
                assert isinstance(workload, nodus.AsyncWorkload)
                for _ in range(2):
                    assert await workload.refresh() is workload
                    states.append((list(workload.links), workload.sink_error))

        asyncio.run(run())
    else:
        with sync_client(handler) as client:
            workload = client.rl.launch(**arguments)
            assert isinstance(workload, nodus.Workload)
            for _ in range(2):
                assert workload.refresh() is workload
                states.append((list(workload.links), workload.sink_error))

    assert states == [([link], "Synthetic sink delivery failed"), ([], "")]
    assert requests == [
        ("POST", "/v1/rl-runs"),
        ("GET", "/v1/workloads/wl_managed_composition"),
        ("GET", "/v1/workloads/wl_managed_composition"),
    ]
