import asyncio

import httpx
import pytest

import nodus
from nodus._brief import build_payload


def test_single_source_assets_and_outputs_compile_to_real_stage():
    payload = build_payload(image="image:v1", command=["python", "train.py"], budget=5,
                            source_asset_id="asset_code", inputs=[{"name": "data", "asset_id": "asset_data"}],
                            outputs={"model": "model.bin"})
    assert "source" not in payload
    assert payload["stages"] == [{"id": "main", "source": {"image": "image:v1", "command": ["python", "train.py"], "asset_id": "asset_code"}, "outputs": {"model": "model.bin"}}]
    assert payload["inputs"] == [{"name": "data", "asset_id": "asset_data"}]
    assert payload["outcome"] == {"max_cost_usd": 5}


@pytest.mark.parametrize("options", [
    {"source_asset_id": "bad/id"},
    {"inputs": [{"name": "data", "uri": "https://example.com"}]},
    {"outputs": {"model": "../outside"}},
    {"outputs": {"../model": "model.bin"}},
    {"outputs": {"model": "/absolute"}},
    {"outputs": {"CON": "model.bin"}},
    {"outputs": {"model.": "model.bin"}},
    {"outputs": {"model": "model.bin", "MODEL": "another.bin"}},
    {"stages": [{"id": "CON", "outputs": {"model": "model.bin"}}]},
    {"outputs": {"model": "model.bin"}, "framework": "train_eval"},
    {"stages": [{"id": "train"}], "source_asset_id": "asset_code"},
])
def test_invalid_new_fields_fail_before_submission(options):
    with pytest.raises((ValueError, TypeError)):
        build_payload(budget=5, **options)


@pytest.mark.parametrize("asynchronous", [False, True])
def test_python_run_sends_new_fields(asynchronous):
    import json
    seen = []
    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(202, json={"id": "wl_test", "status": "accepted"})
    cls = nodus.AsyncClient if asynchronous else nodus.Client
    client = cls(api_key="nk_test", base_url="https://nodus.invalid")
    http = httpx.AsyncClient if asynchronous else httpx.Client
    client._http = http(base_url="https://nodus.invalid", transport=httpx.MockTransport(handler))
    args = dict(command=["python", "train.py"], budget=5, source_asset_id="asset_code",
                inputs=[{"name": "data", "asset_id": "asset_data"}], outputs={"model": "model.bin"})
    if asynchronous:
        async def run():
            async with client:
                return await client.run(**args)
        result = asyncio.run(run())
    else:
        with client:
            result = client.run(**args)
    assert result.id == "wl_test"
    assert seen[0]["stages"][0]["source"]["asset_id"] == "asset_code"
    assert seen[0]["inputs"][0]["asset_id"] == "asset_data"
