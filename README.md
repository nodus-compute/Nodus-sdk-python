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
pip install nodus-run
```

The distribution is `nodus-run`; the import is `nodus` — the same split as
`pip install scikit-learn` and `import sklearn`.

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

Only `command` is required. Everything else narrows the search or bounds the
cost; omit `budget` and the run is uncapped.

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
    print(event.type, event.message)

print(client.logs(wl.id))           # the job's own stdout and stderr
print(wl.refresh().cost_now_usd)    # charged plus what is accruing right now
```

## Async

`AsyncClient` mirrors `Client` method for method:

```python
async with nodus.AsyncClient() as client:
    wl = await client.run(command=["python", "train.py"])
    done = await client.wait(wl.id)
```

## Errors

Every failure is a subclass of `NodusError`, so one `except` catches the lot:

| Raised | When |
|---|---|
| `ConfigurationError` | a setting is missing, before any request |
| `AuthenticationError` | the key is wrong or revoked |
| `ValidationError` | the brief was rejected, with the reason |
| `BudgetExceededError` | the run would pass your account's spend cap |
| `CapacityUnavailableError` | nothing in the market fits the brief |
| `NotFoundError` | no such workload |
| `RateLimitError` | too many requests; honours `Retry-After` |
| `APIConnectionError` / `APITimeoutError` | the network, not the API |

## Command line

```bash
nodus run --command "python train.py" --budget 20
nodus status wl_…
nodus logs wl_…
```

## Licence

Apache-2.0. See [LICENSE](./LICENSE).
