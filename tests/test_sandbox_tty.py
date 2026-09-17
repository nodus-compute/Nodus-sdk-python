import json
import os
import pytest
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


@pytest.mark.skipif(os.name != "posix", reason="Physical shell requires an interactive POSIX terminal")
def test_shell_restores_real_terminal_and_cancels_disconnect(monkeypatch):
    import os
    import pty
    import signal
    import struct
    import termios
    import fcntl
    import threading
    import sys
    from nodus._shell import shell
    master, slave = pty.openpty()
    fcntl.ioctl(slave,termios.TIOCSWINSZ,struct.pack('HHHH',24,80,0,0))
    original=termios.tcgetattr(slave)
    terminal=os.fdopen(os.dup(slave),'r+b',buffering=0)
    ready=threading.Event()
    seen=[]
    def respond(request):
        body=json.loads(request.content) if request.content else {}
        seen.append((request.url.path,body))
        if request.url.path.endswith('/resize'):
            ready.set()
            return httpx.Response(202,json={'rows':24,'cols':80,'version':1})
        if request.url.path.endswith('/stream'):
            return httpx.Response(200,json={'frames':[],'next_sequence':0,'done':False})
        return httpx.Response(202,json={'id':'ex_test','sandbox_id':'sb_test','state':'queued'})
    def disconnect():
        assert ready.wait(2)
        os.write(master,b'\x1d')
    writer=threading.Thread(target=disconnect)
    writer.start()
    try:
        monkeypatch.setattr(sys,'stdin',terminal)
        monkeypatch.setattr(sys,'stdout',terminal)
        with Client(api_key='test-key',base_url='https://api.example.com') as client:
            client._http.close()
            client._http=httpx.Client(base_url='https://api.example.com',transport=httpx.MockTransport(respond))
            assert shell(Sandbox(client,'sb_test'))==130
        restored=termios.tcgetattr(slave)
        restored[3] &= ~getattr(termios,"PENDIN",0)
        original[3] &= ~getattr(termios,"PENDIN",0)
        assert restored==original
        assert seen[-1][0].endswith('/cancel')
    finally:
        writer.join(2)
        terminal.close()
        os.close(master)
        os.close(slave)
