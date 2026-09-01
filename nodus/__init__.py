"""Nodus Python SDK.

Submit workload requirements and outcomes; Nodus matches infrastructure,
manages cost to completion, and recovers through reclaim. You describe the work
and its constraints — never a machine, an instance type, or a supplier.

Two settings are required, and both come from the console at
https://nodus.run/console/ — the quickstart shown there has your key and the
API address already in it:

    export NODUS_BASE_URL=https://…
    export NODUS_API_KEY=nk_live_…

    import nodus

    with nodus.Client() as client:
        workload = client.run(
            model="7B fine-tune",
            command=["python", "train.py"],
            peak_memory_gb=80,
            expected_runtime_hours=18,
            budget=400,
        )
        workload.wait()
        print(workload.status, workload.route.sku, workload.spend_usd)
"""

from __future__ import annotations

import asyncio
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Iterator

import httpx

from ._brief import build_payload, status_filter
from .errors import (
    APIConnectionError,
    APIError,
    APITimeoutError,
    AuthenticationError,
    BudgetExceededError,
    CapacityUnavailableError,
    ConfigurationError,
    IdempotencyConflictError,
    NodusError,
    NotFoundError,
    RateLimitError,
    SignatureError,
    ValidationError,
    error_from_response,
)
from .types import (
    TERMINAL,
    TERMINAL_STATUSES,
    Artifact,
    ComputeClass,
    ContinuityMode,
    Event,
    InterruptTolerance,
    Ledger,
    LedgerEntry,
    ManifestFile,
    Route,
    Settlement,
    StageRun,
    WorkloadStatus,
)

__version__ = "1.0.0"

__all__ = [
    "Client",
    "AsyncClient",
    "Workload",
    "AsyncWorkload",
    "Route",
    "StageRun",
    "Artifact",
    "ManifestFile",
    "Event",
    "Ledger",
    "LedgerEntry",
    "Settlement",
    "ComputeClass",
    "ContinuityMode",
    "InterruptTolerance",
    "WorkloadStatus",
    "NodusError",
    "ConfigurationError",
    "AuthenticationError",
    "NotFoundError",
    "ValidationError",
    "IdempotencyConflictError",
    "RateLimitError",
    "BudgetExceededError",
    "CapacityUnavailableError",
    "SignatureError",
    "APIError",
    "APIConnectionError",
    "APITimeoutError",
    "run",
    "__version__",
]

# Statuses worth another attempt, and the methods safe to replay. A submission
# carries an Idempotency-Key so POST /v1/workloads is replay-safe too.
_RETRY_STATUSES = frozenset({408, 429, 500, 502, 503, 504})
_RETRY_METHODS = frozenset({"GET", "PUT", "DELETE", "POST"})


def _retry_after(resp: httpx.Response) -> float | None:
    raw = resp.headers.get("Retry-After")
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    return value if 0 < value <= 300 else None


# Ceiling on how long a wait holds off after a failed poll. A wait is cheap to
# keep alive and expensive to abandon, so the interval widens — but not so far
# that a finished workload sits unnoticed.
_WAIT_BACKOFF_CAP = 30.0


def _is_transient(exc: BaseException) -> bool:
    """True when another poll could plausibly succeed.

    The same rule the transport retries a single request by, so a wait and a
    lone call never disagree about which failures clear on their own.
    """
    if isinstance(exc, (APITimeoutError, APIConnectionError)):
        return True
    return isinstance(exc, NodusError) and exc.status_code in _RETRY_STATUSES


class _WaitPolicy:
    """How a long poll answers failure. One copy, so sync and async cannot drift.

    A workload outlives any single request, and keeps billing whatever the
    client believes, so a transient failure widens the interval instead of
    ending the wait. A permanent one — a revoked key, an unknown id — is raised
    immediately, because waiting on it is a program that hangs instead of
    failing.
    """

    def __init__(self, poll_seconds: float, timeout_seconds: float | None = None):
        self.poll_seconds = poll_seconds
        self.deadline = None if timeout_seconds is None else time.monotonic() + timeout_seconds
        self.failures = 0
        self.last: NodusError | None = None

    def failed(self, exc: NodusError) -> float:
        """Seconds to hold off after a failed poll; re-raises what will never clear."""
        if not _is_transient(exc):
            raise exc
        self.failures += 1
        self.last = exc
        hinted = getattr(exc, "retry_after", None)
        if hinted:
            return min(float(hinted), _WAIT_BACKOFF_CAP)
        widened = max(self.poll_seconds, 0.05) * 2 ** min(self.failures - 1, 10)
        return min(_WAIT_BACKOFF_CAP, widened)

    def polled(self) -> float:
        """Seconds until the next poll, after one that answered."""
        self.failures, self.last = 0, None
        return self.poll_seconds

    def hold(self, delay: float, workload_id: str) -> float:
        """Seconds to sleep, or the caller's bound expiring as an error.

        Never sleeps past that bound: a widened backoff must not overshoot the
        deadline the caller asked for.
        """
        if self.deadline is None:
            return delay
        left = self.deadline - time.monotonic()
        if left <= 0:
            raise APITimeoutError(f"workload {workload_id} did not finish in time") from self.last
        return max(0.0, min(delay, left))


