import asyncio
import json
import httpx
import nodus
from test_sandboxes import SANDBOX, sync_client

REPORT={"latest":{"rss_bytes":None,"cpu_seconds":2.5,"generation":3},"rollup_24h":{"samples":1,"cpu_seconds":None},"history":[],"last_output_at":None,"meter":{"accruing_rate_usd_hour":0.25}}

def test_metrics_keeps_unavailable_values_and_explicit_stuck_threshold():
    def handler(request):
        if request.method=="POST":
            assert json.loads(request.content)["stuck_after_s"]==30
            return httpx.Response(202,json=SANDBOX)
        assert request.url.path=="/v1/sandboxes/sb_agent/metrics"
        return httpx.Response(200,json=REPORT)
    with sync_client(handler) as client:
        box=client.sandboxes.create(image="python:3.12",stuck_after_s=30)
        assert box.metrics()==REPORT

def test_async_metrics_same_wire_contract():
    async def scenario():
        def handler(request):
            if request.method=="POST":
                assert json.loads(request.content)["stuck_after_s"]==60
                return httpx.Response(202,json=SANDBOX)
            assert request.url.path=="/v1/sandboxes/sb_agent/metrics"
            return httpx.Response(200,json=REPORT)
        async with nodus.AsyncClient(api_key="nk_live_test",base_url="https://nodus.invalid") as client:
            await client._http.aclose()
            client._http=httpx.AsyncClient(base_url="https://nodus.invalid",transport=httpx.MockTransport(handler))
            box=await client.sandboxes.create(image="python:3.12",stuck_after_s=60)
            assert await box.metrics()==REPORT
    asyncio.run(scenario())
