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
`succeeded` before using its results. Interactive waits show elapsed time, lifecycle events, live program output,
and available training progress. Saved logs remain accessible with `logs()`.

## Download files

For workloads that declare output files, use these calls inside the client
context after the workload completes:

```python
for path in done.download():
    print(path)
```

Files go into `outputs/WORKLOAD_ID/STAGE/NAME` by default, using the declared output name. Pass a directory to
`done.download("results")` to choose another location. For one file, use
`done.download_output("result", "result.json")` with your declared output name. See
[output declarations](../reference/parameters/source.md#input-and-output-files) when your program writes
files you want to download.

## Progress and cancellation

`workload.status` gives the last fetched status. Use `client.get(workload.id)`
to refresh it. For lifecycle updates, iterate over `workload.stream_events()`.
These events describe execution progress, not your program's stdout.

Ctrl+C while waiting requests cancellation and remote resource cleanup.
To cancel explicitly, call `client.cancel(workload.id)`. A wait timeout ends
local observation without cancelling the run.

## From the terminal

```bash
nodus wait WORKLOAD_ID
nodus logs WORKLOAD_ID
nodus download WORKLOAD_ID
```

Check a workload without waiting with `nodus status WORKLOAD_ID`. Stop it with
`nodus cancel WORKLOAD_ID`. Downloads include declared output files, not the
entire container filesystem.

## Live display

`wait(progress=None)` automatically enables a Rich display in an interactive
terminal on Windows, macOS, and Linux. Use `progress=True` to enable output
explicitly or `progress=False` for silent waiting. Progress goes to stderr and
does not capture your program's stdout. Redirected output has no animations.

Elapsed time refreshes every second. Server updates are polled every two
seconds by default. Recognized training output can show steps, epochs, loss,
and throughput. Percentages appear only when a matching total is reported.
For other programs, stage updates, elapsed time, and logs remain visible.

For a custom display, call `client.live_logs(workload.id, after=cursor)`. It
returns `chunks`, `next_cursor`, and `truncated`. Each chunk has a numeric ID,
stage ID, generation, and text. Pass the returned cursor on the next request.
Live capture is limited to 8 MiB per attempt, with explicit truncation. The live
view is retained for 24 hours after termination. Saved logs retain their normal
retention. Older runners show saved logs as they become available.

Live output combines stdout and stderr. Buffered programs may delay their own
output. Python output is unbuffered unless explicitly overridden.