def _setup_help(missing: list[str]) -> str:
    """What to tell someone whose client cannot be built yet.

    It names the settings, says where their values come from, and gives the
    lines to run. There is no built-in address to fall back on: a guessed one
    is either a domain that does not exist or an account the caller is not on,
    and both answer a setup mistake with a network error.

    ASCII only. This lands on a terminal, and a Windows console on a legacy
    code page turns a typographic ellipsis into a replacement character —
    mojibake in the one message whose whole job is to be read and copied.
    """
    joined = " and ".join(missing)
    verb = "is" if len(missing) == 1 else "are"
    return (
        f"Nodus is not configured yet: {joined} {verb} not set.\n"
        "\n"
        "Sign in at https://nodus.run/console/ and create an API key. The\n"
        "quickstart shown there has your key and the API address in it:\n"
        "\n"
        "    export NODUS_BASE_URL=https://your-api-address\n"
        "    export NODUS_API_KEY=nk_live_your_key\n"
        "\n"
        "Or pass them straight in: nodus.Client(api_key=..., base_url=...)."
    )


def _resolve(api_key: str | None, base_url: str | None) -> tuple[str, str]:
    key = (api_key or os.environ.get("NODUS_API_KEY") or "").strip()
    url = (base_url or os.environ.get("NODUS_BASE_URL") or "").strip().rstrip("/")
    missing = [
        name
        for name, value in (("NODUS_BASE_URL", url), ("NODUS_API_KEY", key))
        if not value
    ]
    if missing:
        raise ConfigurationError(_setup_help(missing))
    if not url.startswith(("http://", "https://")):
        raise ConfigurationError(
            f"base_url must start with http:// or https://, got {url!r}. "
            "It is the API address from https://nodus.run/console/."
        )
    return key, url


def _headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "User-Agent": f"nodus-python/{__version__}",
    }


@dataclass
class _WorkloadState:
    """Fields shared by the sync and async workload handles."""

    id: str = ""
    status: Any = None
    route: Route | None = None
    spend_usd: float = 0.0
    budget_usd: float = 0.0
    revision: int = 1
    stages: list[StageRun] = field(default_factory=list)
    error: str | None = None
    created_at: Any = None
    updated_at: Any = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    def _absorb(self, d: dict[str, Any]) -> None:
        """Update in place from a wire object.

        In place rather than returning a new handle: after ``wait()`` the caller
        reads attributes off the object it already has, without another round
        trip and without having to rebind.
        """
        from .types import _dt  # local import: internal helper

        # Submit answers with "workload_id"; every read endpoint answers with
        # "id". Accepting both is what makes run() work against the real
        # control plane: the SDK read only "id", so client.run() raised
        # "submit returned no workload id" on a 202 that had in fact created
        # the workload. Unit tests missed it because the fixtures used a shape
        # POST /v1/workloads has never returned.
        self.id = d.get("id") or d.get("workload_id") or self.id
        self.status = WorkloadStatus.coerce(d.get("status"))
        self.route = Route.from_dict(d.get("route")) or self.route
        self.spend_usd = float(d.get("spend_usd") or 0.0)
        outcome = (d.get("payload") or {}).get("outcome") or {}
        self.budget_usd = float(d.get("budget_usd") or outcome.get("max_cost_usd") or 0.0)
        self.revision = int(d.get("revision") or self.revision)
        self.stages = [StageRun.from_dict(s) for s in (d.get("stages") or [])]
        self.error = d.get("error") or d.get("error_message")
        self.created_at = _dt(d.get("created_at")) or self.created_at
        self.updated_at = _dt(d.get("updated_at")) or self.updated_at
        self.raw = d

    @property
    def is_terminal(self) -> bool:
        """True once the status can no longer change."""
        return getattr(self.status, "value", self.status) in TERMINAL

    @property
    def succeeded(self) -> bool:
        """True only for COMPLETED. ``is_terminal`` is also true for failure."""
        return getattr(self.status, "value", self.status) == WorkloadStatus.COMPLETED.value


