import asyncio
import json

import httpx
import pytest
import nodus


@pytest.mark.parametrize('asynchronous', [False, True])
@pytest.mark.parametrize('enabled', [False, True])
def test_exact_optional_image_cache_wire(asynchronous, enabled):
    seen = []
    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(202, json={'id': 'sb_cache', 'state': 'creating', 'envelope': {}})
    client = (nodus.AsyncClient if asynchronous else nodus.Client)(api_key='nk_test', base_url='https://nodus.invalid')
    client._http = (httpx.AsyncClient if asynchronous else httpx.Client)(base_url='https://nodus.invalid', transport=httpx.MockTransport(handler))
    if asynchronous:
        async def run():
            async with client:
                return await client.sandboxes.create(image='python:3.12', cache_image=enabled, budget=2)
        asyncio.run(run())
    else:
        with client:
            client.sandboxes.create(image='python:3.12', cache_image=enabled, budget=2)
    if enabled:
        assert seen[0]['source'] == {'image': 'python:3.12', 'cache': True}
    else:
        assert 'source' not in seen[0]
    assert seen[0]['outcome']['max_cost_usd'] == 2


@pytest.mark.parametrize('value', [1, 'true', None])
def test_image_cache_rejects_nonboolean_before_http(value):
    with nodus.Client(api_key='nk_test', base_url='https://nodus.invalid') as client:
        with pytest.raises(nodus.ValidationError):
            client.sandboxes.create(image='python:3.12', cache_image=value)
