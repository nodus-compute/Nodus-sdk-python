# Logs and results

Keep the workload ID returned by `client.run()`. You can use it later to check
the run from any Python process signed in to the same account.

## Wait for the result

```python
import nodus

with nodus.Client() as client:
    workload = client.get("YOUR_WORKLOAD_ID")
    done = workload.wait()
    print(done.status, done.cost_now_usd)
    if not done.succeeded:
        raise RuntimeError(f"Workload ended: {done.status}")
    print(done.logs())
```

`wait()` returns when the workload finishes, fails, or is cancelled. Check
`succeeded` before using its results. Logs become available when the runner
commits them, so they may not be available while the workload is still running.

## Download files

For workloads that declare output files, use these calls inside the client
context after the workload completes:

```python
for output in done.outputs():
    print(output.name, output.bytes)

done.download_output("result", "result.json")
```

Replace `result` with your declared output name. See
[output declarations](../reference/parameters/stages.md) when your program writes
files you want to download.

## Progress and cancellation

`workload.status` gives the last fetched status. Use `client.get(workload.id)`
to refresh it. For lifecycle updates, iterate over `workload.stream_events()`.
These events describe execution progress, not your program's stdout.

Ctrl+C while waiting requests cancellation and remote resource cleanup.
To cancel explicitly, call `client.cancel(workload.id)`. A wait timeout ends
local observation without cancelling the run.
