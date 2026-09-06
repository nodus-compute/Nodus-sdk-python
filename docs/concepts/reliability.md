# Lifecycle and reliability

A workload is a durable server resource. A typical successful run moves through
`accepted`, `planning`, `reserving`, `provisioning`, `running`, and `completed`.
Interruption can move it to `recovering` and back to `running`. `failed` and
`cancelled` are also terminal. Acceptance does not imply successful placement.

```python
done = client.wait(workload_id, poll_seconds=5, timeout_seconds=3600)
if not done.succeeded:
    raise RuntimeError(f"{done.id}: {done.status}")
```

The default poll interval is 2 seconds. The default wait has no deadline.
`timeout_seconds` sets a polling deadline and raises `APITimeoutError`; it does not cancel
remote execution. The deadline is checked between requests; an in-flight request
or its retries can exceed it. Reconnect using the saved workload ID to resume observing it.
`workload.wait()` refreshes that handle in place; `client.wait(id)` returns a
fresh handle. Do not share a mutable workload handle between threads.

## Retry behavior

Ordinary API requests default to `timeout=30.0` seconds and `max_retries=2`
(three total attempts). The SDK retries connection failures, request timeouts,
and HTTP 408, 429, 500, 502, 503, and 504. Request backoff starts at 0.5 seconds,
doubles up to 8 seconds, and honors a bounded `Retry-After`.

Output downloads stream separately and do not automatically retry; retry the
download call to restart a failed transfer.

Long waits and event streams also survive transient errors, with polling backoff
capped at 30 seconds. Authentication, validation, and other permanent errors
propagate. `stream_events()` has no timeout parameter; stop iteration to stop
watching. Repeated requests still consume API capacity; use longer polling
intervals for large fleets.

Events have a monotonic `seq`; `events(after=seq)` reads a page and
`iter_events(after=seq)` walks subsequent pages. After a reclaim, a stage's new
generation distinguishes its new attempt from older artifacts and logs.
Checkpointed recovery requires compatible checkpoint production and restoration;
see [continuity](../reference/parameters/continuity.md).

See [safe submission retries](../guides/ci-and-idempotency.md) before building
an application-level retry loop.
