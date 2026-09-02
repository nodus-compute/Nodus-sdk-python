# Errors

Everything the SDK raises from an API call inherits `nodus.NodusError`, so one
`except` catches all of them and nothing else:

```python
try:
    wl = client.run(command=["python", "train.py"], budget=20)
except nodus.NodusError as e:
    print(e)                 # what failed, what to do, which request
    print(e.status_code)     # 402, 429, … or None if nothing was sent
    print(e.code)            # the machine-readable code, e.g. "budget_exceeded"
    print(e.payload)         # the response body as a dict, {} if there was none
    print(e.request_id)      # give this to support
```

## Does it clear on its own?

That is the only distinction that matters when you write a handler.

| Exception | HTTP | Clears on its own? |
|---|---|---|
| `ConfigurationError` | — (nothing sent) | Never |
| `AuthenticationError` | 401 / 403 | Never |
| `SignatureError` | 401 (`invalid_signature`) | Never |
| `NotFoundError` | 404 | Never |
| `ValidationError` | 400 / 422 | Never |
| `IdempotencyConflictError` | 409 | Never |
| `BudgetExceededError` | 402 | Only if you lower the ask or raise the cap |
| `RateLimitError` | 429 | **Yes**, with time |
| `CapacityUnavailableError` | 503 | **Yes**, and sooner with a wider brief |
| `APIConnectionError` | — (never arrived) | **Yes** |
| `APITimeoutError` | — (client-side deadline) | **Yes** |
| `APIError` | any other 4xx/5xx | Yes for 408/500/502/504; no otherwise |

The SDK retries the "yes" rows for you — inside a single request, and for the
whole life of a `wait()`. See
[Waiting and Reliability](Waiting-and-Reliability). It never retries the "never"
rows, because retrying a rejected credential or a malformed brief just sends the
same thing again.

## Each one, and what to do

**`ConfigurationError`** — no API key, no base URL, or a base URL that does not
start with `http://` or `https://`. Raised before any network call, so nothing
was charged or created. *Do:* set `NODUS_API_KEY` and `NODUS_BASE_URL` from the
console — the message contains the exact lines. See
[Getting Started](Getting-Started).

**`AuthenticationError`** — the key is missing, unknown, revoked, or expired.
*Do:* issue a new key in the console. Never retry.

**`SignatureError`** — a signed request was rejected: stale timestamp, altered
body, or the wrong secret. The credential is fine, the signature is not, which
is why it is a separate class from a bad key. *Do:* check the shared secret and
your clock.

**`NotFoundError`** — no such workload for this key. Ids are scoped to your
account, so somebody else's id reads as absent rather than forbidden. Also what
`logs()` raises while nothing has been committed yet — that one is normal, not a
fault. *Do:* check the id; for logs, wait for the first checkpoint.

**`ValidationError`** — the brief was rejected. Retrying resends the same brief.
*Do:* read the message; it names the field and carries a remedy.

**`IdempotencyConflictError`** — that `Idempotency-Key` already names a
different payload. *Do:* resend the original brief, or mint a new key for the
new intent. Never retried.

**`BudgetExceededError`** — the brief would breach the spend cap on the key. It
carries `monthly_cap_usd`, `month_to_date_usd`, `estimated_cost_usd` and
`headroom_usd` as numbers. *Do:* raise the cap in the console, or lower `budget`
or `expected_runtime_hours` and resubmit against the headroom it just told you
about. See [Costs](Costs).

**`RateLimitError`** — too many requests. Carries `retry_after` (seconds, as the
server asked, unclamped) and `retry_after_header` (the header verbatim, whether
it arrived as seconds or an HTTP-date). *Do:* nothing, usually — the SDK already
honours it. Read the fields if you are pacing your own loop.

**`CapacityUnavailableError`** — no feasible route for this brief right now.
*Do:* retry, or widen the brief: drop `finish_by`, or raise `budget`. Both
admit routes the current brief excludes.

**`APIConnectionError`** — the request never reached the API. *Do:* check the
network and the base URL. Already retried a few times before you see it.

**`APITimeoutError`** — a client-side deadline elapsed: either the per-request
`timeout` or `timeout_seconds` on `wait()`. For a wait, **the workload is
unaffected and keeps running** — a client deadline is not a cancellation. *Do:*
wait again, or `cancel()` if you meant to stop it.

**`APIError`** — any other 4xx/5xx with no more specific class. *Do:* read
`status_code` and `payload`.

## What an error message looks like

Three things: what failed, what to do about it, and which request it was.

```
POST /v1/workloads failed (400): source, framework, or stages required
Give the workload something to run: image= and command=, or framework=, or stages=[...].
request id: req_0192
```

The middle line is a remedy written in the SDK's own vocabulary — it names the
`run()` parameters you would actually change, not server-side field names. The
codes that carry one are `missing_source`, `invalid_compute_class`,
`invalid_continuity_mode`, `invalid_complete_by`, `budget_exceeded`,
`capacity_unavailable` and `idempotency_conflict`.

The last line appears whenever the response carried an `X-Request-Id`. It is
also on the exception as `.request_id`. Quote it when you ask for help.

## Two things that are not `NodusError`

**`TypeError` from a mistyped brief field.** `run(budget_usd=400)` raises
`TypeError` before anything is sent. That is a bug in your code, not an answer
from the API, so it is deliberately outside the `NodusError` tree. See
[The Brief](The-Brief).

**`UserWarning` at submission time.** Two warnings fire while the brief is still
free: an image measured to ship no fetch tool, and a brief with no `budget=`.
Turn them into refusals with `warnings.simplefilter("error", UserWarning)`.

## An honest handler

```python
import time
import nodus

def submit_with_backoff(client, **brief):
    for attempt in range(5):
        try:
            return client.run(idempotency_key="my-stable-key", **brief)
        except nodus.CapacityUnavailableError:
            time.sleep(30 * (attempt + 1))          # will clear
        except nodus.RateLimitError as e:
            time.sleep(e.retry_after or 10)         # will clear
        except nodus.BudgetExceededError as e:
            raise SystemExit(f"${e.headroom_usd} left under the cap")
        except nodus.NodusError:
            raise                                   # never clears; fail loudly
    raise SystemExit("no capacity after 5 attempts")
```

The stable `idempotency_key` is what stops that loop from becoming five paid
workloads.
