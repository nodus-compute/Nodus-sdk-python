"""Owner scopes stay explicit and consistent across workload pages."""

import httpx
import pytest

import nodus
from nodus import cli


def transport_capture(requests):
    def handler(request):
        requests.append(request)
        offset = int(request.url.params.get('offset', '0'))
        return httpx.Response(200, json={
            'workloads': [{'id': f'wl_{offset}', 'owner_user_id': 'user_42'}],
            'next_offset': 1 if offset == 0 else None,
        })
    return httpx.MockTransport(handler)


@pytest.mark.parametrize('scope', [None, 'mine', 'team'])
def test_sync_scope_on_every_page(scope):
    requests = []
    with nodus.Client(api_key='test', base_url='https://nodus.invalid') as client:
        client._http.close()
        client._http = httpx.Client(base_url='https://nodus.invalid', transport=transport_capture(requests))
        assert client.list(scope=scope)[0].owner_user_id == 'user_42'
        assert len(list(client.iter_workloads(scope=scope))) == 2
    assert len(requests) == 3
    for request in requests:
        assert request.url.params.get('scope') == scope


@pytest.mark.asyncio
@pytest.mark.parametrize('scope', [None, 'mine', 'team'])
async def test_async_scope_on_every_page(scope):
    requests = []
    async with nodus.AsyncClient(api_key='test', base_url='https://nodus.invalid') as client:
        await client._http.aclose()
        client._http = httpx.AsyncClient(base_url='https://nodus.invalid', transport=transport_capture(requests))
        assert (await client.list(scope=scope))[0].owner_user_id == 'user_42'
        assert len([wl async for wl in client.iter_workloads(scope=scope)]) == 2
    assert len(requests) == 3
    for request in requests:
        assert request.url.params.get('scope') == scope


@pytest.mark.parametrize('scope', ['', 'all', 'Mine', 1, []])
def test_bad_scope_never_contacts_api(scope):
    requests = []
    with nodus.Client(api_key='test', base_url='https://nodus.invalid') as client:
        client._http.close()
        client._http = httpx.Client(base_url='https://nodus.invalid', transport=transport_capture(requests))
        with pytest.raises(ValueError):
            client.list(scope=scope)
        with pytest.raises(ValueError):
            list(client.iter_workloads(scope=scope))
    assert not requests


@pytest.mark.asyncio
@pytest.mark.parametrize('scope', ['', 'all', 'Mine', 1, []])
async def test_async_bad_scope_never_contacts_api(scope):
    requests = []
    async with nodus.AsyncClient(api_key='test', base_url='https://nodus.invalid') as client:
        await client._http.aclose()
        client._http = httpx.AsyncClient(base_url='https://nodus.invalid', transport=transport_capture(requests))
        with pytest.raises(ValueError):
            await client.list(scope=scope)
        with pytest.raises(ValueError):
            [wl async for wl in client.iter_workloads(scope=scope)]
    assert not requests


def test_owner_null_and_absent_fields():
    with nodus.Client(api_key='test') as client:
        workload = nodus.Workload(client)
        assert workload.owner_user_id is None
        workload._absorb({'owner_user_id': 'user_42'})
        workload._absorb({'id': 'wl_test'})
        assert workload.owner_user_id == 'user_42'
        workload._absorb({'owner_user_id': None})
        assert workload.owner_user_id is None
        assert workload.raw['owner_user_id'] is None


@pytest.mark.parametrize('selection', ['mine', 'team', 'active'])
def test_cli_list_scope_and_status(selection, monkeypatch):
    requests = []
    with nodus.Client(api_key='test', base_url='https://nodus.invalid') as client:
        client._http.close()
        client._http = httpx.Client(base_url='https://nodus.invalid', transport=transport_capture(requests))
        monkeypatch.setattr(cli, 'Client', lambda **kwargs: client)
        assert cli.main(['list', selection]) == 0
    key = 'status' if selection == 'active' else 'scope'
    assert requests[0].url.params[key] == selection
