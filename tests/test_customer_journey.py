"""The customer path end to end, over real HTTP, through the public surface only.

Configure, submit, poll, read logs, cancel — the five things a researcher does
in their first hour — driven with ``nodus.Client`` and the workload handle it
returns. Nothing here reaches into ``_request``, ``_brief`` or ``_http``: if a
step needs a private helper, a customer cannot do it.

Where it runs:

* against a **real control plane** when ``NODUS_E2E_BASE_URL`` and
  ``NODUS_E2E_API_KEY`` are set, e.g. the throwaway one from
  ``make build && ./bin/demo -serve`` (key ``nodus_dev_key_phase1``);
* otherwise against the double below, which serves the byte shapes recorded
  from that real plane — ``store.SubmitResult``, ``models.Workload`` plus
  ``store.Meter``, ``models.WorkloadEvent``, and the error bodies
  ``internal/api/server.go:writeErr`` produces. It is a stand-in for the
  server, never for the SDK: every request still crosses a socket.

The double exists because the real plane needs Go, Postgres and a free port,
none of which the SDK's own test job has. It is worth having anyway — it is the
only thing that fails when the SDK stops speaking the published contract.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest

import nodus


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass
class _State:
    """What the double remembers. One tenant, because one key is issued."""

    api_key: str
    workloads: dict[str, dict[str, Any]] = field(default_factory=dict)
    events: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    by_idempotency_key: dict[str, str] = field(default_factory=dict)
    seen_idempotency_keys: list[str] = field(default_factory=list)
    seen_user_agents: list[str] = field(default_factory=list)
    seq: int = 0

    def next_seq(self) -> int:
        self.seq += 1
        return self.seq

    def record_event(self, workload_id: str, event_type: str) -> None:
        self.events.setdefault(workload_id, []).append(
            {
                "id": self.next_seq(),
                "event_id": f"ev_{self.seq:08d}",
                "event_type": event_type,
                "payload": {},
                "created_at": _now(),
            }
        )


class _Handler(BaseHTTPRequestHandler):
    """The routes of internal/api that a customer's first hour touches."""

    protocol_version = "HTTP/1.1"

    @property
    def state(self) -> _State:
        return self.server.state  # type: ignore[attr-defined]

    def log_message(self, *_: Any) -> None:
        """Silent: pytest output is for failures, not for a request log."""

    # -- plumbing ----------------------------------------------------------

    def _send(self, code: int, body: Any, content_type: str = "application/json") -> None:
        raw = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _error(self, code: int, err: str, message: str) -> None:
        self._send(code, {"error": err, "message": message})

    def _authenticated(self) -> bool:
        if self.headers.get("Authorization") == f"Bearer {self.state.api_key}":
            self.state.seen_user_agents.append(self.headers.get("User-Agent", ""))
            return True
        self._error(401, "unauthorized", "invalid api key")
        return False

    def _summary(self, wl: dict[str, Any]) -> dict[str, Any]:
        """A list row: no payload, no route. The read endpoint carries those."""
        return {
            "id": wl["id"],
            "status": wl["status"],
            "spend_usd": wl["spend_usd"],
            "revision": wl["revision"],
            "created_at": wl["created_at"],
            "updated_at": wl["updated_at"],
            "meter": wl["meter"],
        }

    # -- routes ------------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's spelling
        url = urlparse(self.path)
        path = url.path
        if path == "/healthz":
            return self._send(200, {"status": "ok"})
        if path == "/readyz":
            return self._send(200, {"status": "ready"})
        if not path.startswith("/v1/"):
            return self._error(404, "not_found", "no such route")
        if not self._authenticated():
            return None

        if path == "/v1/workloads":
            rows = [self._summary(w) for w in reversed(list(self.state.workloads.values()))]
            return self._send(200, {"workloads": rows})

        parts = path.split("/")
        # /v1/workloads/{id}[/{sub}]
        if len(parts) < 4 or parts[2] != "workloads":
            return self._error(404, "not_found", "no such route")
        wl = self.state.workloads.get(parts[3])
        if wl is None:
            return self._error(404, "not_found", "workload not found")
        sub = parts[4] if len(parts) > 4 else ""

        if sub == "":
            return self._send(200, wl)
        if sub == "events":
            after = int((parse_qs(url.query).get("after") or ["0"])[0])
            rows = [e for e in self.state.events.get(wl["id"], []) if e["id"] > after]
            return self._send(200, {"events": rows})
        if sub == "artifacts":
            return self._send(200, {"artifacts": None})
        if sub == "logs":
            # Nothing runs on the double, so nothing ever commits a log. This is
            # the same 404 a real workload answers with until its first manifest.
            return self._error(404, "not_found", "no log recorded for this workload yet")
        return self._error(404, "not_found", "no such route")

    def do_POST(self) -> None:  # noqa: N802
        url = urlparse(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        if not self._authenticated():
            return None
        idem = self.headers.get("Idempotency-Key") or ""
        self.state.seen_idempotency_keys.append(idem)
        if not idem:
            return self._error(400, "missing_idempotency_key", "Idempotency-Key required")

        if url.path == "/v1/workloads":
            return self._submit(idem, raw)

        parts = url.path.split("/")
        if len(parts) == 5 and parts[2] == "workloads" and parts[4] == "cancel":
            wl = self.state.workloads.get(parts[3])
            if wl is None:
                return self._error(404, "not_found", "workload not found")
            wl["status"] = "cancelled"
            wl["updated_at"] = _now()
            self.state.record_event(wl["id"], "workload.cancelled")
            return self._send(202, {"status": "cancel_requested"})
        return self._error(404, "not_found", "no such route")

    def _submit(self, idem: str, raw: bytes) -> None:
        try:
            payload = json.loads(raw or b"{}")
        except ValueError as exc:
            return self._error(400, "invalid_json", str(exc))
        source = payload.get("source") or {}
        if not source and not payload.get("stages") and not payload.get("framework"):
            return self._error(400, "missing_source", "source, framework, or stages required")

        replayed = self.state.by_idempotency_key.get(idem)
        if replayed:
            wl = self.state.workloads[replayed]
            return self._send(
                202,
                {
                    "workload_id": wl["id"],
                    "id": wl["id"],
                    "status": wl["status"],
                    "revision": wl["revision"],
                },
            )

        wid = f"wl_{len(self.state.workloads) + 1:08d}-0000-4000-8000-000000000000"
        stamp = _now()
        self.state.workloads[wid] = {
            "id": wid,
            "tenant_id": "ten_dev",
            "status": "accepted",
            "revision": 1,
            "spend_usd": 0,
            "created_at": stamp,
            "updated_at": stamp,
            "payload": payload,
            "stages": [
                {
                    "id": "main",
                    "status": "pending",
                    "continuity_mode": (payload.get("continuity") or {}).get("mode", ""),
                    "completed_units": 0,
                    "total_units": 0,
                }
            ],
            "meter": {
                "settled_usd": 0,
                "accruing_usd": 0,
                "accruing_rate_usd_hour": 0,
                "total_now_usd": 0,
                "as_of": stamp,
            },
        }
        self.state.by_idempotency_key[idem] = wid
        self.state.record_event(wid, "workload.accepted")
        self._send(202, {"workload_id": wid, "id": wid, "status": "accepted", "revision": 1})


@dataclass
class Plane:
    base_url: str
    api_key: str
    kind: str
    state: _State | None = None


@pytest.fixture(scope="module")
def plane():
    """A control plane to run the journey against, real if one was named."""
    live_url = os.environ.get("NODUS_E2E_BASE_URL", "").strip()
    live_key = os.environ.get("NODUS_E2E_API_KEY", "").strip()
    if live_url and live_key:
        yield Plane(base_url=live_url, api_key=live_key, kind="live")
        return

    state = _State(api_key="nodus_dev_key_phase1")
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    server.state = state  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    try:
        yield Plane(f"http://{host}:{port}", state.api_key, kind="double", state=state)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture
def client(plane):
    with nodus.Client(api_key=plane.api_key, base_url=plane.base_url) as c:
        yield c


def test_an_unconfigured_client_never_reaches_the_network(monkeypatch):
    """Step zero. Nothing is sent, so the message has to carry the whole fix."""
    monkeypatch.delenv("NODUS_BASE_URL", raising=False)
    monkeypatch.delenv("NODUS_API_KEY", raising=False)
    with pytest.raises(nodus.ConfigurationError) as exc:
        nodus.Client()
    message = str(exc.value)
    assert "NODUS_BASE_URL" in message
    assert "NODUS_API_KEY" in message
    assert "https://nodus.run/console/" in message


def test_the_environment_alone_is_enough_to_build_a_client(plane, monkeypatch):
    """What the console's quickstart does: two exports, then ``nodus.Client()``."""
    monkeypatch.setenv("NODUS_BASE_URL", plane.base_url)
    monkeypatch.setenv("NODUS_API_KEY", plane.api_key)
    with nodus.Client() as c:
        assert c.healthz() == {"status": "ok"}


def test_a_wrong_key_is_refused_and_says_so(plane):
    with nodus.Client(api_key="nk_live_not_a_real_key", base_url=plane.base_url) as c:
        with pytest.raises(nodus.AuthenticationError) as exc:
            c.list(limit=1)
    assert exc.value.status_code == 401


def test_the_whole_journey(client, plane):
    """Submit, find it, watch it, ask for logs, cancel. One customer's hour."""
    workload = client.run(
        command=["sh", "-c", "for i in 1 2 3; do echo step-$i; sleep 3; done"],
        model="7B fine-tune",
        peak_memory_gb=24,
        expected_runtime_hours=0.1,
        budget=25,
    )

    # Submitted and identified. The id is what every later call is about.
    assert workload.id.startswith("wl_")
    assert not workload.is_terminal

    # Read back: the budget the brief asked for comes back on the workload, so a
    # customer can see the ceiling their run is being held to.
    fetched = client.get(workload.id)
    assert fetched.id == workload.id
    assert fetched.budget_usd == 25.0

    # It appears in their own list.
    assert workload.id in [w.id for w in client.list(limit=50)]

    # "Is it running?" is answered by events, in order, from the first one.
    events = workload.events()
    assert [e.type for e in events][:1] == ["workload.accepted"]
    assert events[0].seq >= 1

    # Logs before anything has been committed: a clear refusal, not an empty
    # string that reads like a program which printed nothing.
    with pytest.raises(nodus.NotFoundError) as exc:
        client.logs(workload.id)
    assert "log" in str(exc.value).lower()

    # Cancelling is a customer action, and it sticks.
    workload.cancel()
    done = workload.wait(poll_seconds=0.2, timeout_seconds=60)
    assert done.is_terminal
    assert nodus.WorkloadStatus.coerce(done.status) == nodus.WorkloadStatus.CANCELLED
    assert "workload.cancelled" in [e.type for e in workload.events()]


def test_every_submission_carried_an_idempotency_key(plane):
    """A retried submit must not become a second paid workload.

    Asserted on the double only: it is the only side that can see the headers a
    live plane merely required.
    """
    if plane.kind != "double":
        pytest.skip("header inspection needs the double")
    assert plane.state is not None
    assert plane.state.seen_idempotency_keys
    assert all(plane.state.seen_idempotency_keys)
    assert any(ua.startswith("nodus-python/") for ua in plane.state.seen_user_agents)
