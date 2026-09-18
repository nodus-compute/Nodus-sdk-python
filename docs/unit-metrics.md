# Per-unit measurements

Print one complete JSON line when your command finishes a logical unit of work:

```python
import json

print("nodus.unit_done " + json.dumps({"id": "batch-42", "ms": 125.5}), flush=True)
```

Keep each unit ID stable if recovery repeats the same work. IDs must contain at
most 128 UTF-8 bytes. Durations must be finite, nonnegative milliseconds.

Read measurements from the workload returned by the service:

```python
workload = client.get(workload_id)
metrics = workload.unit_metrics
if metrics is not None:
    print(metrics.units_completed, metrics.p50_ms, metrics.p95_ms)
    print(metrics.cost_per_unit_usd, metrics.dropped_observations)
```

Cost per unit uses posted workload charges. It can change while charges settle.
It is not a final price estimate. Missing measurements remain `None`.

Measurements arrive periodically. A host failure can lose observations that have
not reached the service. A full local queue reports dropped observations, so
these measurements do not prove that every completed unit was counted.

These lines report performance only. They do not save application state or
advance the recovery position.
