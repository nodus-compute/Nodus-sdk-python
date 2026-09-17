import pytest
import nodus

def test_step_requires_explicit_stable_identity_before_invocation():
    calls=[]
    @nodus.step(name='send',version='1')
    def send(value):
        calls.append(value)
        return value
    with pytest.raises(nodus.ValidationError):
        send(42)
    assert calls==[]

import base64
from datetime import datetime, timedelta, timezone
import http.server
import json
import os
import socketserver
import sqlite3
import threading
import uuid

@pytest.fixture
def journal_socket(tmp_path,monkeypatch):
    import tempfile
    from pathlib import Path
    socket_dir=tempfile.TemporaryDirectory(prefix='nds-agent-',dir='/tmp')
    path=Path(socket_dir.name)/'j.sock'
    db=sqlite3.connect(tmp_path/'journal.sqlite',check_same_thread=False)
    db.execute('CREATE TABLE journal (step TEXT PRIMARY KEY, definition TEXT, status TEXT, result TEXT, token TEXT, external_key TEXT)')
    effects=sqlite3.connect(tmp_path/'remote-effects.sqlite',check_same_thread=False)
    effects.execute('CREATE TABLE effects (id INTEGER PRIMARY KEY, external_key TEXT UNIQUE)')
    state={'effects':effects,'status':'active','result':None,'lost_completion':False,'unknown_on_resume':False,'complete_requests':0}
    encode=lambda value:base64.b64encode(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).decode()
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self,*args):
            pass
        def do_POST(self):
            request=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            action=self.path[1:]
            result={}
            if action in ('session','renew'):
                result={'run':{'run_id':state.get('run_id','cycle-42'),'name':'main','version':'1','image_digest':'sha256:'+'a'*64,'status':state['status'],'input':encode(state.get('input',{'invoice':42})),'result':state['result']},'session_token':'scoped-capability','epoch':1,'expires_at':(datetime.now(timezone.utc)+timedelta(minutes=1)).isoformat()}
            elif action=='claim':
                step_id=request['step_id']
                definition=json.dumps({k:request[k] for k in ['name','version','effect','encoding','input']},sort_keys=True)
                row=db.execute('SELECT definition,status,result,token,external_key FROM journal WHERE step=?',(step_id,)).fetchone()
                if row is None:
                    token,external=uuid.uuid4().hex,uuid.uuid4().hex
                    db.execute('INSERT INTO journal VALUES (?,?,?,?,?,?)',(step_id,definition,'started',None,token,external));db.commit()
                    result={'decision':'execute','step_id':step_id,'claim_token':token,'external_key':external,'revision':1}
                elif row[0]!=definition:
                    self.send_response(409);self.end_headers();self.wfile.write(b'{"code":"step_definition_conflict"}');return
                else:
                    result={'decision':'replay' if row[1]=='completed' else 'unknown','step_id':step_id,'result':row[2],'external_key':row[4],'revision':2}
            elif action=='complete':
                state['complete_requests']+=1
                row=db.execute('SELECT token,status,result FROM journal WHERE step=?',(request['step_id'],)).fetchone()
                assert row[0]==request['claim_token']
                if row[1]=='completed':
                    assert row[2]==request['result']
                db.execute('UPDATE journal SET status=?,result=? WHERE step=?',('completed',request['result'],request['step_id']));db.commit()
                if state['lost_completion']:
                    state['lost_completion']=False
                    self.close_connection=True
                    return
                result={'decision':'replay','step_id':request['step_id'],'result':request['result'],'external_key':'stable','revision':2}
            elif action=='unknown':
                db.execute('UPDATE journal SET status=? WHERE step=?',('unknown',request['step_id']));db.commit()
                result={'decision':'unknown','step_id':request['step_id'],'revision':2}
            elif action=='finish':
                state['status']='completed';state['result']=request['result']
                result={'status':'completed','result':request['result']}
            else:
                raise AssertionError(action)
            body=json.dumps(result).encode();self.send_response(200);self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
    class Server(socketserver.UnixStreamServer):
        allow_reuse_address=True
    server=Server(str(path),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    monkeypatch.setenv('NODUS_AGENT_SOCKET',str(path))
    yield db,state
    server.shutdown();server.server_close();thread.join(2);db.close();effects.close();socket_dir.cleanup()


def test_completed_replay_and_lost_completion_do_not_repeat_actual_effect(journal_socket):
    db,state=journal_socket
    calls=[]
    @nodus.step(name='invoice.send',version='1',effect='external')
    def send(invoice):
        key=nodus.step_context().idempotency_key
        state['effects'].execute('INSERT INTO effects(external_key) VALUES (?)',(key,));state['effects'].commit()
        calls.append(invoice)
        return {'receipt':'remote-7'}
    def main(event):
        return send(event['invoice'],_step_id='invoice:42:send')
    state['lost_completion']=True
    first=nodus.agent.resume(main,run_id='cycle-42',version='1')
    # Simulate an older application snapshot with a newer committed step journal.
    state['status']='active';state['result']=None
    second=nodus.agent.resume(main,run_id='cycle-42',version='1')
    assert first==second=={'receipt':'remote-7'}
    assert calls==[42]
    assert state['effects'].execute('SELECT count(*) FROM effects').fetchone()[0]==1
    assert state['complete_requests']==2


def test_unknown_remote_commit_blocks_without_reexecution(journal_socket):
    db,state=journal_socket
    @nodus.step(name='invoice.send',version='1')
    def send(invoice):
        state['effects'].execute('INSERT INTO effects(external_key) VALUES (?)',(nodus.step_context().idempotency_key,));state['effects'].commit()
        raise ConnectionError('response lost after remote commit')
    def main(event):
        return send(event['invoice'],_step_id='invoice:42:send')
    for _ in range(2):
        with pytest.raises(nodus.StepOutcomeUnknown):
            nodus.agent.resume(main,run_id='cycle-42',version='1')
    assert state['effects'].execute('SELECT count(*) FROM effects').fetchone()[0]==1


def test_oversized_result_never_justifies_retry(journal_socket):
    db,state=journal_socket
    calls=[]
    @nodus.step(name='big',version='1',effect='idempotent',dedupe_seconds=3600)
    def big():
        calls.append(1)
        return 'x'*(256<<10)
    def main(event):
        return big(_step_id='big-result')
    with pytest.raises(nodus.StepOutcomeUnknown):
        nodus.agent.resume(main,run_id='cycle-42',version='1')
    assert calls==[1]

@pytest.mark.parametrize('value',[float('nan'),float('inf'),{1:'invalid'},object(),'\ud800'])
def test_payload_rejects_non_json_before_transport(value):
    from nodus._agent import encode
    with pytest.raises(nodus.ValidationError):
        encode(value)


def test_changed_step_definition_refuses_function(journal_socket):
    calls=[]
    @nodus.step(name='stable',version='1')
    def first(value):
        calls.append(value)
        return value
    def main(event):
        return first(1,_step_id='business-id')
    nodus.agent.resume(main,run_id='cycle-42',version='1')
    journal_socket[1]['status']='active'
    @nodus.step(name='stable',version='2')
    def changed(value):
        calls.append(999)
        return value
    def changed_main(event):
        return changed(1,_step_id='business-id')
    with pytest.raises(nodus.StepDefinitionConflict):
        nodus.agent.resume(changed_main,name='main',run_id='cycle-42',version='1')
    assert calls==[1]


def test_run_registration_and_resolution_use_customer_identity():
    import httpx
    requests=[]
    encoded=base64.b64encode(b'{"invoice":42}').decode()
    run={'run_id':'cycle:42','name':'main','version':'1','image_digest':'sha256:'+'a'*64,'status':'active','epoch':0,'input':encoded}
    def handler(request):
        requests.append(request)
        if request.url.path.endswith('/steps'):return httpx.Response(200,json={'steps':[]})
        if request.url.path.endswith('/resolve'):return httpx.Response(200,json={'decision':'unknown','step_id':'send','revision':2,'external_key':'stable'})
        return httpx.Response(201 if request.method=='POST' else 200,json=run)
    client=nodus.Client(api_key='nk_live_test',base_url='https://nodus.invalid')
    client._http=httpx.Client(base_url='https://nodus.invalid',transport=httpx.MockTransport(handler))
    try:
        runs=nodus.Sandbox(client,'sb_test').agent_runs
        created=runs.create(run_id='cycle:42',name='main',version='1',image_digest=run['image_digest'],input={'invoice':42})
        assert created.input=={'invoice':42}
        assert runs.get('cycle:42').run_id=='cycle:42'
        assert runs.steps('cycle:42')=={'steps':[]}
        runs.resolve('cycle:42',step_id='send',expected_revision=2,decision='no_effect',reason='Verified independent receipt absence',evidence_digest='sha256:'+'b'*64)
        assert json.loads(requests[0].content)['input']==encoded
        assert json.loads(requests[-1].content)['expected_revision']==2
        assert all('/agent-runs' in r.url.path for r in requests)
    finally: client.close()

@pytest.mark.asyncio
async def test_async_account_run_registration_and_read():
    import httpx
    run={'run_id':'run','name':'main','version':'1','image_digest':'sha256:'+'a'*64,'status':'active','epoch':0,'input':base64.b64encode(b'null').decode()}
    client=nodus.AsyncClient(api_key='nk_live_test',base_url='https://nodus.invalid')
    client._http=httpx.AsyncClient(base_url='https://nodus.invalid',transport=httpx.MockTransport(lambda r:httpx.Response(200,json=run)))
    try:
        runs=(await client.sandboxes.from_id('sb_test')).agent_runs
        assert (await runs.create(run_id='run',name='main',version='1',image_digest=run['image_digest'],input=None)).run_id=='run'
        assert (await runs.get('run')).input is None
    finally: await client.aclose()
