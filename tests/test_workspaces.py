import json
import httpx
from test_sandboxes import SANDBOX,sync_client

def test_named_workspace_creation_attachment_and_listing():
    record={"id":"ws_repo","name":"repo","size_gb":0.1,"holder_id":None,"billing_status":"disabled_no_approved_storage_rate"}
    def handler(request):
        if request.url.path=="/v1/workspaces":
            if request.method=="POST":
                assert json.loads(request.content)=={"name":"repo","size_gb":0.1}
                return httpx.Response(201,json=record)
            return httpx.Response(200,json={"workspaces":[record]})
        assert request.url.path=="/v1/sandboxes"
        assert json.loads(request.content)["workspace"]=={"name":"repo","mount":"/workspace"}
        return httpx.Response(202,json=SANDBOX)
    with sync_client(handler) as client:
        assert client.workspaces.create("repo",size_gb=0.1)==record
        client.sandboxes.create(image="python:3.12",workspace={"name":"repo","mount":"/workspace"})
        assert client.workspaces.list()==[record]

def test_async_workspace_creation_and_attachment():
    import asyncio
    import nodus
    async def scenario():
        def handler(request):
            if request.url.path == "/v1/workspaces":
                if request.method == "POST":
                    assert json.loads(request.content) == {"name": "repo", "size_gb": 0.1}
                    return httpx.Response(201, json={"name": "repo"})
                return httpx.Response(200, json={"workspaces": [{"name": "repo"}]})
            assert json.loads(request.content)["workspace"] == {"name": "repo", "mount": "/project"}
            return httpx.Response(202, json=SANDBOX)
        async with nodus.AsyncClient(api_key="nk_live_test", base_url="https://nodus.invalid") as client:
            await client._http.aclose()
            client._http = httpx.AsyncClient(base_url="https://nodus.invalid", transport=httpx.MockTransport(handler))
            assert await client.workspaces.create("repo", size_gb=0.1) == {"name": "repo"}
            assert await client.workspaces.list() == [{"name": "repo"}]
            await client.sandboxes.create(image="python:3.12", workspace={"name": "repo", "mount": "/project"})
    asyncio.run(scenario())
