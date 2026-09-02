"""The device exchange behind ``nodus login``.

A browser can hold a console session and a terminal cannot, so the terminal
asks for a short code, a human approves it in the browser, and the terminal
collects an API key once. Two unauthenticated console endpoints do it:
``/v1/console/device/start`` hands back the code, ``/v1/console/device/token``
answers ``authorization_pending`` until the human acts and the key exactly once
after they do.

Field names are read as documented and no other spelling is accepted: a server
that renames one is a mismatch to fix there, not to absorb here, and a login
that quietly reads the wrong field writes a credential nobody can trace.
"""

from __future__ import annotations

import platform
import time
from dataclasses import dataclass
from typing import Any, Callable

import httpx

from . import __version__
from .errors import APIConnectionError, APITimeoutError, NodusError, error_from_response

__all__ = [
    "START_PATH",
    "TOKEN_PATH",
    "DeviceCode",
    "Credentials",
    "open_http",
    "start_device_authorization",
    "poll_for_credentials",
]

START_PATH = "/v1/console/device/start"
TOKEN_PATH = "/v1/console/device/token"

# The three answers the poll is allowed to continue or stop on. Every other
# 4xx is terminal: a code this device cannot use does not become usable.
_PENDING = 428
_SLOW_DOWN = 429
_EXPIRED = 410

_DEFAULT_INTERVAL = 2.0
# The ceiling backoff grows to, and how long someone can stare at "waiting"
# after they have already approved it in the browser.
_MAX_INTERVAL = 5.0
# A floor under whatever the server asks for: interval 0 would spend the whole
# TTL hammering the console.
_MIN_INTERVAL = 0.01

_EXPIRED_MESSAGE = (
    "the sign-in code expired before it was approved, so no key was issued "
    "and nothing was written. Run: nodus login"
)


@dataclass(frozen=True)
class DeviceCode:
    """What ``/start`` handed back: the code, where to type it, how long for."""

    device_code: str
    user_code: str
    verification_url: str
    expires_in: float
    interval: float


@dataclass(frozen=True)
class Credentials:
    """What ``/token`` released. ``tenant`` is empty when the server sent none."""

    api_key: str
    base_url: str
    tenant: str


def client_name() -> str:
    """How this login shows up in the console's key list."""
    return f"cli:{platform.node().strip() or 'unknown'}"


def open_http(base_url: str, *, timeout: float = 30.0) -> httpx.Client:
    """A transport for the two endpoints that run before there is any key."""
    return httpx.Client(
        base_url=base_url,
        timeout=timeout,
        headers={"User-Agent": f"nodus-python/{__version__}"},
    )


def _body(resp: httpx.Response) -> Any:
    try:
        return resp.json()
    except ValueError:
        return resp.text


def _mapping(resp: httpx.Response, path: str) -> dict[str, Any]:
    body = _body(resp)
    if not isinstance(body, dict):
        raise NodusError(
            f"POST {path} did not return a JSON object",
            status_code=resp.status_code,
            body=body,
        )
    return body


def _text(body: dict[str, Any], name: str, path: str) -> str:
    value = body.get(name)
    if not isinstance(value, str) or not value.strip():
        raise NodusError(
            f"POST {path} did not return {name!r}. The SDK reads the documented "
            "field names only, so a different spelling is a server change to "
            "make, not one to guess at here.",
            body=body,
        )
    return value.strip()


def _seconds(body: dict[str, Any], name: str, path: str) -> float:
    value = body.get(name)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise NodusError(
            f"POST {path} did not return {name!r} as a positive number of "
            "seconds, so there is no deadline to bound the wait by.",
            body=body,
        )
    return float(value)


def _interval(body: dict[str, Any]) -> float:
    value = body.get("interval")
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        return _DEFAULT_INTERVAL
    return min(max(float(value), _MIN_INTERVAL), _MAX_INTERVAL)


def _post(http: httpx.Client, path: str, payload: dict[str, Any]) -> httpx.Response:
    try:
        return http.post(path, json=payload)
    except httpx.HTTPError as exc:
        raise APIConnectionError(f"POST {path} failed to connect: {exc}") from exc


def start_device_authorization(http: httpx.Client) -> DeviceCode:
    """Ask for a code, and the deadline and cadence that come with it."""
    resp = _post(http, START_PATH, {"client_name": client_name()})
    if resp.status_code >= 400:
        raise error_from_response("POST", START_PATH, resp.status_code, _body(resp))
    body = _mapping(resp, START_PATH)
    return DeviceCode(
        device_code=_text(body, "device_code", START_PATH),
        user_code=_text(body, "user_code", START_PATH),
        verification_url=_text(body, "verification_url", START_PATH),
        expires_in=_seconds(body, "expires_in", START_PATH),
        interval=_interval(body),
    )


def poll_for_credentials(
    http: httpx.Client,
    device: DeviceCode,
    base_url: str,
    *,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> Credentials:
    """Wait for the human, bounded by the TTL the server set.

    ``base_url`` is the address this exchange was run against, and stands in
    when the token response carries none: it is the one thing already known to
    be true, so using it invents nothing.
    """
    deadline = monotonic() + device.expires_in
    interval = device.interval
    while True:
        if monotonic() >= deadline:
            raise APITimeoutError(_EXPIRED_MESSAGE)
        resp = _post(http, TOKEN_PATH, {"device_code": device.device_code})
        status = resp.status_code
        if status == _EXPIRED:
            raise APITimeoutError(_EXPIRED_MESSAGE, status_code=status, body=_body(resp))
        if status < 400:
            body = _mapping(resp, TOKEN_PATH)
            issued = body.get("base_url")
            tenant = body.get("tenant")
            return Credentials(
                api_key=_text(body, "api_key", TOKEN_PATH),
                base_url=issued.strip() if isinstance(issued, str) and issued.strip() else base_url,
                tenant=tenant.strip() if isinstance(tenant, str) else "",
            )
        if status == _SLOW_DOWN:
            interval = min(interval * 2, _MAX_INTERVAL)
        elif status != _PENDING:
            raise error_from_response("POST", TOKEN_PATH, status, _body(resp))
        sleep(min(interval, max(0.0, deadline - monotonic())))
        interval = min(interval * 1.5, _MAX_INTERVAL)
