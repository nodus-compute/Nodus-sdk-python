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
changes; they are not a stream of your process's stdout.

## Logs and artifacts

`workload.logs(stage=None, generation=None)` reads committed stdout/stderr.
It can raise `NotFoundError` before a commit includes logs. Use a stage ID or
generation to select a particular stage/attempt after recovery.

`workload.artifacts()` lists checkpoint manifests. Each artifact has stage,
generation, sequence, and `final`, plus checkpoint `files` and named `outputs`.
These describe stored objects; an artifact itself is not a downloadable file.

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
destination. Transfers are not automatically retried; rerun the download to
restart. Archives are downloaded as bytes, not automatically extracted.

## Routing and costs

`workload.route` is the selected route, or `None` before placement.
`workload.routing()` returns placement-history dictionaries ordered by stage ID and generation.
`workload.ledger()` returns billing evidence. See [costs](../concepts/costs.md)
for the difference between live cost, charges, and settlement balance.
