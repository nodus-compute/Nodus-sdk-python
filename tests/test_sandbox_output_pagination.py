"""Completed executions can retain several pages of binary terminal output."""
import asyncio
import base64

import httpx
import pytest

from nodus import AsyncClient, Client
from nodus._sandboxes import AsyncSandboxExec, SandboxExec


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


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("state", ["running", "completed"])
def test_nonfollowing_output_drains_initial_available_frames(asynchronous, state):
    chunks = [f"frame-{sequence}\n".encode() for sequence in range(1, 19)]
    cursors = []

    def respond(request):
        assert request.method == "GET"
        assert request.url.path == "/v1/sandboxes/sb_test/execs/ex_test/stream"
        assert request.url.params["wait"] == "false"
        after, limit = int(request.url.params["after"]), int(request.url.params["limit"])
        assert len(cursors) < 2, "nonfollowing reads must not chase new output"
        cursors.append(after)
        last = 18 if state == "running" and after else 17
        end = min(after + limit, last)
        done = state == "completed" and end == last
        return httpx.Response(200, json={
            "frames": [{"sequence": index + 1, "stream": "stdout",
                        "offset": sum(map(len, chunks[:index])),
                        "data": base64.b64encode(chunks[index]).decode("ascii")}
                       for index in range(after, end)],
            "next_sequence": end, "last_sequence": last,
            "final_sequence": last if state == "completed" else None,
            "state": state, "done": done, "complete": done,
        })

    async def collect():
        async with AsyncClient(api_key="test-key", base_url="https://api.example.com") as client:
            await client._http.aclose()
            client._http = httpx.AsyncClient(base_url="https://api.example.com", transport=httpx.MockTransport(respond))
            return [frame.data async for frame in AsyncSandboxExec(client, "sb_test", "ex_test").iter_output(follow=False)]

    if asynchronous:
        transcript = asyncio.run(collect())
    else:
        with Client(api_key="test-key", base_url="https://api.example.com") as client:
            client._http.close()
            client._http = httpx.Client(base_url="https://api.example.com", transport=httpx.MockTransport(respond))
            transcript = [frame.data for frame in SandboxExec(client, "sb_test", "ex_test").iter_output(follow=False)]
    assert transcript == chunks[:17]
    assert cursors == [0, 16]


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
