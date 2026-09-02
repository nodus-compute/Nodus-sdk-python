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
        wl = client.run(
            model="7B fine-tune",
            command=["python", "train.py"],
            peak_memory_gb=80,
            expected_runtime_hours=18,
            budget=400,
        )
        done = client.wait(wl.id)
        print(done.status, done.route.sku, done.cost_now_usd)
"""

from __future__ import annotations

import asyncio
import math
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from email.utils import parsedate_to_datetime
from importlib.metadata import PackageNotFoundError, version as _distribution_version
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
    Meter,
    Route,
    Settlement,
    StageRun,
    WorkloadStatus,
)

try:
    # The distribution is nodus_compute; the import name alone would resolve to a
    # different project that happens to be installed, or to nothing.
    __version__ = _distribution_version("nodus_compute")
except PackageNotFoundError:
    # Imported from a source tree that was never installed. Not a release
    # number, and deliberately not one that could be mistaken for one.
    __version__ = "0.0.0+source"

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
    "Meter",
    "ComputeClass",
    "ContinuityMode",
    "InterruptTolerance",
    "WorkloadStatus",
    "TERMINAL",
    "TERMINAL_STATUSES",
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
    "__version__",
]

# Statuses worth another attempt, and the methods safe to replay. A submission
# carries an Idempotency-Key so POST /v1/workloads is replay-safe too.
_RETRY_STATUSES = frozenset({408, 429, 500, 502, 503, 504})
_RETRY_METHODS = frozenset({"GET", "PUT", "DELETE", "POST"})


# Longest a single backoff will honour a Retry-After for. A larger hint is real
# and worth keeping on the error, but sleeping ten minutes inside one call is
# indistinguishable to the caller from a hang.
_RETRY_AFTER_CAP = 300.0


def _retry_after(resp: httpx.Response) -> float | None:
    """Seconds the server asked for, in either spelling the header allows.

    Returned as sent, not clamped: what to do about a very long hint is the
    caller's decision, and :func:`_retry_after_backoff` makes the SDK's own.
    """
    raw = resp.headers.get("Retry-After")
    if not raw:
        return None
    try:
        seconds = float(raw)
    except ValueError:
        pass
    else:
        # float() accepts "inf" and "nan". Neither is a number of seconds, and
        # honouring the first is a hang the caller cannot tell from a dropped
        # connection -- from a header the server chooses.
        return max(0.0, seconds) if math.isfinite(seconds) else None
    try:
        when = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    if when is None:
        return None
    now = datetime.now(when.tzinfo)
    return max(0.0, (when - now).total_seconds())


def _retry_after_backoff(resp: httpx.Response) -> float | None:
    """The hint a retry will actually wait for."""
    seconds = _retry_after(resp)
    if seconds is None or seconds <= 0:
        return None
    return min(seconds, _RETRY_AFTER_CAP)


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


def _is_header_safe(value: str) -> bool:
    """Whether this can be sent verbatim in a header or a URL.

    Printable ASCII and no spaces: a control character in a credential is a
    header injection waiting for a server that tolerates one, and a non-ASCII
    one is a UnicodeEncodeError thrown from inside the transport, long after
    the setting that caused it was read.
    """
    return bool(value) and value.isascii() and value.isprintable() and " " not in value


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
    for name, value in (("NODUS_BASE_URL", url), ("NODUS_API_KEY", key)):
        if not _is_header_safe(value):
            raise ConfigurationError(
                f"{name} contains a character that cannot be sent: it must be "
                "printable ASCII with no spaces or line breaks. Check for a "
                "newline picked up from a file, or a smart quote pasted from a "
                "browser."
            )
    if not url.startswith(("http://", "https://")):
        raise ConfigurationError(
            f"base_url must start with http:// or https://, got {url!r}. "
            "It is the API address from https://nodus.run/console/."
        )
    return key, url


#: What an identifier may contain. Everything the control plane mints is inside
#: this; everything outside it is a URL, not an id.
_WORKLOAD_ID = re.compile(r"\A[A-Za-z0-9_-]+\Z")


def _valid_id(workload_id: Any) -> str:
    """One path segment, checked before it can become part of a URL.

    ``/v1/workloads/{id}`` is a template, and httpx resolves dot segments before
    it sends: an id of ``../../v1/webhooks`` left the workload namespace and
    read back the webhook signing secret. An id that arrives from the server and
    is followed on the next refresh reaches the same place, so this is checked
    wherever an id becomes a path -- not only where a caller typed one.
    """
    if isinstance(workload_id, str) and _WORKLOAD_ID.match(workload_id):
        return workload_id
    raise ValidationError(
        f"{workload_id!r} is not a workload id: an id is letters, digits, "
        "underscores and hyphens. It becomes one segment of /v1/workloads/{id}, "
        "where a slash or a dot segment addresses a different endpoint entirely."
    )


def _valid_idempotency_key(key: str) -> str:
    """A key that can survive being a header.

    A non-ASCII one raised UnicodeEncodeError from inside the transport, mid
    submit, naming neither the key nor the call; one carrying CRLF was a header
    the server would reject on every attempt, retried to the end of the budget.
    """
    if _is_header_safe(key):
        return key
    raise ValidationError(
        f"idempotency_key {key!r} cannot be sent as a header: it must be "
        "printable ASCII with no spaces or line breaks."
    )


def _was_replayed(headers: dict[str, str]) -> bool:
    """Whether the control plane answered from an idempotency record.

    Anything but a case-insensitive "true" reads as a fresh submission:
    claiming a replay that did not happen is the reading that loses a run.
    """
    for name, value in headers.items():
        if name.lower() == "idempotent-replayed":
            return value.strip().lower() == "true"
    return False


def _headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "User-Agent": f"nodus-python/{__version__}",
    }


# Identity, not value: a handle is a live view of one workload, so two handles
# on the same id are two things to refresh, and equality on the fields would
# make every blank handle equal to every other one.
@dataclass(eq=False)
class _WorkloadState:
    """Fields shared by the sync and async workload handles."""

    id: str = ""
    status: Any = None
    route: Route | None = None
    #: Settled charges only. What a running workload costs is ``cost_now_usd``.
    spend_usd: float = 0.0
    budget_usd: float = 0.0
    #: Settled plus accruing, as of an instant. ``None`` until a read sends one.
    meter: Meter | None = None
    revision: int = 1
    stages: list[StageRun] = field(default_factory=list)
    #: True when the control plane answered from an idempotency record: the
    #: submission already existed, this call did not create a second run.
    replayed: bool = False
    error: str | None = None
    created_at: Any = None
    updated_at: Any = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    def _absorb(self, d: dict[str, Any]) -> None:
        """Update in place from a wire object, keeping every field it omits.

        In place rather than returning a new handle: after ``wait()`` the caller
        reads attributes off the object it already has, without another round
        trip and without having to rebind.

        An absent key is not a key set to nothing. A list row carries no route
        and no stages, and a body that clobbered them would leave the handle
        claiming the workload has none — worse for ``status``, where None is
        never terminal and a wait polling it can never end.
        """
        from .types import _dt, _int, _num, _obj, _rows  # local import: internal helpers

        # Submit answers with "workload_id"; every read endpoint answers with
        # "id". Accepting both is what makes run() work against the real
        # control plane: the SDK read only "id", so client.run() raised
        # "submit returned no workload id" on a 202 that had in fact created
        # the workload. Unit tests missed it because the fixtures used a shape
        # POST /v1/workloads has never returned.
        d = _obj(d)
        self.id = d.get("id") or d.get("workload_id") or self.id
        if "status" in d:
            self.status = WorkloadStatus.coerce(d.get("status"))
        self.route = Route.from_dict(d.get("route")) or self.route
        if "spend_usd" in d:
            self.spend_usd = _num(d.get("spend_usd"))
        self.meter = Meter.from_dict(d.get("meter")) or self.meter
        outcome = _obj(_obj(d.get("payload")).get("outcome"))
        ceiling = d.get("budget_usd") or outcome.get("max_cost_usd")
        if ceiling is not None:
            self.budget_usd = _num(ceiling, self.budget_usd)
        self.revision = _int(d.get("revision"), self.revision) or self.revision
        if "stages" in d:
            self.stages = [StageRun.from_dict(s) for s in _rows(d.get("stages"))]
        self.error = d.get("error") or d.get("error_message") or self.error
        self.created_at = _dt(d.get("created_at")) or self.created_at
        self.updated_at = _dt(d.get("updated_at")) or self.updated_at
        self.raw = d

    @property
    def cost_now_usd(self) -> float:
        """What this workload has cost as of the last read.

        ``meter.settled_usd`` counts only the current billing period and
        ``spend_usd`` lags a settling lease, so what has been charged is the
        larger of the two; ``meter.accruing_usd`` is open leases' money on top.
        """
        if self.meter is None:
            return self.spend_usd
        return max(self.spend_usd, self.meter.settled_usd) + self.meter.accruing_usd

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
            retry_after_header=resp.headers.get("Retry-After"),
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
            hinted = _retry_after_backoff(resp)
            if hinted:
                return hinted
        return min(8.0, 0.5 * (2**attempt))

    def _hold(self, attempt: int, resp: httpx.Response | None, slept: float) -> float | None:
        """Seconds to wait before the next attempt, or None to stop retrying.

        The Retry-After ceiling used to bound one sleep rather than one call, so
        two retries of a 300-second hint held a single call for ten minutes --
        a cap a loop can multiply is not a cap, and what the caller sees is a
        function that does not return.
        """
        left = _RETRY_AFTER_CAP - slept
        if left <= 0:
            return None
        return min(self._backoff(attempt, resp), left)

    @staticmethod
    def _unreached(
        cls: type[NodusError], message: str, idempotency_key: str | None
    ) -> NodusError:
        """A failure that leaves the caller unable to say what happened.

        The request may have arrived, in which case the workload exists and is
        billing. The key travels on the error so a retry can be the same
        submission instead of a second paid one.
        """
        if not idempotency_key:
            return cls(message)
        return cls(
            f"{message}\nIt may still have reached the control plane, in which case "
            "the workload exists and is billing. Retry with "
            f"idempotency_key={idempotency_key!r} so the retry is the same "
            "submission rather than a second paid one.",
            body={"idempotency_key": idempotency_key},
        )

    @staticmethod
    def _one(body: Any, method: str, path: str) -> dict[str, Any]:
        """A read that must have answered with an object.

        An empty body absorbed as ``{}`` leaves a handle with no status, which
        no wait can ever see finish.
        """
        if not isinstance(body, dict) or not body:
            raise NodusError(f"{method} {path} returned an empty body", body=body)
        return body


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
        headers_out: dict[str, str] | None = None,
    ) -> Any:
        headers = {"Idempotency-Key": _valid_idempotency_key(idempotency_key)} if idempotency_key else {}
        attempt = 0
        # One waiting budget for the whole call, so retries cannot multiply the
        # ceiling into a wait the caller reads as a hang.
        slept = 0.0
        while True:
            delay: float | None = None
            try:
                resp = self._http.request(
                    method, path, json=json, headers=headers, params=params
                )
            except httpx.TimeoutException as exc:
                if attempt < self.max_retries:
                    delay = self._hold(attempt, None, slept)
                if delay is not None:
                    time.sleep(delay)
                    slept += delay
                    attempt += 1
                    continue
                raise self._unreached(
                    APITimeoutError, f"{method} {path} timed out", idempotency_key
                ) from exc
            except httpx.HTTPError as exc:
                if attempt < self.max_retries:
                    delay = self._hold(attempt, None, slept)
                if delay is not None:
                    time.sleep(delay)
                    slept += delay
                    attempt += 1
                    continue
                raise self._unreached(
                    APIConnectionError,
                    f"{method} {path} failed to connect: {exc}",
                    idempotency_key,
                ) from exc

            if resp.status_code >= 400:
                if self._should_retry(method, resp.status_code, attempt):
                    delay = self._hold(attempt, resp, slept)
                    if delay is not None:
                        time.sleep(delay)
                        slept += delay
                        attempt += 1
                        continue
                self._raise(method, path, resp)

            if headers_out is not None:
                headers_out.update(resp.headers)
            if not resp.content:
                return "" if text else None
            # The log endpoint answers text/plain, because its whole purpose is
            # to be read. Everything else is JSON.
            return resp.text if text else resp.json()

    # -- workloads ---------------------------------------------------------

    def run(
        self,
        *,
        command: list[str] | str | None = None,
        image: str | None = None,
        model: str | None = None,
        peak_memory_gb: float | None = None,
        expected_runtime_hours: float | None = None,
        budget: float | None = None,
        compute_class: ComputeClass | str | None = None,
        continuity: ContinuityMode | str | dict[str, Any] | None = None,
        interrupt_tolerance: InterruptTolerance | str | None = None,
        finish_by: datetime | str | None = None,
        data_regions: list[str] | None = None,
        env: dict[str, str] | None = None,
        inputs: list[dict[str, Any]] | dict[str, Any] | None = None,
        stages: list[dict[str, Any]] | None = None,
        framework: str | None = None,
        policy: dict[str, Any] | None = None,
        requirements: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
        extra: dict[str, Any] | None = None,
        **unknown: Any,
    ) -> "Workload":
        """Submit a brief — requirements and outcomes, never a machine.

        Returns as soon as the workload is accepted; it is not yet placed. Call
        ``client.wait(wl.id)`` to block until it reaches a terminal state.

        ``budget`` is cost to completion in USD, and omitting it leaves the run
        capped only by the account. ``finish_by`` takes a datetime or RFC3339
        text. ``extra`` is merged into the payload for a field the control plane
        models and this SDK version does not; any other keyword is refused
        rather than sent, because the server drops what it does not recognise.

        ``idempotency_key`` defaults to a fresh value per call, which covers
        this call's own retries and nothing beyond it. To make an
        application-level retry loop safe, pass a key derived from the thing you
        are running so a resubmission cannot become a second paid workload.

        A raised :class:`APITimeoutError` or :class:`APIConnectionError` does
        not mean nothing was submitted. Retry with the key on ``err.payload`` —
        the one that was sent — so the retry cannot become a second paid run.
        """
        payload = build_payload(
            command=command,
            image=image,
            model=model,
            peak_memory_gb=peak_memory_gb,
            expected_runtime_hours=expected_runtime_hours,
            budget=budget,
            compute_class=compute_class,
            continuity=continuity,
            interrupt_tolerance=interrupt_tolerance,
            finish_by=finish_by,
            data_regions=data_regions,
            env=env,
            inputs=inputs,
            stages=stages,
            framework=framework,
            policy=policy,
            requirements=requirements,
            extra=extra,
            **unknown,
        )
        answered: dict[str, str] = {}
        res = self._request(
            "POST",
            "/v1/workloads",
            json=payload,
            idempotency_key=idempotency_key or f"nodus-{uuid.uuid4()}",
            headers_out=answered,
        )
        wl = Workload(self)
        wl._absorb(res or {})
        wl.replayed = _was_replayed(answered)
        if not wl.id:
            raise NodusError("submit returned no workload id", body=res)
        return wl

    def get(self, workload_id: str) -> "Workload":
        path = f"/v1/workloads/{_valid_id(workload_id)}"
        wl = Workload(self)
        wl._absorb(self._one(self._request("GET", path), "GET", path))
        return wl

    def list(
        self, *, limit: int = 50, offset: int = 0, status: Any = None
    ) -> list["Workload"]:
        """The first page only — use ``iter_workloads()`` for all of them.

        Newest first. ``status`` accepts a member, a wire string, a list of
        either, or the presets ``"active"`` and ``"terminal"``.
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
        """Page lazily so a long history never has to be held in memory.

        Stops when the next offset does not advance: an offset that repeats is
        a stall, and following it re-reads the same page forever.
        """
        offset = 0
        while True:
            page, nxt = self.list_page(limit=page_size, offset=offset, status=status)
            for wl in page:
                yield wl
            if nxt is None or nxt <= offset:
                return
            offset = nxt

    def cancel(self, workload_id: str, *, idempotency_key: str | None = None) -> None:
        self._request(
            "POST",
            f"/v1/workloads/{_valid_id(workload_id)}/cancel",
            idempotency_key=idempotency_key or f"cancel-{workload_id}",
        )

    def events(self, workload_id: str, *, after: int = 0) -> list[Event]:
        """One page of lifecycle events, oldest first.

        The server returns at most 100 and does not say whether more follow.
        Pass ``after=`` the last ``seq`` you saw, or use ``iter_events()``.
        """
        res = self._request("GET", f"/v1/workloads/{_valid_id(workload_id)}/events", params={"after": after})
        return [Event.from_dict(e) for e in (res or {}).get("events") or []]

    def iter_events(self, workload_id: str, *, after: int = 0) -> Iterator[Event]:
        """Every event recorded so far, walking past the server's page cap."""
        while True:
            batch = self.events(workload_id, after=after)
            if not batch:
                return
            for ev in batch:
                after = max(after, ev.seq)
                yield ev

    def artifacts(self, workload_id: str) -> list[Artifact]:
        res = self._request("GET", f"/v1/workloads/{_valid_id(workload_id)}/artifacts")
        return [Artifact.from_dict(a) for a in (res or {}).get("artifacts") or []]

    def ledger(self, workload_id: str) -> Ledger:
        return Ledger.from_dict(self._request("GET", f"/v1/workloads/{_valid_id(workload_id)}/ledger"))

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
        # `is not None`, so an explicit generation=0 is sent and refused rather
        # than read as "no filter": a caller that computed 0 has a bug.
        if generation is not None:
            params["generation"] = generation
        return self._request(
            "GET", f"/v1/workloads/{_valid_id(workload_id)}/logs", params=params or None, text=True
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
                # Whether it is over is only worth a request when the events ran
                # out: a stream that runs for hours would otherwise double its
                # load to ask a question the next batch answers anyway.
                if not batch and self.get(workload_id).is_terminal:
                    return
            except NodusError as exc:
                time.sleep(policy.failed(exc))
                continue
            for ev in batch:
                after = max(after, ev.seq)
                yield ev
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
        path = f"/v1/workloads/{_valid_id(self.id)}"
        self._absorb(self._client._one(self._client._request("GET", path), "GET", path))
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

    def iter_events(self, *, after: int = 0) -> Iterator[Event]:
        return self._client.iter_events(self.id, after=after)

    def stream_events(self, *, poll_seconds: float = 2.0) -> Iterator[Event]:
        return self._client.stream_events(self.id, poll_seconds=poll_seconds)

    def artifacts(self) -> list[Artifact]:
        return self._client.artifacts(self.id)

    def ledger(self) -> Ledger:
        return self._client.ledger(self.id)

    def logs(self, *, stage: str | None = None, generation: int | None = None) -> str:
        """This workload's log. See :meth:`Client.logs`."""
        return self._client.logs(self.id, stage=stage, generation=generation)

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
        text: bool = False,
        headers_out: dict[str, str] | None = None,
    ) -> Any:
        headers = {"Idempotency-Key": _valid_idempotency_key(idempotency_key)} if idempotency_key else {}
        attempt = 0
        # One waiting budget for the whole call, so retries cannot multiply the
        # ceiling into a wait the caller reads as a hang.
        slept = 0.0
        while True:
            delay: float | None = None
            try:
                resp = await self._http.request(
                    method, path, json=json, headers=headers, params=params
                )
            except httpx.TimeoutException as exc:
                if attempt < self.max_retries:
                    delay = self._hold(attempt, None, slept)
                if delay is not None:
                    await asyncio.sleep(delay)
                    slept += delay
                    attempt += 1
                    continue
                raise self._unreached(
                    APITimeoutError, f"{method} {path} timed out", idempotency_key
                ) from exc
            except httpx.HTTPError as exc:
                if attempt < self.max_retries:
                    delay = self._hold(attempt, None, slept)
                if delay is not None:
                    await asyncio.sleep(delay)
                    slept += delay
                    attempt += 1
                    continue
                raise self._unreached(
                    APIConnectionError,
                    f"{method} {path} failed to connect: {exc}",
                    idempotency_key,
                ) from exc

            if resp.status_code >= 400:
                if self._should_retry(method, resp.status_code, attempt):
                    delay = self._hold(attempt, resp, slept)
                    if delay is not None:
                        await asyncio.sleep(delay)
                        slept += delay
                        attempt += 1
                        continue
                self._raise(method, path, resp)

            if headers_out is not None:
                headers_out.update(resp.headers)
            if not resp.content:
                return "" if text else None
            # The log endpoint answers text/plain, because its whole purpose is
            # to be read. Everything else is JSON.
            return resp.text if text else resp.json()

    async def run(
        self,
        *,
        command: list[str] | str | None = None,
        image: str | None = None,
        model: str | None = None,
        peak_memory_gb: float | None = None,
        expected_runtime_hours: float | None = None,
        budget: float | None = None,
        compute_class: ComputeClass | str | None = None,
        continuity: ContinuityMode | str | dict[str, Any] | None = None,
        interrupt_tolerance: InterruptTolerance | str | None = None,
        finish_by: datetime | str | None = None,
        data_regions: list[str] | None = None,
        env: dict[str, str] | None = None,
        inputs: list[dict[str, Any]] | dict[str, Any] | None = None,
        stages: list[dict[str, Any]] | None = None,
        framework: str | None = None,
        policy: dict[str, Any] | None = None,
        requirements: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
        extra: dict[str, Any] | None = None,
        **unknown: Any,
    ) -> "AsyncWorkload":
        """Submit a brief. Same arguments and same meaning as :meth:`Client.run`."""
        payload = build_payload(
            command=command,
            image=image,
            model=model,
            peak_memory_gb=peak_memory_gb,
            expected_runtime_hours=expected_runtime_hours,
            budget=budget,
            compute_class=compute_class,
            continuity=continuity,
            interrupt_tolerance=interrupt_tolerance,
            finish_by=finish_by,
            data_regions=data_regions,
            env=env,
            inputs=inputs,
            stages=stages,
            framework=framework,
            policy=policy,
            requirements=requirements,
            extra=extra,
            **unknown,
        )
        answered: dict[str, str] = {}
        res = await self._request(
            "POST",
            "/v1/workloads",
            json=payload,
            idempotency_key=idempotency_key or f"nodus-{uuid.uuid4()}",
            headers_out=answered,
        )
        wl = AsyncWorkload(self)
        wl._absorb(res or {})
        wl.replayed = _was_replayed(answered)
        if not wl.id:
            raise NodusError("submit returned no workload id", body=res)
        return wl

    async def get(self, workload_id: str) -> "AsyncWorkload":
        path = f"/v1/workloads/{_valid_id(workload_id)}"
        wl = AsyncWorkload(self)
        wl._absorb(self._one(await self._request("GET", path), "GET", path))
        return wl

    async def list(
        self, *, limit: int = 50, offset: int = 0, status: Any = None
    ) -> list["AsyncWorkload"]:
        """The first page only — use ``iter_workloads()`` for all of them.

        Newest first. ``status`` accepts a member, a wire string, a list of
        either, or the presets ``"active"`` and ``"terminal"``.
        """
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
        """Page lazily so a long history never has to be held in memory.

        Stops when the next offset does not advance: an offset that repeats is
        a stall, and following it re-reads the same page forever.
        """
        offset = 0
        while True:
            page, nxt = await self.list_page(limit=page_size, offset=offset, status=status)
            for wl in page:
                yield wl
            if nxt is None or nxt <= offset:
                return
            offset = nxt

    async def cancel(self, workload_id: str, *, idempotency_key: str | None = None) -> None:
        await self._request(
            "POST",
            f"/v1/workloads/{_valid_id(workload_id)}/cancel",
            idempotency_key=idempotency_key or f"cancel-{workload_id}",
        )

    async def events(self, workload_id: str, *, after: int = 0) -> list[Event]:
        """One page of lifecycle events, oldest first.

        The server returns at most 100 and does not say whether more follow.
        Pass ``after=`` the last ``seq`` you saw, or use ``iter_events()``.
        """
        res = await self._request(
            "GET", f"/v1/workloads/{_valid_id(workload_id)}/events", params={"after": after}
        )
        return [Event.from_dict(e) for e in (res or {}).get("events") or []]

    async def iter_events(self, workload_id: str, *, after: int = 0) -> AsyncIterator[Event]:
        """Every event recorded so far, walking past the server's page cap."""
        while True:
            batch = await self.events(workload_id, after=after)
            if not batch:
                return
            for ev in batch:
                after = max(after, ev.seq)
                yield ev

    async def artifacts(self, workload_id: str) -> list[Artifact]:
        res = await self._request("GET", f"/v1/workloads/{_valid_id(workload_id)}/artifacts")
        return [Artifact.from_dict(a) for a in (res or {}).get("artifacts") or []]

    async def ledger(self, workload_id: str) -> Ledger:
        return Ledger.from_dict(await self._request("GET", f"/v1/workloads/{_valid_id(workload_id)}/ledger"))

    async def logs(
        self, workload_id: str, *, stage: str | None = None, generation: int | None = None
    ) -> str:
        """What the submitted program printed. See :meth:`Client.logs`."""
        params: dict[str, Any] = {}
        if stage:
            params["stage"] = stage
        if generation is not None:
            params["generation"] = generation
        return await self._request(
            "GET", f"/v1/workloads/{_valid_id(workload_id)}/logs", params=params or None, text=True
        ) or ""

    async def wait(
        self,
        workload_id: str,
        *,
        poll_seconds: float = 2.0,
        timeout_seconds: float | None = None,
    ) -> "AsyncWorkload":
        """Poll until the workload is terminal. Same policy as :meth:`Client.wait`."""
        policy = _WaitPolicy(poll_seconds, timeout_seconds)
        while True:
            try:
                wl = await self.get(workload_id)
            except NodusError as exc:
                delay = policy.failed(exc)
            else:
                if wl.is_terminal:
                    return wl
                delay = policy.polled()
            await asyncio.sleep(policy.hold(delay, workload_id))

    async def stream_events(
        self, workload_id: str, *, poll_seconds: float = 2.0
    ) -> AsyncIterator[Event]:
        """Yield events until the terminal one. Same policy as :meth:`Client.stream_events`."""
        after = 0
        policy = _WaitPolicy(poll_seconds)
        while True:
            try:
                batch = await self.events(workload_id, after=after)
                if not batch and (await self.get(workload_id)).is_terminal:
                    return
            except NodusError as exc:
                await asyncio.sleep(policy.failed(exc))
                continue
            for ev in batch:
                after = max(after, ev.seq)
                yield ev
            await asyncio.sleep(policy.polled())

    # -- webhooks / health -------------------------------------------------

    async def set_webhook(self, url: str, *, secret: str | None = None) -> dict[str, Any]:
        body: dict[str, Any] = {"url": url}
        if secret:
            body["secret"] = secret
        return await self._request("PUT", "/v1/webhooks", json=body) or {}

    async def get_webhook(self) -> dict[str, Any]:
        return await self._request("GET", "/v1/webhooks") or {}

    async def delete_webhook(self) -> None:
        await self._request("DELETE", "/v1/webhooks")

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
        path = f"/v1/workloads/{_valid_id(self.id)}"
        self._absorb(self._client._one(await self._client._request("GET", path), "GET", path))
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

    def iter_events(self, *, after: int = 0) -> AsyncIterator[Event]:
        return self._client.iter_events(self.id, after=after)

    def stream_events(self, *, poll_seconds: float = 2.0) -> AsyncIterator[Event]:
        return self._client.stream_events(self.id, poll_seconds=poll_seconds)

    async def artifacts(self) -> list[Artifact]:
        return await self._client.artifacts(self.id)

    async def ledger(self) -> Ledger:
        return await self._client.ledger(self.id)

    async def logs(self, *, stage: str | None = None, generation: int | None = None) -> str:
        """This workload's log. See :meth:`Client.logs`."""
        return await self._client.logs(self.id, stage=stage, generation=generation)

    async def cancel(self) -> None:
        await self._client.cancel(self.id)
