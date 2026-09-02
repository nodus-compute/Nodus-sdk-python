# Async

`nodus.AsyncClient` mirrors `nodus.Client` method for method. Same names, same
arguments, same errors, same retry and wait policy — with `await`.

```python
import asyncio
import nodus

async def main():
    async with nodus.AsyncClient() as client:
        wl = await client.run(command=["python", "train.py"], budget=20)
        done = await client.wait(wl.id)
        print(done.status, done.cost_now_usd)

asyncio.run(main())
```

## The three differences

1. `await` everything that talks to the API.
2. `async with` / `await client.aclose()` instead of `with` / `client.close()`.
   That alias is the only place the two clients are allowed to differ.
3. Anything that yields is an **async iterator**, so `async for` — and you do
   not `await` the call itself:

```python
async for event in client.stream_events(wl.id):
    print(event.seq, event.type)

async for wl in client.iter_workloads(status="active"):
    print(wl.id, wl.cost_now_usd)

async for ev in client.iter_events(wl.id):
    ...
```

`run()` returns an `AsyncWorkload`, which has exactly the attributes and methods
of `Workload` — `refresh()`, `wait()`, `events()`, `artifacts()`, `ledger()`,
`logs()`, `cancel()`, plus `is_terminal`, `succeeded`, `cost_now_usd` and the
rest. The two handle types are compared name by name in the test suite.

## A worked example: several runs at once

Submitting is where async earns its keep — a sweep of briefs goes out
concurrently instead of one round trip at a time.

```python
import asyncio
import nodus

BRIEFS = [
    {"command": ["python", "train.py", "--lr", lr], "idempotency_key": f"sweep-lr-{lr}"}
    for lr in ("1e-3", "1e-4", "1e-5")
]

async def sweep():
    async with nodus.AsyncClient() as client:
        runs = await asyncio.gather(
            *(client.run(peak_memory_gb=24, budget=40, **b) for b in BRIEFS)
        )
        print("submitted:", [wl.id for wl in runs])

        done = await asyncio.gather(*(wl.wait(poll_seconds=10) for wl in runs))
        for wl in done:
            print(wl.id, wl.status, f"${wl.cost_now_usd:.2f}")

asyncio.run(sweep())
```

Three notes on that example, all of which apply to the sync client too:

- The stable `idempotency_key` per brief is what makes rerunning the script
  safe: a second run replays the same three workloads instead of creating three
  more. See [Waiting and Reliability](Waiting-and-Reliability).
- `poll_seconds=10` rather than the default 2 — three concurrent waits at two
  seconds is three times the request rate for no extra information.
- `asyncio.gather` raises on the first exception and leaves the other waits
  running. If you want every result, pass `return_exceptions=True` and sort them
  out yourself; the workloads themselves are unaffected either way, because a
  client-side failure never cancels a run.

## Concurrency notes

One `AsyncClient` per process, shared across tasks — the transport pools
connections. Handles are the exception: `refresh()` and `wait()` mutate the
handle in place, so two tasks should not share one. Give each its own from
`await client.get(id)`.

Do not mix a `Client` into an async program. Its `wait()` uses a blocking sleep
and will stall the event loop.

## The surface guarantee

The async half is not a subset. This was once untrue — `AsyncClient` shipped
without `wait()`, `stream_events()`, `logs()` or the webhook calls, and nothing
failed, because the test that claimed to check parity called a single method.

The suite now asserts it structurally:

- every public method on `Client` exists on `AsyncClient`, and vice versa
  (`close`/`aclose` excepted)
- for every shared method, the parameter names match in order
- `Workload` and `AsyncWorkload` have the identical set of public methods, with
  matching parameters
- `run()` on both names the whole brief explicitly

So a method added to one half and forgotten on the other is a red suite rather
than something you discover in production. If you find a gap anyway,
[file an issue](https://github.com/Nodus-compute/Nodus-sdk-python/issues).
