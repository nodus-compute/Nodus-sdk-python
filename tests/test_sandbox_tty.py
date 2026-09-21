import json
import httpx
from nodus import Client
from nodus._sandboxes import Sandbox


def test_tty_launch_and_resize_wire():
    seen = []
    def respond(request):
        seen.append((request.url.path, json.loads(request.content)))
        if request.url.path.endswith('/resize'):
            return httpx.Response(202,json={'rows':40,'cols':120,'version':1})
        return httpx.Response(202,json={'id':'ex_test','sandbox_id':'sb_test','state':'queued'})
    with Client(api_key='test-key',base_url='https://api.example.com') as client:
        client._http.close()
        client._http=httpx.Client(base_url="https://api.example.com",transport=httpx.MockTransport(respond))
        execution=Sandbox(client,'sb_test').exec(['bash'],stdin=True,tty=True,rows=24,cols=80)
        execution.resize(40,120)
    assert seen[0][1]=={'command':['bash'],'stdin':True,'tty':True,'rows':24,'cols':80}
    assert seen[1]==('/v1/sandboxes/sb_test/execs/ex_test/resize',{'rows':40,'cols':120})
