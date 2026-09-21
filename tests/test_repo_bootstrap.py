import json
import httpx
import nodus
import pytest
from test_sandbox_cli import client_factory

BOOTSTRAP = {"repo": "org/api", "ref": "main", "setup": "make deps", "dotfiles": "org/dotfiles"}


def test_repo_bootstrap_sdk_preserves_intent_and_warning():
    def handler(request):
        assert json.loads(request.content)["bootstrap"] == BOOTSTRAP
        assert request.headers["Idempotency-Key"] == "repo-create"
        return httpx.Response(202, json={"id": "sb_repo", "state": "idle", "warnings": ["bootstrap_failed"]})
    with client_factory(handler)() as client:
        box = client.sandboxes.create(name="api", image="customer:qualified", bootstrap=BOOTSTRAP, idempotency_key="repo-create")
        assert not box.is_terminal
        assert box.warnings == ["bootstrap_failed"]


@pytest.mark.asyncio
async def test_repo_bootstrap_async_forwards_without_credentials():
    def handler(request):
        assert json.loads(request.content)["bootstrap"] == BOOTSTRAP
        return httpx.Response(202, json={"id": "sb_repo", "state": "creating"})
    async with nodus.AsyncClient(api_key="test", base_url="https://nodus.test") as client:
        await client._http.aclose()
        client._http = httpx.AsyncClient(base_url="https://nodus.test", transport=httpx.MockTransport(handler))
        assert (await client.sandboxes.create(image="customer:qualified", bootstrap=BOOTSTRAP)).id == "sb_repo"
