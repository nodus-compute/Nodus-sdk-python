"""Live connections reach the service and expose links before completion."""
import asyncio
import json
import httpx
import pytest
import nodus
from nodus import cli
from test_sandboxes import sync_client
from test_sandbox_cli import client_factory

URL = "https://wandb.ai/team/project/runs/abc123"

def test_live_run_and_sandbox_wire():
    bodies = []
    def handler(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(201, json={"id": "wl_live", "workload_id": "wl_live", "status": "queued"})
    with sync_client(handler) as client:
        client.run(command="python train.py", connections=["metrics"], sweep_id="experiment-1", policy={"egress_allow": ["example.com"], "secret_refs": ["TOKEN"]})
        client.sandboxes.create(image="python:3.11-slim", connections=["metrics"])
    assert bodies[0]["connections"] == ["metrics"]
    assert bodies[0]["sweep_id"] == "experiment-1"
    assert bodies[0]["policy"] == {"egress_allow": ["example.com"], "secret_refs": ["TOKEN"]}
    assert bodies[1]["connections"] == ["metrics"]

@pytest.mark.parametrize("extra", [{"connections": ["a", "b"]}, {"connections": "metrics"}, {"connections": ["https://private"]}, {"sweep_id": {"key": "private"}}, {"sweep_id": "bad\nvalue"}])
def test_live_invalid_extra_never_reaches_http(extra):
    with sync_client(lambda request: pytest.fail("invalid live fields reached HTTP")) as client:
        with pytest.raises((ValueError, nodus.ValidationError)):
            client.run(command="true", extra=extra)

def test_links_survive_list_and_wait_and_reject_untrusted_urls():
    seen = []
    def handler(request):
        return httpx.Response(200, json={"id": "wl_live", "status": "completed", "links": [{"kind": "wandb", "url": URL}, {"kind": "wandb", "url": "https://wandb.ai/evil\nterminal"}, {"kind": "wandb", "url": "https://evil.test/path"}]})
    with sync_client(handler) as client:
        workload = client.wait("wl_live", progress=False, on_update=lambda wl: seen.append(wl.links))
    assert workload.links == [nodus.WorkloadLink(kind="wandb", url=URL)]
    assert seen == [workload.links]

def test_cli_live_link_printed_before_terminal_poll(monkeypatch, tmp_path, capsys):
    path = tmp_path / "run.toml"
    path.write_text('command = ["true"]\nconnections = ["metrics"]\nsweep_id = "experiment-1"\n')
    reads = 0
    def handler(request):
        nonlocal reads
        if request.method == "POST":
            return httpx.Response(201, json={"workload_id": "wl_live"})
        reads += 1
        if reads == 2:
            assert URL in capsys.readouterr().out
        return httpx.Response(200, json={"id": "wl_live", "status": "running" if reads == 1 else "completed", "links": [{"kind": "wandb", "url": URL}]})
    monkeypatch.setattr(cli, "Client", client_factory(handler))
    assert cli.main(["run", str(path), "--plain", "--poll", "0.01"]) == 0
    assert reads == 2

def test_async_live_fields_and_links():
    async def run():
        bodies, updates = [], []
        def handler(request):
            if request.method == "POST":
                bodies.append(json.loads(request.content))
                return httpx.Response(201, json={"id": "wl_live", "workload_id": "wl_live"})
            row = {"id": "wl_live", "status": "completed", "links": [{"kind": "wandb", "url": URL}]}
            return httpx.Response(200, json={"workloads": [row]} if request.url.path == "/v1/workloads" else row)
        client = nodus.AsyncClient(api_key="nk_test", base_url="https://nodus.invalid")
        client._http = httpx.AsyncClient(base_url="https://nodus.invalid", transport=httpx.MockTransport(handler))
        async with client:
            workload = await client.run(command="true", connections=["metrics"], sweep_id="group-1")
            await client.sandboxes.create(image="python:3.11-slim", connections=["metrics"])
            await workload.wait(progress=False, on_update=lambda wl: updates.append(wl.links))
            listed = await client.list()
        assert bodies[0]["connections"] == ["metrics"] and bodies[0]["sweep_id"] == "group-1"
        assert bodies[1]["connections"] == ["metrics"]
        assert updates == [[nodus.WorkloadLink(kind="wandb", url=URL)]]
        assert listed[0].links == workload.links
    asyncio.run(run())

def test_cli_workload_get_live_link(monkeypatch, capsys):
    monkeypatch.setattr(cli, "Client", client_factory(lambda request: httpx.Response(200, json={"id": "wl_live", "status": "running", "links": [{"kind": "wandb", "url": URL}]})))
    assert cli.main(["workload", "get", "wl_live", "--plain"]) == 0
    assert URL in capsys.readouterr().out
