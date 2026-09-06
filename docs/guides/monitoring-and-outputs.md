# Monitor work and retrieve results

```python
import nodus

with nodus.Client() as client:
    workload = client.get(workload_id)
    print(workload.status, workload.cost_now_usd)
    for event in workload.iter_events():
        print(event.seq, event.type, event.payload)
```

For ongoing progress, use `stream_events()` instead. Events describe lifecycle
changes. They are not a stream of your process's stdout.

## Terminal progress

`nodus run --wait` prints the workload ID immediately. In an interactive terminal,
it then shows a spinner and elapsed time while waiting. The spinner indicates
activity, not a completion percentage. Redirected output has no animation.
Use `nodus get ID` for status or `nodus events ID --follow` for lifecycle events.
Logs are retrieved separately with `nodus logs ID`.

Ctrl+C during a CLI wait or event follow requests cancellation and resource
cleanup. The CLI reports if that request cannot be confirmed. Check the saved
workload ID afterward if the network was unavailable. A wait timeout ends local
observation without cancellation.

`running` means the runner is alive and may still be preparing your image.
`completed` means all required stages and their final result commits succeeded.
Billing settlement can finish afterward. Always check `succeeded` before using
results. Stages run serially.

## Logs and artifacts

`workload.logs(stage=None, generation=None)` reads committed stdout/stderr.
It can raise `NotFoundError` before a commit includes logs. Use a stage ID or
generation to select a particular stage/attempt after recovery.

Use `outputs()` for result files. The advanced `artifacts()` method lists stored
manifest metadata rather than downloadable file contents.

## Download declared outputs

On this checkout, sync and async clients expose output download helpers. If your
installed release lacks these methods, install the updated SDK release or this
checkout before using them.

```python
from pathlib import Path

Path("results").mkdir(exist_ok=True)
for output in workload.outputs():
    print(output.stage_id, output.name, output.bytes, output.sha256)
# Choose a known declared name and your own destination filename:
path = workload.download_output("result", "results/result.json", stage="summarize")
print(path)
```

Outputs come from completed stages with declared outputs. See the
[multi-stage example](multi-stage-workloads.md) for a complete producer/consumer
flow. Specify `stage` when different stages reuse an output name. The destination
parent directory must exist. Files are streamed to a temporary file, verified
against server-provided SHA-256 (and length when provided), and atomically
replace the destination only on success. A failed download preserves an existing
destination. Transfers are not automatically retried. Rerun the download to
restart. Archives are downloaded as bytes, not automatically extracted.

## Routing and costs

`workload.route` is the selected route, or `None` before placement.
`workload.routing()` returns placement-history dictionaries ordered by stage ID and generation.
`workload.ledger()` returns billing evidence. See [costs](../concepts/costs.md)
for the difference between live cost, charges, and settlement balance.
