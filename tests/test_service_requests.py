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


import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import pytest


@pytest.fixture
def binary_service(monkeypatch):
    received = []
    payload = bytes(range(256)) + b"\x00\xffarchive"

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            received.append((self.path, self.headers.get("Content-Type"),
                             self.rfile.read(int(self.headers.get("Content-Length", 0)))))
            body = b"x" * 65537 if self.path.endswith("/oversized") else payload
            if self.path.endswith("/empty"):
                body = b""
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", payload, received
    finally:
        server.shutdown()
        server.server_close()
        worker.join()


def test_service_binary_sync_real_http(binary_service):
    url, payload, received = binary_service
    with nodus.Client(api_key="test", base_url=url) as client:
        box = nodus.Sandbox(client, "sb_binary")
        assert box.request("POST", "/binary", port=8080, content=payload) == payload
        assert received[-1][2] == payload
        assert box.request("POST", "/json", port=8080, json={"prompt": "hello"}) == payload
        assert json.loads(received[-1][2]) == {"prompt": "hello"}
        assert received[-1][1] == "application/json"
        assert box.request("POST", "/empty", port=8080, content=b"") == b""
        with pytest.raises(nodus.ValidationError):
            box.request("POST", port=8080, json={}, content=b"raw")
        with pytest.raises(nodus.NodusError):
            box.request("POST", "/oversized", port=8080)


@pytest.mark.asyncio
async def test_service_binary_async_real_http(binary_service):
    from nodus._sandboxes import AsyncSandbox
    url, payload, received = binary_service
    async with nodus.AsyncClient(api_key="test", base_url=url) as client:
        box = AsyncSandbox(client, "sb_binary")
        assert await box.request("POST", "/binary", port=8080, content=payload) == payload
        assert received[-1][2] == payload
        assert await box.request("POST", "/json", port=8080, json={"prompt": "hello"}) == payload
        assert json.loads(received[-1][2]) == {"prompt": "hello"}
        assert await box.request("POST", "/empty", port=8080, content=b"") == b""
        with pytest.raises(nodus.ValidationError):
            await box.request("POST", port=8080, json={}, content=b"raw")
        with pytest.raises(nodus.NodusError):
            await box.request("POST", "/oversized", port=8080)
