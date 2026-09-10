import json
from typing import get_args, get_type_hints

import httpx
import pytest

import nodus
from nodus._brief import build_payload
from nodus.requests import Requirements


TIERS = ["lowest_cost", "lower_cost", "balanced", "faster", "fastest"]


def test_requirements_expose_optional_routing_fields():
    hints = get_type_hints(Requirements)
    assert set(get_args(hints["optimization"])) == {"", *TIERS}
    assert hints["disk_gb"] is float
    assert hints["vcpus"] is float
    assert not {"optimization", "disk_gb", "vcpus"} & Requirements.__required_keys__


@pytest.mark.parametrize("tier", [None, "", *TIERS])
@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.asyncio
async def test_optimization_reaches_submission_with_balanced_default(tier, asynchronous):
    requirements = Requirements(disk_gb=32.5, vcpus=4.5)
    if tier is not None:
        requirements["optimization"] = tier
    sent = []

    def handler(request):
        assert request.method == "POST"
        assert request.url.path == "/v1/workloads"
        sent.append(json.loads(request.content))
        return httpx.Response(202, json={"workload_id": "wl_fixture"})

    if asynchronous:
        async with nodus.AsyncClient(api_key="nk_test", base_url="https://nodus.invalid") as client:
            await client._http.aclose()
            client._http = httpx.AsyncClient(base_url="https://nodus.invalid", transport=httpx.MockTransport(handler))
            workload = await client.run(command=["python", "train.py"], requirements=requirements, budget=1)
    else:
        with nodus.Client(api_key="nk_test", base_url="https://nodus.invalid") as client:
            client._http.close()
            client._http = httpx.Client(base_url="https://nodus.invalid", transport=httpx.MockTransport(handler))
            workload = client.run(command=["python", "train.py"], requirements=requirements, budget=1)
    assert workload.id == "wl_fixture"
    assert len(sent) == 1
    for key, value in requirements.items():
        assert sent[0]["requirements"][key] == value
    if tier is None:
        assert sent[0]["requirements"]["optimization"] == "balanced"


@pytest.mark.parametrize("tier", [None, "", *TIERS])
def test_stage_routing_override_remains_distinct(tier):
    requirements = Requirements(disk_gb=64, vcpus=8)
    if tier is not None:
        requirements["optimization"] = tier
    stage = {
        "id": "train",
        "source": {"command": ["python", "train.py"]},
        "requirements": requirements,
    }
    payload = build_payload(stages=[stage], requirements=Requirements(optimization="lower_cost"), budget=1)
    assert payload["requirements"]["optimization"] == "lower_cost"
    assert payload["stages"][0]["requirements"] == stage["requirements"]
