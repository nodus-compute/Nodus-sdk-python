"""Durable sandbox handles for interactive agent execution."""

from __future__ import annotations

import asyncio
import base64
import binascii
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
import math
import re
import time
from typing import Any, AsyncIterator, Iterator
import uuid

from ._secrets import _names
from .errors import APITimeoutError, NodusError, ValidationError
from .types import Event, _dt, _int, _num, _obj, _rows, _text

__all__ = [
    "SandboxState",
    "SandboxExecState",
    "SandboxOutputFrame",
    "SandboxOutputPage",
    "SandboxInputReceipt",
    "Sandboxes",
    "Sandbox",
    "SandboxExec",
    "AsyncSandboxes",
    "AsyncSandbox",
    "AsyncSandboxExec",
]


class _OpenWireEnum(str, Enum):
    @classmethod
    def coerce(cls, value: Any) -> Any:
        if value is None or isinstance(value, cls):
            return value
        try:
            return cls(str(value))
        except ValueError:
            return str(value)


class SandboxState(_OpenWireEnum):
    CREATING = "creating"
    READY = "ready"
    RUNNING = "running"
    IDLE = "idle"
    SUSPENDED = "suspended"
    RESUMING = "resuming"
    RECOVERING = "recovering"
    TERMINATED = "terminated"
    FAILED = "failed"


class SandboxExecState(_OpenWireEnum):
    QUEUED = "queued"
    DISPATCHING = "dispatching"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    LOST = "lost"


SANDBOX_TERMINAL = frozenset({"terminated", "failed"})
EXEC_TERMINAL = frozenset({"completed", "failed", "cancelled", "lost"})
_RESOURCE_ID = re.compile(r"\A[A-Za-z0-9_-]+\Z")


def _valid_id(value: Any, kind: str) -> str:
    if isinstance(value, str) and _RESOURCE_ID.match(value):
        return value
    raise ValidationError(
        f"{value!r} is not a {kind} id. Use letters, digits, underscores, and hyphens."
    )


