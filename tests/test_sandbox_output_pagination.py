"""Completed executions can retain several pages of binary terminal output."""
import asyncio
import base64
import os

import httpx
import pytest

from nodus import AsyncClient, Client
from nodus._sandboxes import AsyncSandboxExec, Sandbox, SandboxExec


CHUNKS = [b"\x1b[31mfirst\xff\x00", b"\r\nsecond\x80", b"\x1b[0mfinal\xfe\n"]


def completed_page(after):
    assert 0 <= after < len(CHUNKS), "unexpected output cursor"
    sequence = after + 1
    return {
        "frames": [{"sequence": sequence, "stream": "stdout", "offset": 0,
                    "data": base64.b64encode(CHUNKS[after]).decode("ascii")}],
        "next_sequence": sequence, "last_sequence": len(CHUNKS),
        "final_sequence": len(CHUNKS), "state": "completed", "done": True,
        "complete": sequence == len(CHUNKS),
    }


@pytest.mark.parametrize("asynchronous", [False, True])
def test_output_iterator_drains_all_completed_pages(asynchronous):
    cursors = []

    def respond(request):
        assert request.url.path.endswith("/stream")
        after = int(request.url.params["after"])
        cursors.append(after)
        return httpx.Response(200, json=completed_page(after))

    async def collect_async():
        async with AsyncClient(api_key="test-key", base_url="https://api.example.com") as client:
            await client._http.aclose()
            client._http = httpx.AsyncClient(base_url="https://api.example.com", transport=httpx.MockTransport(respond))
            execution = AsyncSandboxExec(client, "sb_test", "ex_test")
            return [frame.data async for frame in execution.iter_output()]

    if asynchronous:
        transcript = asyncio.run(collect_async())
    else:
        with Client(api_key="test-key", base_url="https://api.example.com") as client:
            client._http.close()
            client._http = httpx.Client(base_url="https://api.example.com", transport=httpx.MockTransport(respond))
            transcript = [frame.data for frame in SandboxExec(client, "sb_test", "ex_test").iter_output()]
    assert transcript == CHUNKS
    assert cursors == [0, 1, 2]


@pytest.mark.skipif(os.name != "posix", reason="Physical shell requires a POSIX terminal")
@pytest.mark.parametrize("stalled", [False, True])
def test_shell_drains_binary_final_pages_and_restores_terminal(monkeypatch, stalled):
    import fcntl
    import pty
    import select
    import signal
    import struct
    import sys
    import termios
    import threading
    from nodus._shell import shell

    master, slave = pty.openpty()
    terminal = os.fdopen(os.dup(slave), "r+b", buffering=0)
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 80, 0, 0))
    original = termios.tcgetattr(slave)
    signals = {number: signal.getsignal(number) for number in (signal.SIGWINCH, signal.SIGHUP, signal.SIGTERM)}
    cursors, actions = [], []

    def respond(request):
        actions.append(request.url.path)
        if request.url.path.endswith("/stream"):
            after = int(request.url.params["after"])
            cursors.append(after)
            page = completed_page(after)
            if stalled and after == 2:
                page.update(frames=[], next_sequence=after, complete=False)
            return httpx.Response(200, json=page)
        if request.url.path.endswith("/resize"):
            return httpx.Response(202, json={"rows": 24, "cols": 80, "version": 1})
        return httpx.Response(200, json={"id": "ex_test", "sandbox_id": "sb_test",
                                        "state": "completed", "exit_code": 7})

    transcript = bytearray()
    finished = threading.Event()

    def collect_terminal():
        while True:
            if select.select([master], [], [], 0.05)[0]:
                transcript.extend(os.read(master, 4096))
            elif finished.is_set():
                return

    reader = threading.Thread(target=collect_terminal)
    reader.start()
    real_write = os.write

    def short_write(fd, data):
        return real_write(fd, data[:3])

    try:
        monkeypatch.setattr(sys, "stdin", terminal)
        monkeypatch.setattr(sys, "stdout", terminal)
        monkeypatch.setattr(os, "write", short_write)
        with Client(api_key="test-key", base_url="https://api.example.com") as client:
            client._http.close()
            client._http = httpx.Client(base_url="https://api.example.com", transport=httpx.MockTransport(respond))
            if stalled:
                from nodus import NodusError
                with pytest.raises(NodusError, match="final output.*unavailable"):
                    shell(Sandbox(client, "sb_test"))
            else:
                assert shell(Sandbox(client, "sb_test")) == 7
        finished.set()
        reader.join(2)
        assert not reader.is_alive()
        assert bytes(transcript) == b"".join(CHUNKS[:2] if stalled else CHUNKS)
        assert cursors == [0, 1, 2]
        assert not any(path.endswith("/cancel") for path in actions)
        restored = termios.tcgetattr(slave)
        restored[3] &= ~getattr(termios, "PENDIN", 0)
        original[3] &= ~getattr(termios, "PENDIN", 0)
        assert restored == original
        assert {number: signal.getsignal(number) for number in signals} == signals
    finally:
        finished.set()
        reader.join(2)
        terminal.close()
        os.close(master)
        os.close(slave)


@pytest.mark.parametrize("asynchronous", [False, True])
def test_terminal_incomplete_output_refuses_nonadvancing_page(asynchronous):
    from nodus import NodusError
    calls = []
    def respond(request):
        calls.append(int(request.url.params["after"]))
        assert len(calls) == 1, "terminal incomplete page must not be polled forever"
        return httpx.Response(200, json={"frames": [], "next_sequence": 7,
            "last_sequence": 8, "final_sequence": 8, "state": "completed",
            "done": True, "complete": False})
    async def collect():
        async with AsyncClient(api_key="test-key", base_url="https://api.example.com") as client:
            await client._http.aclose()
            client._http = httpx.AsyncClient(base_url="https://api.example.com", transport=httpx.MockTransport(respond))
            return [frame async for frame in AsyncSandboxExec(client, "sb_test", "ex_test").iter_output(after=7)]
    with pytest.raises(NodusError, match="final output.*unavailable"):
        if asynchronous:
            asyncio.run(collect())
        else:
            with Client(api_key="test-key", base_url="https://api.example.com") as client:
                client._http.close()
                client._http = httpx.Client(base_url="https://api.example.com", transport=httpx.MockTransport(respond))
                list(SandboxExec(client, "sb_test", "ex_test").iter_output(after=7))
    assert calls == [7]
