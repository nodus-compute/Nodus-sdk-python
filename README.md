# Nodus Python SDK

Run a training job on rented GPUs from Python. Nodus places the work on the
cheapest capacity that fits, checkpoints it, and resumes it if the machine is
taken back — so a fine-tune that would have died at hour six finishes.

```python
import nodus

with nodus.Client() as client:
    wl = client.run(command=["python", "train.py"], budget=20)
    print(client.wait(wl.id).status)
```

## Install

```bash
pip install nodus_compute
```

The distribution is `nodus_compute`; the import is `nodus`.
<!-- import-name decision pending -->

## Point it at your account

Two settings. Sign in at <https://nodus.run/console/>, create an API key — it is
shown once — and the console's quickstart comes with both already filled in.

```bash
export NODUS_API_KEY=nk_live_…
export NODUS_BASE_URL=https://…
```

Or pass them directly: `nodus.Client(api_key=…, base_url=…)`.

There is no built-in address. With neither setting the client raises
`ConfigurationError` before it opens a socket, naming what is missing — it does
not dial a guess and hand you a name-lookup error.

## Running work

`run()` takes a flat brief and returns a `Workload`:

```python
import nodus

with nodus.Client() as client:
    wl = client.run(
        image="python:3.11-slim",
        command=["python", "train.py", "--epochs", "3"],
        peak_memory_gb=24,          # picks capacity that fits
        expected_runtime_hours=6,   # informs the estimate
        budget=40,                  # a ceiling, not a reservation
    )

    done = client.wait(wl.id)       # polls until the run is over
    print(done.status, done.cost_now_usd)
```

Nothing is strictly required. Omit `image` and the default `python:3.11-slim`
fills in; omit `command` and the image's own entrypoint runs. Everything else
narrows the search or bounds the cost; omit `budget` and the run is uncapped —
the SDK warns rather than inventing a ceiling on your money.

**Your image must be able to fetch a small binary** — it needs `curl`, `wget`,
or `python3` on the PATH. Most ML images have one. A bare `ubuntu` image has
none of them, and a machine that cannot fetch the runner is a machine you are
billed for while it does nothing.

## Waiting

`wait()` polls until the workload is terminal and **has no deadline of its
own**. An 18-hour run is normal, and a client that gave up on one would not stop
it — the work would carry on and keep billing while your program believed it had
failed. A transient network failure does not end the wait either; only a
permanent one (a revoked key, an unknown workload) is raised.

Pass `timeout_seconds=` if you want a bound. It ends the *waiting*, not the run:
`APITimeoutError` is raised, the workload continues, and `.cancel()` is what
stops it.

## Watching it

```python
for event in client.stream_events(wl.id):
    print(event.type, event.payload)

print(client.logs(wl.id))           # the job's own stdout and stderr
print(wl.refresh().cost_now_usd)    # charged plus what is accruing right now
```

`logs()` is not a live tail. The log is a committed artifact, so it lags the
process by a checkpoint and raises `NotFoundError` until the first checkpoint
carries one — for live progress, watch the events.

## Async

`AsyncClient` mirrors `Client` method for method:

```python
async with nodus.AsyncClient() as client:
    wl = await client.run(command=["python", "train.py"])
    done = await client.wait(wl.id)
```

## Errors

Every failure is a subclass of `NodusError`, so one `except` catches the lot.
The column that matters when writing a handler is whether the condition clears
on its own — the SDK already retries the ones that do, and never retries the
ones that do not.

| Raised | When | Clears on its own? |
|---|---|---|
| `ConfigurationError` | a setting is missing, before any request | never |
| `AuthenticationError` | the key is wrong or revoked | never |
| `SignatureError` | a signed request was rejected — the key is fine, the signature is not | never |
| `ValidationError` | the brief was rejected, with the reason | never |
| `IdempotencyConflictError` | an `Idempotency-Key` was reused with a different payload | never |
| `NotFoundError` | no such workload | never |
| `BudgetExceededError` | the run would pass your account's spend cap | only if you lower the ask or raise the cap |
| `RateLimitError` | too many requests; honours `Retry-After` | yes, with time |
| `CapacityUnavailableError` | nothing in the market fits the brief | yes |
| `APIConnectionError` / `APITimeoutError` | the network, not the API | yes |
| `APIError` | any other 4xx/5xx | for 5xx, usually |

## Command line

Everything after a bare `--` is your program's own command line, passed through
untouched:

```bash
nodus run --budget 20 -- python train.py
nodus get wl_…
nodus logs wl_…
```

## Licence

Apache-2.0. See
[LICENSE](https://github.com/Nodus-compute/Nodus-sdk-python/blob/main/LICENSE).
