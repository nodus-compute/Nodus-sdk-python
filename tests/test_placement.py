"""Private placement intent survives both submission clients without default injection."""
import asyncio
import json
import httpx
import pytest
import nodus


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("placement", [None, {"pool":"pool_customer"}, {"prefer":"any"}])
def test_placement_wire_preserves_explicit_choice_and_gpu_count(asynchronous, placement):
    cls=nodus.AsyncClient if asynchronous else nodus.Client
    client=cls(api_key="nk_test",base_url="https://nodus.invalid")
    def handler(req):
        payload=json.loads(req.content)
        assert payload["requirements"]["gpu_count"]==4
        assert req.headers.get("Idempotency-Key")
        if placement is None:assert "placement" not in payload
        else:assert payload["placement"]==placement
        return httpx.Response(201,json={"id":"wl_test","status":"queued"})
    transport=httpx.MockTransport(handler)
    client._http=(httpx.AsyncClient if asynchronous else httpx.Client)(base_url="https://nodus.invalid",transport=transport)
    if asynchronous:
        async def run():
            async with client:return await client.run(command=["python","train.py"],gpu_count=4,placement=placement)
        result=asyncio.run(run())
    else:
        with client:result=client.run(command=["python","train.py"],gpu_count=4,placement=placement)
    assert result.id=="wl_test"


@pytest.mark.parametrize("placement", [{}, {"pool":"pool_test","prefer":"any"}, {"pool":""}, {"prefer":"private"},
    {"pool":" padded"},{"pool":"line\nbreak"},{"pool":"a"*201},{"pool":True},{"pool":None},{"extra":"field"},"pool_test"])
def test_invalid_placement_rejected_before_network(placement):
    with nodus.Client(api_key="nk_test",base_url="https://nodus.invalid") as client:
        client._http=httpx.Client(base_url="https://nodus.invalid",transport=httpx.MockTransport(lambda req:pytest.fail("invalid placement submitted")))
        with pytest.raises((ValueError,TypeError)):
            client.run(command=["true"],placement=placement)


def test_public_placement_type_constructs_wire_dictionary():
    assert nodus.Placement(pool="pool_customer")=={"pool":"pool_customer"}
