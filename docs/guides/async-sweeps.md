# Concurrent experiments

`AsyncClient` has the same operations as `Client`. Await ordinary methods; use
`async for` with iterator/event-stream methods. Bound concurrent submission and
polling to avoid an unbounded number of API calls.

Run [`examples/async_sweep.py`](../../examples/async_sweep.py):

```bash
python examples/async_sweep.py --run-id experiment-001 --budget-per-run 5
```

This submits three self-contained workloads and permits two active tasks at a
time. Each receives its own budget. The total experiment can therefore consume
up to three workload budgets, subject to account limits. The semaphore is a
client scheduling limit, not a server-side aggregate budget.

Reuse the same `--run-id` only to retry the identical experiment. A new experiment
needs a new ID; changing payloads under old keys causes an idempotency conflict.
Stopping the local process does not cancel submitted work.
