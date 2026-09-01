"""Waiting survives a bad patch of network; it does not survive a bad key.

A wait is the longest-lived thing in the SDK. An 18-hour run polled every two
seconds is tens of thousands of requests, so a transient failure somewhere in
them is ordinary, not exceptional — and the workload keeps running and keeps
billing whatever the client believes. A wait that ends on one 503 hands the
customer an exception for a run that is still spending their money.

The other half is the opposite duty: a revoked key or a deleted workload will
never come good, so waiting on one is a program that hangs instead of failing.

Everything here is asserted over real HTTP against a scripted local server. A
mock's call count would prove the loop calls something; only a socket proves
what the loop does with what comes back.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse

import pytest

import nodus

WID = "wl_00000001-0000-4000-8000-000000000000"

RUNNING = {"id": WID, "status": "running", "revision": 1}
DONE = {"id": WID, "status": "completed", "spend_usd": 12.5, "revision": 1}

ACCEPTED_EVENT = {
    "id": 1,
    "event_id": "ev_00000001",
    "event_type": "workload.accepted",
    "payload": {},
    "created_at": "2026-09-01T10:00:00Z",
}

# Hang up mid-request instead of answering: the shape a network hiccup has on
# the wire, which no status code can stand in for.
HANGUP = object()


@dataclass
class _Script:
    """Responses to serve in order; the last one is served from then on."""

    workload: list[Any] = field(default_factory=lambda: [(200, DONE)])
    events: list[Any] = field(default_factory=lambda: [(200, {"events": []})])
    seen: list[str] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def take(self, key: str) -> Any:
        with self._lock:
            self.seen.append(key)
            plan = getattr(self, key)
            return plan.pop(0) if len(plan) > 1 else plan[0]


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_: Any) -> None:
        """Silent: pytest output is for failures, not for a request log."""

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's spelling
        path = urlparse(self.path).path
        step = self.server.script.take("events" if path.endswith("/events") else "workload")
        if step is HANGUP:
            self.close_connection = True
            return
        status, body = step
        raw = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


@dataclass
class Plane:
    base_url: str
    script: _Script


@pytest.fixture
def plane():
    script = _Script()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    server.script = script  # type: ignore[attr-defined]
    # A short poll interval so shutdown is prompt: this fixture is per-test, and
    # the default half-second would be most of what the file costs to run.
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    try:
        yield Plane(f"http://{host}:{port}", script)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def client(plane: Plane) -> nodus.Client:
    c = nodus.Client(api_key="nodus_dev_key_phase1", base_url=plane.base_url, timeout=5.0)
    # The per-request backoff is not what these tests are about, and its real
    # values would put seconds of sleeping into the suite.
    c._backoff = lambda *a, **k: 0  # type: ignore[method-assign]
    return c


def async_client(plane: Plane) -> nodus.AsyncClient:
    c = nodus.AsyncClient(api_key="nodus_dev_key_phase1", base_url=plane.base_url, timeout=5.0)
    c._backoff = lambda *a, **k: 0  # type: ignore[method-assign]
    return c


# More failures than one request's own retry budget (max_retries=2, so three
# attempts) can absorb, so surviving this is the wait's doing and not the
# transport's. Every kind the docs call transient is represented.
A_BAD_PATCH = [
    (503, {"error": "no_capacity"}),
    (503, {"error": "no_capacity"}),
    (503, {"error": "no_capacity"}),
    (429, {"error": "rate_limited"}),
    HANGUP,
    (500, {"error": "internal"}),
]


# -- a wait outlives a bad patch of network --------------------------------


def test_a_transient_burst_does_not_end_the_wait(plane):
    """The failure this exists for: one 503 must not end an 18-hour wait."""
    plane.script.workload = [*A_BAD_PATCH, (200, DONE)]
    with client(plane) as c:
        done = c.wait(WID, poll_seconds=0.01)
    assert done.status is nodus.WorkloadStatus.COMPLETED
    assert len(plane.script.seen) > len(A_BAD_PATCH), "the wait gave up inside the burst"


def test_a_transient_burst_does_not_end_a_handle_wait(plane):
    """``workload.wait()`` is the documented spelling, so it carries the same duty."""
    plane.script.workload = [(200, RUNNING), *A_BAD_PATCH, (200, DONE)]
    with client(plane) as c:
        wl = c.get(WID)
        assert wl.wait(poll_seconds=0.01) is wl
    assert wl.succeeded


def test_a_transient_burst_does_not_end_an_async_wait(plane):
    """The async half cannot drift from the sync half; same script, same outcome."""
    plane.script.workload = [(200, RUNNING), *A_BAD_PATCH, (200, DONE)]

    async def go():
        async with async_client(plane) as c:
            wl = await c.get(WID)
            await wl.wait(poll_seconds=0.01)
            return wl

    assert asyncio.run(go()).succeeded


def test_the_callers_own_bound_still_ends_a_wait_that_only_ever_fails(plane):
    """Retrying forever is the other way to hang. ``timeout_seconds`` still rules."""
    plane.script.workload = [(503, {"error": "no_capacity"})]
    with client(plane) as c:
        started = time.monotonic()
        with pytest.raises(nodus.APITimeoutError):
            c.wait(WID, poll_seconds=0.01, timeout_seconds=0.3)
    assert time.monotonic() - started < 5.0, "backoff slept past the caller's bound"


# -- a wait does not outlive a permanent failure ---------------------------


def test_a_revoked_key_ends_the_wait_at_once(plane):
    """A key that was revoked mid-run never comes good; spinning on it hides that."""
    plane.script.workload = [(200, RUNNING), (401, {"error": "unauthorized"})]
    with client(plane) as c:
        started = time.monotonic()
        with pytest.raises(nodus.AuthenticationError):
            c.wait(WID, poll_seconds=0.01)
    assert time.monotonic() - started < 5.0
    assert len(plane.script.seen) <= 3, plane.script.seen


def test_an_unknown_workload_ends_the_wait_at_once(plane):
    plane.script.workload = [(200, RUNNING), (404, {"error": "not_found"})]
    with client(plane) as c:
        with pytest.raises(nodus.NotFoundError):
            c.wait(WID, poll_seconds=0.01)


def test_a_revoked_key_ends_an_async_wait_at_once(plane):
    plane.script.workload = [(200, RUNNING), (401, {"error": "unauthorized"})]

    async def go():
        async with async_client(plane) as c:
            wl = await c.get(WID)
            await wl.wait(poll_seconds=0.01)

    with pytest.raises(nodus.AuthenticationError):
        asyncio.run(go())


# -- streaming events is a wait too ----------------------------------------


def test_a_transient_burst_does_not_end_the_event_stream(plane):
    """``nodus events --follow`` runs as long as the workload does."""
    plane.script.events = [*A_BAD_PATCH, (200, {"events": [ACCEPTED_EVENT]}), (200, {"events": []})]
    plane.script.workload = [(200, RUNNING), (200, DONE)]
    with client(plane) as c:
        assert [e.type for e in c.stream_events(WID, poll_seconds=0.01)] == ["workload.accepted"]


def test_a_transient_burst_does_not_end_an_async_event_stream(plane):
    plane.script.events = [*A_BAD_PATCH, (200, {"events": [ACCEPTED_EVENT]}), (200, {"events": []})]
    plane.script.workload = [(200, RUNNING), (200, DONE)]

    async def go():
        async with async_client(plane) as c:
            wl = await c.get(WID)
            return [e.type async for e in wl.stream_events(poll_seconds=0.01)]

    plane.script.workload = [(200, RUNNING), (200, RUNNING), (200, DONE)]
    assert asyncio.run(go()) == ["workload.accepted"]


def test_a_revoked_key_ends_the_event_stream_at_once(plane):
    plane.script.events = [(401, {"error": "unauthorized"})]
    with client(plane) as c:
        with pytest.raises(nodus.AuthenticationError):
            list(c.stream_events(WID, poll_seconds=0.01))
