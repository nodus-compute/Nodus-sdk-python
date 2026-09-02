# Waiting and Reliability

## `wait()` has no deadline of its own

```python
done = client.wait(wl.id)                    # blocks until terminal
done = client.wait(wl.id, poll_seconds=5)    # default is 2.0
```

It polls until the workload reaches `COMPLETED`, `FAILED` or `CANCELLED`, and
`timeout_seconds` defaults to `None` in every spelling of it. That is
deliberate. An 18-hour run is normal, and a client that gave up on one would not
stop it — the work would carry on and keep billing while your program believed
it had failed.

Two spellings, same policy:

```python
done = client.wait(wl.id)      # returns a fresh handle
wl.wait()                      # updates wl in place, returns wl
```

## What a wait survives, and what ends it

An 18-hour run polled every two seconds is tens of thousands of requests, so a
bad patch of network in there is ordinary, not exceptional.

**Survived** (the wait widens its interval and keeps going, for as long as the
wait lasts): connection failures, client timeouts, and HTTP 408, 429, 500, 502,
503, 504. The backoff doubles from `poll_seconds` and is capped at 30 seconds,
so a finished workload is never left sitting unnoticed. A `Retry-After` from the
server is honoured, capped the same way.

**Raised immediately** (it will never come good, and spinning on it is a program
that hangs instead of failing): `AuthenticationError` from a revoked key,
`NotFoundError` for an unknown id, and every other 4xx.

This is the same rule a single request retries by, so a lone call and a long
wait never disagree about which failures clear on their own.

## Bounding the wait

```python
try:
    done = wl.wait(timeout_seconds=3600)
except nodus.APITimeoutError:
    print("still running; not cancelled")
```

`timeout_seconds` ends the **waiting**, never the run. `APITimeoutError` is
raised, the workload keeps going, and nothing is cancelled. If you meant to stop
it, that is `wl.cancel()`. The backoff will not sleep past your bound either — a
widened interval cannot overshoot the deadline you asked for.

## Watching it happen

```python
for event in client.stream_events(wl.id):
    print(event.seq, event.type)
```

`stream_events()` yields lifecycle events as they arrive and returns when the
workload is terminal. It follows the same policy as `wait()`: it outlives a
transient failure and ends on a permanent one. It has no timeout parameter —
break out of the loop if you want to stop early.

An `Event` carries `seq` (monotonic, the thing to resume from), `id`, `type`
(e.g. `"workload.accepted"`, `"workload.running"`), `payload` (a dict), and
`created_at`. There is no `message` field.

For history rather than a live feed:

```python
client.events(wl.id, after=0)        # one page, oldest first, at most 100
client.iter_events(wl.id)            # every event, walking past the page cap
```

The server returns at most 100 events per page and does not say whether more
follow, which is why `iter_events()` exists.

## When your machine is taken back

Capacity gets reclaimed. From your side that is not a failure:

- the status goes to `RECOVERING`, then back to `RUNNING`
- with the default `continuity="checkpointed"`, work resumes from the last
  committed manifest rather than from the beginning
- `wait()` rides straight through it — `RECOVERING` is not terminal
- the stage picks up a new **generation**. Artifacts and logs are per
  generation, so `wl.logs(generation=2)` and `wl.logs(generation=3)` are
  different stories about the same work
- `artifacts()` shows the manifests, with `final=True` on the one that completed
  the stage

You do not need to do anything. If you would rather not pay for durability, say
so up front with `continuity="ephemeral"` — see [The Brief](The-Brief).

## Per-request retries

Underneath the wait, each individual request retries too: up to `max_retries`
(default 2, so three attempts) on 408, 429, 500, 502, 503, 504, with a backoff
of 0.5s doubling to a cap of 8s, or the server's `Retry-After` clamped to five
minutes. `POST` is retried as well, which is safe because every submission
carries an idempotency key.

```python
client = nodus.Client(timeout=30.0, max_retries=2)   # the defaults
```

## Idempotency and safe resubmits

Every `run()` sends an `Idempotency-Key` header. By default it is a fresh UUID
per call, which covers **that call's own retries and nothing beyond it**.

If your own code retries — a `for attempt in range(3)` loop, a CI job that gets
rerun, a Kubernetes cron that fires twice — a fresh key each time means a second
paid workload. Pass a key derived from the thing you are running:

```python
wl = client.run(
    command=["python", "eval.py"],
    budget=25,
    idempotency_key="nightly-eval-2026-07-27",
)
```

Resubmitting the same brief under that key replays the original submission and
gives you back the same workload id. Resubmitting a *different* brief under it
raises `IdempotencyConflictError` (409), which is never retried: the key names
one submission, and a second payload claiming it would destroy that identity.
Resend the original brief, or mint a new key for the new intent.

`cancel()` is idempotent by default too, keyed on the workload id.

## Resuming after your process dies

A workload id is durable and independent of the process that submitted it.
Nothing is lost when your script is killed or your laptop sleeps:

```python
with nodus.Client() as client:
    done = client.wait("wl_00000001-…")   # pick the wait back up
```

Store the id, or find it again with `client.list(status="active")`. And note the
consequence: closing your client does not stop the workload. `cancel()` does.

## Threads

One `Client` per process is enough and it is safe to share across threads — the
transport pools connections. The workload handles it returns are **not**
thread-safe: `refresh()` and `wait()` mutate the handle in place. Give each
thread its own from `client.get()`.
