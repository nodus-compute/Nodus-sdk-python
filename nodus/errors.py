"""Typed exceptions for the Nodus API.

Every exception inherits :class:`NodusError`, so one ``except nodus.NodusError``
catches all of them and nothing else. The distinction that matters when writing
a handler is whether the condition can clear on its own: rate limits and
capacity pressure clear with time, budget caps clear only if you lower what you
are asking for, and authentication and validation failures never clear.
"""

from __future__ import annotations

from typing import Any

__all__ = [
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
    "error_from_response",
]


class NodusError(Exception):
    """Base class for everything the SDK raises."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        body: Any = None,
        request_id: str | None = None,
    ):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.body = body
        self.request_id = request_id

    @property
    def code(self) -> str | None:
        """The machine-readable error code the control plane returned, if any."""
        if isinstance(self.body, dict):
            value = self.body.get("error")
            if isinstance(value, str):
                return value
        return None

    @property
    def payload(self) -> dict[str, Any]:
        """Structured detail attached to the error. Empty when there is none."""
        if isinstance(self.body, dict):
            return self.body
        return {}


class ConfigurationError(NodusError):
    """Raised before any network call: no API key, malformed base URL.

    Nothing was sent, so nothing was charged or created.
    """


class AuthenticationError(NodusError):
    """401/403. The key is missing, unknown, revoked, or expired.

    Never retry — a rejected credential does not become valid on its own.
    """


class NotFoundError(NodusError):
    """404. No such workload for this tenant.

    Identifiers are tenant-scoped, so another tenant's id reads as absent
    rather than forbidden.
    """


class ValidationError(NodusError):
    """400/422. The brief was rejected. Retrying resends the same brief."""


class IdempotencyConflictError(NodusError):
    """409. The same Idempotency-Key was reused with a different payload.

    Not retryable: the key names one submission, and a second payload claiming
    it would destroy that identity. Resend the original payload, or mint a new
    key for the new intent.
    """


class RateLimitError(NodusError):
    """429. Too many requests."""

    @property
    def retry_after(self) -> float | None:
        """Seconds to wait, from the Retry-After header, when the server sent one."""
        return self._retry_after

    def __init__(self, message: str, *, retry_after: float | None = None, **kw: Any):
        super().__init__(message, **kw)
        self._retry_after = retry_after


class BudgetExceededError(NodusError):
    """402. The brief would breach a spend cap on the key.

    ``payload`` carries ``monthly_spend_cap_usd``, ``month_to_date_usd`` and
    ``estimated_cost_usd`` so a caller can compute real headroom and resubmit
    against it rather than against the ceiling it asked for.
    """


class CapacityUnavailableError(NodusError):
    """503. No feasible route for this brief right now.

    Retryable, and more likely to succeed with a wider brief: raising
    ``interrupt_tolerance`` or dropping ``finish_by`` admits routes the
    deadline excluded.
    """


class SignatureError(NodusError):
    """401 on a signed request. Stale timestamp, altered body, or wrong secret."""


class APIError(NodusError):
    """Any other 4xx/5xx with no more specific class."""


class APIConnectionError(NodusError):
    """The request never reached the control plane."""


class APITimeoutError(NodusError):
    """A client-side deadline elapsed.

    The per-request ``timeout``, or ``timeout_seconds`` on ``wait()``. For
    ``wait()`` the workload is unaffected and keeps running — a client deadline
    is not a cancellation.
    """


# Status codes whose meaning is specific enough to deserve their own class.
_BY_STATUS: dict[int, type[NodusError]] = {
    400: ValidationError,
    401: AuthenticationError,
    402: BudgetExceededError,
    403: AuthenticationError,
    404: NotFoundError,
    409: IdempotencyConflictError,
    422: ValidationError,
    429: RateLimitError,
    503: CapacityUnavailableError,
}


def error_from_response(
    method: str,
    path: str,
    status_code: int,
    body: Any,
    *,
    retry_after: float | None = None,
    request_id: str | None = None,
) -> NodusError:
    """Map an HTTP response onto the most specific exception class."""
    message = f"{method} {path} failed ({status_code})"
    if isinstance(body, dict):
        detail = body.get("message") or body.get("error")
        if isinstance(detail, str) and detail:
            message = f"{message}: {detail}"

    code = body.get("error") if isinstance(body, dict) else None

    # A signed-request rejection is a 401 like a bad key, but it means something
    # different to the caller: the credential is fine, the signature is not.
    if status_code == 401 and code in {"invalid_signature", "signature_error"}:
        cls: type[NodusError] = SignatureError
    else:
        cls = _BY_STATUS.get(status_code, APIError)

    kwargs: dict[str, Any] = {
        "status_code": status_code,
        "body": body,
        "request_id": request_id,
    }
    if cls is RateLimitError:
        return RateLimitError(message, retry_after=retry_after, **kwargs)
    return cls(message, **kwargs)
