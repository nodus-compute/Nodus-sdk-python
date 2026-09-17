import json
import httpx
import nodus


def test_service_request_preserves_explicit_command_and_lifetime():
    sent=[]
    def handler(request):
        sent.append(json.loads(request.content))
        return httpx.Response(202,json={'id':'sb_service','state':'creating','envelope':sent[-1]})
    with nodus.Client(api_key='test',base_url='https://nodus.test') as client:
        client._http.close()
        client._http=httpx.Client(base_url='https://nodus.test',transport=httpx.MockTransport(handler))
        service={'command':['python','server.py'],'port':8080,'health_path':'/health'}
        box=client.sandboxes.create(image='customer:qualified',service=service,lifecycle={'max_lifetime_s':90000},budget=2,idempotency_key='service-deploy-1')
        assert sent[0]['service']==service
        assert sent[0]['lifecycle']=={'max_lifetime_s':90000}
        assert sent[0]['outcome']['max_cost_usd']==2
        assert box.envelope['service']==service


def test_guest_http_never_retries_and_cannot_escape_route():
    import pytest
    from nodus._sandboxes import Sandbox
    calls=[]
    def handler(request):
        calls.append(request)
        return httpx.Response(503,json={'code':'upstream_unavailable','message':'unavailable'})
    with nodus.Client(api_key='test',base_url='https://nodus.test',max_retries=4) as client:
        client._http.close()
        client._http=httpx.Client(base_url='https://nodus.test',transport=httpx.MockTransport(handler))
        box=Sandbox(client,'sb_service')
        with pytest.raises(nodus.NodusError):
            box.request('POST','/infer',port=8080,json={'prompt':'hello'})
        assert len(calls)==1
        assert str(calls[0].url)=='https://nodus.test/v1/sandboxes/sb_service/ports/8080/infer'
        for path in ('../me','//other.test/','/%2e%2e/%2e%2e/me','/x\\evil','/x%0d%0a'):
            with pytest.raises(nodus.ValidationError):
                box.request('GET',path,port=8080)
        assert len(calls)==1