def _wire(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _wire(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_wire(item) for item in value]
    return value


def _command(value: str | list[str] | tuple[str, ...]) -> list[str]:
    if isinstance(value, str):
        if not value or "\x00" in value:
            raise ValidationError("command must be nonempty text without NUL characters")
        return ["/bin/sh", "-lc", value]
    if not isinstance(value, (list, tuple)) or not value:
        raise ValidationError("command must be a nonempty argv list")
    if any(not isinstance(arg, str) or "\x00" in arg for arg in value):
        raise ValidationError("every command argument must be text without NUL characters")
    return list(value)


def _create_payload(
    *,
    image: str | None,
    name: str | None = None,
    budget: float | None = None,
    wake: str | None = None,
    requirements: dict[str, Any] | None = None,
    outcome: dict[str, Any] | None = None,
    policy: dict[str, Any] | None = None,
    lifecycle: dict[str, Any] | None = None,
    reservation: dict[str, Any] | None = None,
    continuity: dict[str, Any] | None = None,
    from_snapshot: str | None = None,
    secrets: list[str] | None = None,
) -> dict[str, Any]:
    if image is not None and (not isinstance(image, str) or not image.strip()):
        raise ValidationError("image must be nonempty text")
    if image is None and not name:
        raise ValidationError("image or name is required")
    body: dict[str, Any] = {}
    if image is not None:
        body["image"] = image
    selected_requirements = dict(requirements or {})
    selected_requirements.setdefault("compute_class", "accelerator")
    for key, value in (
        ("name", name),
        ("wake", wake),
        ("requirements", selected_requirements),
        ("policy", policy),
        ("lifecycle", lifecycle),
        ("reservation", reservation),
        ("continuity", continuity),
        ("from_snapshot", from_snapshot),
        ("secrets", _names(secrets)),
    ):
        if value is not None:
            body[key] = _wire(value)
    final_outcome = dict(outcome or {})
    if budget is not None:
        if "max_cost_usd" in final_outcome and final_outcome["max_cost_usd"] != budget:
            raise ValidationError("budget conflicts with outcome.max_cost_usd")
        final_outcome["max_cost_usd"] = budget
    if final_outcome:
        body["outcome"] = _wire(final_outcome)
    return body


def _exec_payload(
    command: str | list[str] | tuple[str, ...],
    *,
    cwd: str | None,
    env: dict[str, str] | None,
    timeout_seconds: int | None,
    stdin: bool,
) -> dict[str, Any]:
    body: dict[str, Any] = {"command": _command(command)}
    if cwd is not None:
        body["cwd"] = cwd
    if env is not None:
        body["env"] = dict(env)
    if timeout_seconds is not None:
        body["timeout_s"] = timeout_seconds
    if stdin:
        body["stdin"] = True
    return body


@dataclass(frozen=True)
class SandboxOutputFrame:
    sequence: int
    stream: str
    offset: int
    data: bytes
    created_at: datetime | None = None

    @property
    def text(self) -> str:
        return self.data.decode("utf-8", "replace")

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "SandboxOutputFrame":
        encoded = value.get("data")
        try:
            data = base64.b64decode(encoded, validate=True) if isinstance(encoded, str) else b""
        except (ValueError, binascii.Error):
            data = b""
        return cls(
            sequence=_int(value.get("sequence")),
            stream=_text(value.get("stream")),
            offset=_int(value.get("offset")),
            data=data,
            created_at=_dt(value.get("created_at")),
        )


@dataclass(frozen=True)
class SandboxOutputPage:
    frames: list[SandboxOutputFrame]
    next_sequence: int
    last_sequence: int
    final_sequence: int | None
    state: Any
    done: bool
    complete: bool

    @classmethod
    def from_dict(cls, value: dict[str, Any] | None) -> "SandboxOutputPage":
        body = _obj(value)
        final = body.get("final_sequence")
        return cls(
            frames=[SandboxOutputFrame.from_dict(_obj(row)) for row in _rows(body.get("frames"))],
            next_sequence=_int(body.get("next_sequence")),
            last_sequence=_int(body.get("last_sequence")),
            final_sequence=_int(final) if final is not None else None,
            state=SandboxExecState.coerce(body.get("state")),
            done=bool(body.get("done")),
            complete=bool(body.get("complete")),
        )


@dataclass(frozen=True)
class SandboxInputReceipt:
    sequence: int
    bytes: int
    eof: bool
    created_at: datetime | None

    @classmethod
    def from_dict(cls, value: dict[str, Any] | None) -> "SandboxInputReceipt":
        body = _obj(value)
        return cls(
            sequence=_int(body.get("sequence")),
            bytes=_int(body.get("bytes")),
            eof=bool(body.get("eof")),
            created_at=_dt(body.get("created_at")),
        )


class _SandboxState:
    network_usage: dict[str, int] | None
    failure: dict[str, Any] | None
    id: str
    state: Any
    envelope: dict[str, Any]
    cost_usd: float
    url: str
    created_at: datetime | None
    updated_at: datetime | None
    last_activity_at: datetime | None
    terminal_at: datetime | None
    replayed: bool

    def _init_state(self, sandbox_id: str = "") -> None:
        self.id = sandbox_id
        self.state = None
        self.envelope = {}
        self.cost_usd = 0.0
        self.url = ""
        self.created_at = None
        self.updated_at = None
        self.last_activity_at = None
        self.terminal_at = None
        self.replayed = False
        self.failure = None
        self.network_usage = None

    def _absorb(self, value: dict[str, Any] | None) -> None:
        body = _obj(value)
        self.id = _text(body.get("id")) or self.id
        usage = body.get("network_usage")
        self.network_usage = dict(usage) if isinstance(usage, dict) else None
        failure = body.get("failure")
        self.failure = dict(failure) if isinstance(failure, dict) else None
        if "state" in body:
            self.state = SandboxState.coerce(body.get("state"))
        if "envelope" in body:
            self.envelope = _obj(body.get("envelope"))
        if "cost_usd" in body:
            self.cost_usd = _num(body.get("cost_usd"))
        if "url" in body:
            self.url = _text(body.get("url"))
        for name in ("created_at", "updated_at", "last_activity_at", "terminal_at"):
            if name in body:
                setattr(self, name, _dt(body.get(name)))

    @property
    def is_terminal(self) -> bool:
        return getattr(self.state, "value", self.state) in SANDBOX_TERMINAL


class _SandboxExecState:
    id: str
    sandbox_id: str
    state: Any
    spec: dict[str, Any]
    exit_code: int | None
    failure_code: str
    replayed: bool
    output_sequence: int
    stdout_bytes: int
    stderr_bytes: int
    final_output_sequence: int | None

    def _init_exec_state(self, sandbox_id: str, exec_id: str = "") -> None:
        self.id = exec_id
        self.sandbox_id = sandbox_id
        self.state = None
        self.spec = {}
        self.exit_code = None
        self.failure_code = ""
        self.replayed = False
        self.output_sequence = 0
        self.stdout_bytes = 0
        self.stderr_bytes = 0
        self.final_output_sequence = None
        self.created_at = None
        self.updated_at = None
        self.dispatched_at = None
        self.deadline_at = None
        self.started_at = None
        self.completed_at = None

    def _absorb(self, value: dict[str, Any] | None) -> None:
        body = _obj(value)
        self.id = _text(body.get("id")) or self.id
        self.sandbox_id = _text(body.get("sandbox_id")) or self.sandbox_id
        if "state" in body:
            self.state = SandboxExecState.coerce(body.get("state"))
        if "spec" in body:
            self.spec = _obj(body.get("spec"))
        if "exit_code" in body:
            value = body.get("exit_code")
            self.exit_code = _int(value) if value is not None else None
        self.failure_code = _text(body.get("failure_code")) or self.failure_code
        for name in ("output_sequence", "stdout_bytes", "stderr_bytes"):
            if name in body:
                setattr(self, name, _int(body.get(name)))
        if "final_output_sequence" in body:
            final = body.get("final_output_sequence")
            self.final_output_sequence = _int(final) if final is not None else None
        for name in ("created_at", "updated_at", "dispatched_at", "deadline_at", "started_at", "completed_at"):
            if name in body:
                setattr(self, name, _dt(body.get(name)))

    @property
    def command(self) -> list[str]:
        return [str(arg) for arg in _rows(self.spec.get("command"))]

    @property
    def is_terminal(self) -> bool:
        return getattr(self.state, "value", self.state) in EXEC_TERMINAL

    @property
    def succeeded(self) -> bool:
        return self.state == SandboxExecState.COMPLETED and self.exit_code == 0


class Sandboxes:
    """Create, reconnect to, and list customer sandboxes."""

    def __init__(self, client: Any):
        self._client = client

    def create(
        self,
        *,
        image: str | None = None,
        name: str | None = None,
        budget: float | None = None,
        wake: str | None = None,
        requirements: dict[str, Any] | None = None,
        outcome: dict[str, Any] | None = None,
        policy: dict[str, Any] | None = None,
        lifecycle: dict[str, Any] | None = None,
        reservation: dict[str, Any] | None = None,
        continuity: dict[str, Any] | None = None,
        from_snapshot: str | None = None,
        secrets: list[str] | None = None,
        idempotency_key: str | None = None,
    ) -> "Sandbox":
        body = _create_payload(
            image=image, name=name, budget=budget, wake=wake, requirements=requirements,
            outcome=outcome, policy=policy, lifecycle=lifecycle,
            reservation=reservation, continuity=continuity,
            from_snapshot=from_snapshot, secrets=secrets,
        )
        headers: dict[str, str] = {}
        response = self._client._request(
            "POST", "/v1/sandboxes", json=body,
            idempotency_key=idempotency_key or f"sandbox-{uuid.uuid4()}",
            headers_out=headers,
        )
        sandbox = Sandbox(self._client)
        sandbox._absorb(self._client._one(response, "POST", "/v1/sandboxes"))
        sandbox.replayed = any(name.lower() == "idempotent-replayed" and value.lower() == "true" for name, value in headers.items())
        return sandbox

    def from_id(self, sandbox_id: str) -> "Sandbox":
        path = f"/v1/sandboxes/{_valid_id(sandbox_id, 'sandbox')}"
        sandbox = Sandbox(self._client, sandbox_id)
        sandbox._absorb(self._client._one(self._client._request("GET", path), "GET", path))
        return sandbox

    def list_page(
        self, *, limit: int = 50, cursor: str | None = None,
        name: str | None = None, state: str | SandboxState | None = None,
    ) -> tuple[list["Sandbox"], str | None]:
        params: dict[str, Any] = {"limit": limit}
        if cursor is not None:
            params["cursor"] = cursor
        if name is not None:
            params["name"] = name
        if state is not None:
            params["status"] = getattr(state, "value", state)
        response = _obj(self._client._request("GET", "/v1/sandboxes", params=params))
        rows = []
        for item in _rows(response.get("sandboxes")):
            sandbox = Sandbox(self._client)
            sandbox._absorb(_obj(item))
            rows.append(sandbox)
        cursor_value = response.get("next_cursor")
        return rows, cursor_value if isinstance(cursor_value, str) and cursor_value else None

    def list(self, **kwargs: Any) -> list["Sandbox"]:
        rows, _ = self.list_page(**kwargs)
        return rows

    def iterate(self) -> Iterator["Sandbox"]:
        cursor = None
        while True:
            rows, next_cursor = self.list_page(cursor=cursor)
            yield from rows
            if next_cursor is None or next_cursor == cursor:
                return
            cursor = next_cursor

    def __iter__(self) -> Iterator["Sandbox"]:
        return self.iterate()


class Sandbox(_SandboxState):
    def __init__(
        self,
        client: Any | None = None,
        sandbox_id: str = "",
        *,
        image: str | None = None,
        name: str | None = None,
        budget: float | None = None,
        wake: str | None = None,
        requirements: dict[str, Any] | None = None,
        outcome: dict[str, Any] | None = None,
        policy: dict[str, Any] | None = None,
        lifecycle: dict[str, Any] | None = None,
        reservation: dict[str, Any] | None = None,
        continuity: dict[str, Any] | None = None,
        from_snapshot: str | None = None,
        secrets: list[str] | None = None,
        idempotency_key: str | None = None,
    ):
        if client is None and image is None and not name:
            raise ValidationError("image or name is required")
        self._client = client
        self._owned_client = client is None
        self._init_state(sandbox_id)
        if client is None:
            from . import Client

            self._client = Client()
            try:
                created = self._client.sandboxes.create(
                    image=image,
                    name=name,
                    budget=budget,
                    wake=wake,
                    requirements=requirements,
                    outcome=outcome,
                    policy=policy,
                    lifecycle=lifecycle,
                    reservation=reservation,
                    continuity=continuity,
                    from_snapshot=from_snapshot, secrets=secrets,
                    idempotency_key=idempotency_key,
                )
            except BaseException:
                self._client.close()
                raise
            self._absorb({
                "id": created.id,
                "state": getattr(created.state, "value", created.state),
                "envelope": created.envelope,
                "cost_usd": created.cost_usd,
                "failure": created.failure,
                "network_usage": created.network_usage,
                "url": created.url,
                "created_at": created.created_at,
                "updated_at": created.updated_at,
                "last_activity_at": created.last_activity_at,
                "terminal_at": created.terminal_at,
            })
            self.replayed = created.replayed

    def __enter__(self) -> "Sandbox":
        return self

    def close(self) -> None:
        """Close this handle's owned HTTP client without terminating the sandbox."""
        if self._owned_client:
            self._client.close()

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        try:
            if getattr(self.state, "value", self.state) != SandboxState.TERMINATED.value:
                self.terminate()
        except BaseException:
            if exc_type is None:
                raise
        finally:
            self.close()

    def refresh(self) -> "Sandbox":
        path = f"/v1/sandboxes/{_valid_id(self.id, 'sandbox')}"
        self._absorb(self._client._one(self._client._request("GET", path), "GET", path))
        return self

    def refresh_secrets(self) -> None:
        """Bind current secret versions for subsequent commands."""
        path = f"/v1/sandboxes/{_valid_id(self.id, 'sandbox')}/refresh-secrets"
        self._client._request("POST", path)

    def events(self, *, after: int = 0) -> list[Event]:
        """Read up to 100 lifecycle and denied-host events after an event sequence."""
        path = f"/v1/sandboxes/{_valid_id(self.id, 'sandbox')}/events"
        response = self._client._request("GET", path, params={"after": after})
        return [Event.from_dict(_obj(row)) for row in _rows(_obj(response).get("events"))]

    def exec(
        self, command: str | list[str] | tuple[str, ...], *, cwd: str | None = None,
        env: dict[str, str] | None = None, timeout_seconds: int | None = None,
        stdin: bool = False, idempotency_key: str | None = None,
    ) -> "SandboxExec":
        sandbox_id = _valid_id(self.id, "sandbox")
        path = f"/v1/sandboxes/{sandbox_id}/exec"
        headers: dict[str, str] = {}
        response = self._client._request(
            "POST", path,
            json=_exec_payload(command, cwd=cwd, env=env, timeout_seconds=timeout_seconds, stdin=stdin),
            idempotency_key=idempotency_key or f"sandbox-exec-{uuid.uuid4()}",
            headers_out=headers,
        )
        execution = SandboxExec(self._client, sandbox_id)
        execution._absorb(self._client._one(response, "POST", path))
        execution.replayed = any(name.lower() == "idempotent-replayed" and value.lower() == "true" for name, value in headers.items())
        return execution

    def terminate(self, *, idempotency_key: str | None = None) -> "Sandbox":
        sandbox_id = _valid_id(self.id, "sandbox")
        path = f"/v1/sandboxes/{sandbox_id}/terminate"
        response = self._client._request(
            "POST", path, json={},
            idempotency_key=idempotency_key or f"sandbox-terminate-{uuid.uuid4()}",
        )
        self._absorb(self._client._one(response, "POST", path))
        return self


class SandboxExec(_SandboxExecState):
    def __init__(self, client: Any, sandbox_id: str, exec_id: str = ""):
        self._client = client
        self._init_exec_state(sandbox_id, exec_id)

    def _path(self) -> str:
        return f"/v1/sandboxes/{_valid_id(self.sandbox_id, 'sandbox')}/execs/{_valid_id(self.id, 'sandbox execution')}"

    def refresh(self, *, wait: bool = False, _timeout: float | None = None) -> "SandboxExec":
        path = self._path()
        response = self._client._request(
            "GET", path, params={"wait": str(wait).lower()},
            timeout=_timeout if _timeout is not None else (35 if wait else None),
            max_retries=0 if _timeout is not None else None,
        )
        self._absorb(self._client._one(response, "GET", path))
        return self

    def wait(self, *, timeout_seconds: float | None = None) -> "SandboxExec":
        if timeout_seconds is not None and (not math.isfinite(timeout_seconds) or timeout_seconds <= 0):
            raise ValueError("timeout_seconds must be finite and greater than zero")
        deadline = None if timeout_seconds is None else time.monotonic() + timeout_seconds
        while not self.is_terminal:
            remaining = None if deadline is None else deadline - time.monotonic()
            if remaining is not None and remaining <= 0:
                raise APITimeoutError(f"sandbox execution {self.id} did not finish in time")
            self.refresh(wait=True, _timeout=None if remaining is None else min(35, remaining))
        return self

    def output(self, *, after: int = 0, limit: int = 16, wait: bool = False) -> SandboxOutputPage:
        path = self._path() + "/stream"
        response = self._client._request(
            "GET", path,
            params={"after": after, "limit": limit, "wait": str(wait).lower()},
            timeout=35 if wait else None,
        )
        return SandboxOutputPage.from_dict(self._client._one(response, "GET", path))

    def iter_output(self, *, after: int = 0, follow: bool = True) -> Iterator[SandboxOutputFrame]:
        cursor = after
        while True:
            page = self.output(after=cursor, wait=follow)
            for frame in page.frames:
                if frame.sequence > cursor:
                    cursor = frame.sequence
                    yield frame
            cursor = max(cursor, page.next_sequence)
            if page.done or not follow:
                return

    def write(
        self, data: bytes | str = b"", *, eof: bool = False,
        idempotency_key: str | None = None,
    ) -> SandboxInputReceipt:
        raw = data.encode() if isinstance(data, str) else bytes(data)
        path = self._path() + "/stdin"
        response = self._client._request(
            "POST", path,
            json={"data": base64.b64encode(raw).decode("ascii"), "eof": eof},
            idempotency_key=idempotency_key or f"sandbox-stdin-{uuid.uuid4()}",
        )
        return SandboxInputReceipt.from_dict(self._client._one(response, "POST", path))


class AsyncSandboxes:
    def __init__(self, client: Any):
        self._client = client

    async def create(
        self,
        *,
        image: str | None = None,
        name: str | None = None,
        budget: float | None = None,
        wake: str | None = None,
        requirements: dict[str, Any] | None = None,
        outcome: dict[str, Any] | None = None,
        policy: dict[str, Any] | None = None,
        lifecycle: dict[str, Any] | None = None,
        reservation: dict[str, Any] | None = None,
        continuity: dict[str, Any] | None = None,
        from_snapshot: str | None = None,
        secrets: list[str] | None = None,
        idempotency_key: str | None = None,
    ) -> "AsyncSandbox":
        body = _create_payload(
            image=image, name=name, budget=budget, wake=wake, requirements=requirements,
            outcome=outcome, policy=policy, lifecycle=lifecycle,
            reservation=reservation, continuity=continuity,
            from_snapshot=from_snapshot, secrets=secrets,
        )
        headers: dict[str, str] = {}
        response = await self._client._request(
            "POST", "/v1/sandboxes", json=body,
            idempotency_key=idempotency_key or f"sandbox-{uuid.uuid4()}",
            headers_out=headers,
        )
        sandbox = AsyncSandbox(self._client)
        sandbox._absorb(self._client._one(response, "POST", "/v1/sandboxes"))
        sandbox.replayed = any(name.lower() == "idempotent-replayed" and value.lower() == "true" for name, value in headers.items())
        return sandbox

    async def from_id(self, sandbox_id: str) -> "AsyncSandbox":
        path = f"/v1/sandboxes/{_valid_id(sandbox_id, 'sandbox')}"
        sandbox = AsyncSandbox(self._client, sandbox_id)
        sandbox._absorb(self._client._one(await self._client._request("GET", path), "GET", path))
        return sandbox

    async def list_page(
        self, *, limit: int = 50, cursor: str | None = None,
        name: str | None = None, state: str | SandboxState | None = None,
    ) -> tuple[list["AsyncSandbox"], str | None]:
        params: dict[str, Any] = {"limit": limit}
        if cursor is not None:
            params["cursor"] = cursor
        if name is not None:
            params["name"] = name
        if state is not None:
            params["status"] = getattr(state, "value", state)
        response = _obj(await self._client._request("GET", "/v1/sandboxes", params=params))
        rows = []
        for item in _rows(response.get("sandboxes")):
            sandbox = AsyncSandbox(self._client)
            sandbox._absorb(_obj(item))
            rows.append(sandbox)
        cursor_value = response.get("next_cursor")
        return rows, cursor_value if isinstance(cursor_value, str) and cursor_value else None

    async def list(self, **kwargs: Any) -> list["AsyncSandbox"]:
        rows, _ = await self.list_page(**kwargs)
        return rows

    async def iterate(self) -> AsyncIterator["AsyncSandbox"]:
        cursor = None
        while True:
            rows, next_cursor = await self.list_page(cursor=cursor)
            for row in rows:
                yield row
            if next_cursor is None or next_cursor == cursor:
                return
            cursor = next_cursor


class AsyncSandbox(_SandboxState):
    def __init__(self, client: Any, sandbox_id: str = ""):
        self._client = client
        self._owned_client = False
        self._init_state(sandbox_id)

    async def close(self) -> None:
        """Close this handle's owned HTTP client without terminating the sandbox."""
        if self._owned_client:
            await self._client.aclose()

    async def refresh(self) -> "AsyncSandbox":
        path = f"/v1/sandboxes/{_valid_id(self.id, 'sandbox')}"
        self._absorb(self._client._one(await self._client._request("GET", path), "GET", path))
        return self

    async def refresh_secrets(self) -> None:
        """Bind current secret versions for subsequent commands."""
        path = f"/v1/sandboxes/{_valid_id(self.id, 'sandbox')}/refresh-secrets"
        await self._client._request("POST", path)

    async def events(self, *, after: int = 0) -> list[Event]:
        """Read up to 100 lifecycle and denied-host events after an event sequence."""
        path = f"/v1/sandboxes/{_valid_id(self.id, 'sandbox')}/events"
        response = await self._client._request("GET", path, params={"after": after})
        return [Event.from_dict(_obj(row)) for row in _rows(_obj(response).get("events"))]

    async def exec(
        self, command: str | list[str] | tuple[str, ...], *, cwd: str | None = None,
        env: dict[str, str] | None = None, timeout_seconds: int | None = None,
        stdin: bool = False, idempotency_key: str | None = None,
    ) -> "AsyncSandboxExec":
        sandbox_id = _valid_id(self.id, "sandbox")
        path = f"/v1/sandboxes/{sandbox_id}/exec"
        headers: dict[str, str] = {}
        response = await self._client._request(
            "POST", path,
            json=_exec_payload(command, cwd=cwd, env=env, timeout_seconds=timeout_seconds, stdin=stdin),
            idempotency_key=idempotency_key or f"sandbox-exec-{uuid.uuid4()}",
            headers_out=headers,
        )
        execution = AsyncSandboxExec(self._client, sandbox_id)
        execution._absorb(self._client._one(response, "POST", path))
        execution.replayed = any(name.lower() == "idempotent-replayed" and value.lower() == "true" for name, value in headers.items())
        return execution

    async def terminate(self, *, idempotency_key: str | None = None) -> "AsyncSandbox":
        sandbox_id = _valid_id(self.id, "sandbox")
        path = f"/v1/sandboxes/{sandbox_id}/terminate"
        response = await self._client._request(
            "POST", path, json={},
            idempotency_key=idempotency_key or f"sandbox-terminate-{uuid.uuid4()}",
        )
        self._absorb(self._client._one(response, "POST", path))
        return self


class AsyncSandboxExec(_SandboxExecState):
    def __init__(self, client: Any, sandbox_id: str, exec_id: str = ""):
        self._client = client
        self._init_exec_state(sandbox_id, exec_id)

    def _path(self) -> str:
        return f"/v1/sandboxes/{_valid_id(self.sandbox_id, 'sandbox')}/execs/{_valid_id(self.id, 'sandbox execution')}"

    async def refresh(self, *, wait: bool = False, _timeout: float | None = None) -> "AsyncSandboxExec":
        path = self._path()
        response = await self._client._request(
            "GET", path, params={"wait": str(wait).lower()},
            timeout=_timeout if _timeout is not None else (35 if wait else None),
            max_retries=0 if _timeout is not None else None,
        )
        self._absorb(self._client._one(response, "GET", path))
        return self

    async def wait(self, *, timeout_seconds: float | None = None) -> "AsyncSandboxExec":
        if timeout_seconds is not None and (not math.isfinite(timeout_seconds) or timeout_seconds <= 0):
            raise ValueError("timeout_seconds must be finite and greater than zero")
        deadline = None if timeout_seconds is None else time.monotonic() + timeout_seconds
        while not self.is_terminal:
            remaining = None if deadline is None else deadline - time.monotonic()
            if remaining is not None and remaining <= 0:
                raise APITimeoutError(f"sandbox execution {self.id} did not finish in time")
            await self.refresh(wait=True, _timeout=None if remaining is None else min(35, remaining))
        return self

    async def output(self, *, after: int = 0, limit: int = 16, wait: bool = False) -> SandboxOutputPage:
        path = self._path() + "/stream"
        response = await self._client._request(
            "GET", path,
            params={"after": after, "limit": limit, "wait": str(wait).lower()},
            timeout=35 if wait else None,
        )
        return SandboxOutputPage.from_dict(self._client._one(response, "GET", path))

    async def iter_output(self, *, after: int = 0, follow: bool = True) -> AsyncIterator[SandboxOutputFrame]:
        cursor = after
        while True:
            page = await self.output(after=cursor, wait=follow)
            for frame in page.frames:
                if frame.sequence > cursor:
                    cursor = frame.sequence
                    yield frame
            cursor = max(cursor, page.next_sequence)
            if page.done or not follow:
                return

    async def write(
        self, data: bytes | str = b"", *, eof: bool = False,
        idempotency_key: str | None = None,
    ) -> SandboxInputReceipt:
        raw = data.encode() if isinstance(data, str) else bytes(data)
        path = self._path() + "/stdin"
        response = await self._client._request(
            "POST", path,
            json={"data": base64.b64encode(raw).decode("ascii"), "eof": eof},
            idempotency_key=idempotency_key or f"sandbox-stdin-{uuid.uuid4()}",
        )
        return SandboxInputReceipt.from_dict(self._client._one(response, "POST", path))