class _Transport:
    """Shared request policy: auth, error mapping, retry with backoff."""

    def __init__(self, max_retries: int):
        self.max_retries = max(0, int(max_retries))

    def _raise(self, method: str, path: str, resp: httpx.Response) -> None:
        try:
            body: Any = resp.json()
        except Exception:
            body = resp.text
        raise error_from_response(
            method,
            path,
            resp.status_code,
            body,
            retry_after=_retry_after(resp),
            request_id=resp.headers.get("X-Request-Id"),
        )

    def _should_retry(self, method: str, status: int, attempt: int) -> bool:
        return (
            attempt < self.max_retries
            and method.upper() in _RETRY_METHODS
            and status in _RETRY_STATUSES
        )

    @staticmethod
    def _backoff(attempt: int, resp: httpx.Response | None) -> float:
        if resp is not None:
            hinted = _retry_after(resp)
            if hinted:
                return hinted
        return min(8.0, 0.5 * (2**attempt))


class Client(_Transport):
    """Synchronous Nodus client.

    One instance per process is enough — the underlying transport pools
    connections, so constructing a client per call throws that away. The client
    is safe to share across threads; the workload handles it returns are not.
    """

    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str | None = None,
        timeout: float = 30.0,
        max_retries: int = 2,
    ):
        super().__init__(max_retries)
        self.api_key, self.base_url = _resolve(api_key, base_url)
        self._http = httpx.Client(
            base_url=self.base_url, timeout=timeout, headers=_headers(self.api_key)
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "Client":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        idempotency_key: str | None = None,
        params: dict[str, Any] | None = None,
        text: bool = False,
    ) -> Any:
        headers = {"Idempotency-Key": idempotency_key} if idempotency_key else {}
        attempt = 0
        while True:
            try:
                resp = self._http.request(
                    method, path, json=json, headers=headers, params=params
                )
            except httpx.TimeoutException as exc:
                if attempt < self.max_retries:
                    time.sleep(self._backoff(attempt, None))
                    attempt += 1
                    continue
                raise APITimeoutError(f"{method} {path} timed out") from exc
            except httpx.HTTPError as exc:
                if attempt < self.max_retries:
                    time.sleep(self._backoff(attempt, None))
                    attempt += 1
                    continue
                raise APIConnectionError(f"{method} {path} failed to connect: {exc}") from exc

            if resp.status_code >= 400:
                if self._should_retry(method, resp.status_code, attempt):
                    time.sleep(self._backoff(attempt, resp))
                    attempt += 1
                    continue
                self._raise(method, path, resp)

            if not resp.content:
                return "" if text else None
            # The log endpoint answers text/plain, because its whole purpose is
            # to be read. Everything else is JSON.
            return resp.text if text else resp.json()

    # -- workloads ---------------------------------------------------------

    def run(self, *, idempotency_key: str | None = None, **brief: Any) -> "Workload":
        """Submit a brief — requirements and outcomes, never a machine.

        Returns as soon as the workload is accepted; it is not yet placed. Call
        ``wait()`` on the handle to block until it reaches a terminal state.

        ``idempotency_key`` defaults to a fresh value per call, which covers
        this call's own retries and nothing beyond it. To make an
        application-level retry loop safe, pass a key derived from the thing you
        are running so a resubmission cannot become a second paid workload.
        """
        payload = build_payload(**brief)
        res = self._request(
            "POST",
            "/v1/workloads",
            json=payload,
            idempotency_key=idempotency_key or f"nodus-{uuid.uuid4()}",
        )
        wl = Workload(self)
        wl._absorb(res or {})
        if not wl.id:
            raise NodusError("submit returned no workload id", body=res)
        return wl

    def get(self, workload_id: str) -> "Workload":
        wl = Workload(self)
        wl._absorb(self._request("GET", f"/v1/workloads/{workload_id}") or {})
        return wl

    def list(
        self, *, limit: int = 50, offset: int = 0, status: Any = None
    ) -> list["Workload"]:
        """One page, newest first.

        ``status`` accepts a member, a wire string, a list of either, or the
        presets ``"active"`` and ``"terminal"``.
        """
        items, _ = self.list_page(limit=limit, offset=offset, status=status)
        return items

    def list_page(
        self, *, limit: int = 50, offset: int = 0, status: Any = None
    ) -> tuple[list["Workload"], int | None]:
        """A page plus the next offset, or ``None`` when the page is the last."""
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        wire = status_filter(status)
        if wire:
            params["status"] = wire
        res = self._request("GET", "/v1/workloads", params=params) or {}
        out = []
        for row in res.get("workloads") or []:
            wl = Workload(self)
            wl._absorb(row)
            out.append(wl)
        return out, res.get("next_offset")

    def iter_workloads(self, *, page_size: int = 50, status: Any = None) -> Iterator["Workload"]:
        """Page lazily so a long history never has to be held in memory."""
        offset = 0
        while True:
            page, nxt = self.list_page(limit=page_size, offset=offset, status=status)
            for wl in page:
                yield wl
            if nxt is None:
                return
            offset = nxt

    def cancel(self, workload_id: str, *, idempotency_key: str | None = None) -> None:
        self._request(
            "POST",
            f"/v1/workloads/{workload_id}/cancel",
            idempotency_key=idempotency_key or f"cancel-{workload_id}",
        )

    def events(self, workload_id: str, *, after: int = 0) -> list[Event]:
        res = self._request("GET", f"/v1/workloads/{workload_id}/events", params={"after": after})
        return [Event.from_dict(e) for e in (res or {}).get("events") or []]

    def artifacts(self, workload_id: str) -> list[Artifact]:
        res = self._request("GET", f"/v1/workloads/{workload_id}/artifacts")
        return [Artifact.from_dict(a) for a in (res or {}).get("artifacts") or []]

    def ledger(self, workload_id: str) -> Ledger:
        return Ledger.from_dict(self._request("GET", f"/v1/workloads/{workload_id}/ledger"))

    def logs(
        self, workload_id: str, *, stage: str | None = None, generation: int | None = None
    ) -> str:
        """What the submitted program printed, read back out of a committed artifact.

        Not a live stream: the log is collected as a named output and the
        control plane recomputes its digest before it agrees the run produced
        it, so this lags the process by a checkpoint. That is the point — what
        comes back is evidence rather than a tail.

        Narrowing by stage and generation matters after a reclaim: a stage that
        was interrupted has several generations, and their logs are different
        stories about the same work.
        """
        params: dict[str, Any] = {}
        if stage:
            params["stage"] = stage
        if generation:
            params["generation"] = generation
        return self._request(
            "GET", f"/v1/workloads/{workload_id}/logs", params=params or None, text=True
        ) or ""

    def wait(
        self,
        workload_id: str,
        *,
        poll_seconds: float = 2.0,
        timeout_seconds: float | None = None,
    ) -> "Workload":
        """Poll until the workload is terminal.

        Transient failures are retried for as long as the wait lasts; only the
        caller's own ``timeout_seconds`` ends it early. See :class:`_WaitPolicy`.
        """
        policy = _WaitPolicy(poll_seconds, timeout_seconds)
        while True:
            try:
                wl = self.get(workload_id)
            except NodusError as exc:
                delay = policy.failed(exc)
            else:
                if wl.is_terminal:
                    return wl
                delay = policy.polled()
            time.sleep(policy.hold(delay, workload_id))

    def stream_events(self, workload_id: str, *, poll_seconds: float = 2.0) -> Iterator[Event]:
        """Yield events as they occur, stopping at the terminal event.

        Follows the same policy as ``wait()``: the stream outlives a transient
        failure, and ends on one that will never clear.
        """
        after = 0
        policy = _WaitPolicy(poll_seconds)
        while True:
            try:
                batch = self.events(workload_id, after=after)
                terminal = self.get(workload_id).is_terminal
            except NodusError as exc:
                time.sleep(policy.failed(exc))
                continue
            for ev in batch:
                after = max(after, ev.seq)
                yield ev
            if terminal and not batch:
                return
            time.sleep(policy.polled())

    # -- webhooks / health -------------------------------------------------

    def set_webhook(self, url: str, *, secret: str | None = None) -> dict[str, Any]:
        body: dict[str, Any] = {"url": url}
        if secret:
            body["secret"] = secret
        return self._request("PUT", "/v1/webhooks", json=body) or {}

    def get_webhook(self) -> dict[str, Any]:
        return self._request("GET", "/v1/webhooks") or {}

    def delete_webhook(self) -> None:
        self._request("DELETE", "/v1/webhooks")

    def healthz(self) -> dict[str, Any]:
        return self._request("GET", "/healthz") or {}

    def readyz(self) -> dict[str, Any]:
        return self._request("GET", "/readyz") or {}


class Workload(_WorkloadState):
    """A handle on one submitted brief.

    Mutable: ``refresh()`` and ``wait()`` update this instance in place and also
    return it, so after either call you can read ``status``, ``route`` and
    ``spend_usd`` without a second round trip. The consequence is that a handle
    is not safe to share across threads — give each one its own from
    ``client.get()``.
    """

    def __init__(self, client: Client):
        super().__init__()
        self._client = client

    def refresh(self) -> "Workload":
        self._absorb(self._client._request("GET", f"/v1/workloads/{self.id}") or {})
        return self

    def wait(self, *, poll_seconds: float = 2.0, timeout_seconds: float | None = None) -> "Workload":
        """Poll until terminal.

        Raises :class:`APITimeoutError` if ``timeout_seconds`` elapses first;
        the workload keeps running, because a client-side deadline is not a
        cancellation. Transient poll failures are retried for the life of the
        wait, permanent ones raised at once. See :class:`_WaitPolicy`.
        """
        policy = _WaitPolicy(poll_seconds, timeout_seconds)
        while True:
            try:
                self.refresh()
            except NodusError as exc:
                delay = policy.failed(exc)
            else:
                if self.is_terminal:
                    return self
                delay = policy.polled()
            time.sleep(policy.hold(delay, self.id))

    def events(self, *, after: int = 0) -> list[Event]:
        return self._client.events(self.id, after=after)

    def stream_events(self, *, poll_seconds: float = 2.0) -> Iterator[Event]:
        return self._client.stream_events(self.id, poll_seconds=poll_seconds)

    def artifacts(self) -> list[Artifact]:
        return self._client.artifacts(self.id)

    def ledger(self) -> Ledger:
        return self._client.ledger(self.id)

    def cancel(self) -> None:
        self._client.cancel(self.id)


class AsyncClient(_Transport):
    """Asynchronous Nodus client. Mirrors :class:`Client` with ``await``."""

    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str | None = None,
        timeout: float = 30.0,
        max_retries: int = 2,
    ):
        super().__init__(max_retries)
        self.api_key, self.base_url = _resolve(api_key, base_url)
        self._http = httpx.AsyncClient(
            base_url=self.base_url, timeout=timeout, headers=_headers(self.api_key)
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> "AsyncClient":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        idempotency_key: str | None = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        headers = {"Idempotency-Key": idempotency_key} if idempotency_key else {}
        attempt = 0
        while True:
            try:
                resp = await self._http.request(
                    method, path, json=json, headers=headers, params=params
                )
            except httpx.TimeoutException as exc:
                if attempt < self.max_retries:
                    await asyncio.sleep(self._backoff(attempt, None))
                    attempt += 1
                    continue
                raise APITimeoutError(f"{method} {path} timed out") from exc
            except httpx.HTTPError as exc:
                if attempt < self.max_retries:
                    await asyncio.sleep(self._backoff(attempt, None))
                    attempt += 1
                    continue
                raise APIConnectionError(f"{method} {path} failed to connect: {exc}") from exc

            if resp.status_code >= 400:
                if self._should_retry(method, resp.status_code, attempt):
                    await asyncio.sleep(self._backoff(attempt, resp))
                    attempt += 1
                    continue
                self._raise(method, path, resp)

            if not resp.content:
                return None
            return resp.json()

    async def run(self, *, idempotency_key: str | None = None, **brief: Any) -> "AsyncWorkload":
        payload = build_payload(**brief)
        res = await self._request(
            "POST",
            "/v1/workloads",
            json=payload,
            idempotency_key=idempotency_key or f"nodus-{uuid.uuid4()}",
        )
        wl = AsyncWorkload(self)
        wl._absorb(res or {})
        if not wl.id:
            raise NodusError("submit returned no workload id", body=res)
        return wl

    async def get(self, workload_id: str) -> "AsyncWorkload":
        wl = AsyncWorkload(self)
        wl._absorb(await self._request("GET", f"/v1/workloads/{workload_id}") or {})
        return wl

    async def list(
        self, *, limit: int = 50, offset: int = 0, status: Any = None
    ) -> list["AsyncWorkload"]:
        items, _ = await self.list_page(limit=limit, offset=offset, status=status)
        return items

    async def list_page(
        self, *, limit: int = 50, offset: int = 0, status: Any = None
    ) -> tuple[list["AsyncWorkload"], int | None]:
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        wire = status_filter(status)
        if wire:
            params["status"] = wire
        res = await self._request("GET", "/v1/workloads", params=params) or {}
        out = []
        for row in res.get("workloads") or []:
            wl = AsyncWorkload(self)
            wl._absorb(row)
            out.append(wl)
        return out, res.get("next_offset")

    async def iter_workloads(
        self, *, page_size: int = 50, status: Any = None
    ) -> AsyncIterator["AsyncWorkload"]:
        offset = 0
        while True:
            page, nxt = await self.list_page(limit=page_size, offset=offset, status=status)
            for wl in page:
                yield wl
            if nxt is None:
                return
            offset = nxt

    async def cancel(self, workload_id: str, *, idempotency_key: str | None = None) -> None:
        await self._request(
            "POST",
            f"/v1/workloads/{workload_id}/cancel",
            idempotency_key=idempotency_key or f"cancel-{workload_id}",
        )

    async def events(self, workload_id: str, *, after: int = 0) -> list[Event]:
        res = await self._request(
            "GET", f"/v1/workloads/{workload_id}/events", params={"after": after}
        )
        return [Event.from_dict(e) for e in (res or {}).get("events") or []]

    async def artifacts(self, workload_id: str) -> list[Artifact]:
        res = await self._request("GET", f"/v1/workloads/{workload_id}/artifacts")
        return [Artifact.from_dict(a) for a in (res or {}).get("artifacts") or []]

    async def ledger(self, workload_id: str) -> Ledger:
        return Ledger.from_dict(await self._request("GET", f"/v1/workloads/{workload_id}/ledger"))

    async def healthz(self) -> dict[str, Any]:
        return await self._request("GET", "/healthz") or {}

    async def readyz(self) -> dict[str, Any]:
        return await self._request("GET", "/readyz") or {}


class AsyncWorkload(_WorkloadState):
    """Async handle. Identical attributes to :class:`Workload`; methods await."""

    def __init__(self, client: AsyncClient):
        super().__init__()
        self._client = client

    async def refresh(self) -> "AsyncWorkload":
        self._absorb(await self._client._request("GET", f"/v1/workloads/{self.id}") or {})
        return self

    async def wait(
        self, *, poll_seconds: float = 2.0, timeout_seconds: float | None = None
    ) -> "AsyncWorkload":
        """Poll until terminal. Same policy as :meth:`Workload.wait`."""
        policy = _WaitPolicy(poll_seconds, timeout_seconds)
        while True:
            try:
                await self.refresh()
            except NodusError as exc:
                delay = policy.failed(exc)
            else:
                if self.is_terminal:
                    return self
                delay = policy.polled()
            await asyncio.sleep(policy.hold(delay, self.id))

    async def events(self, *, after: int = 0) -> list[Event]:
        return await self._client.events(self.id, after=after)

    async def stream_events(self, *, poll_seconds: float = 2.0) -> AsyncIterator[Event]:
        """Yield events until the terminal one. Same policy as :meth:`Client.stream_events`."""
        after = 0
        policy = _WaitPolicy(poll_seconds)
        while True:
            try:
                batch = await self._client.events(self.id, after=after)
                terminal = (await self._client.get(self.id)).is_terminal
            except NodusError as exc:
                await asyncio.sleep(policy.failed(exc))
                continue
            for ev in batch:
                after = max(after, ev.seq)
                yield ev
            if terminal and not batch:
                return
            await asyncio.sleep(policy.polled())

    async def artifacts(self) -> list[Artifact]:
        return await self._client.artifacts(self.id)

    async def ledger(self) -> Ledger:
        return await self._client.ledger(self.id)

    async def cancel(self) -> None:
        await self._client.cancel(self.id)


def run(**brief: Any) -> Workload:
    """Submit and wait, using a client built from the environment."""
    with Client() as client:
        return client.run(**brief).wait()
