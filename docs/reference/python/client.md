# Python client reference

`Client(api_key=None, base_url=None, timeout=30.0, max_retries=2)` pools HTTP
connections. Prefer a `with` block; otherwise call `close()`.
`AsyncClient` uses `async with` or `await aclose()` and mirrors the methods below.

| Method | Result / behavior |
|---|---|
| `run(**brief)` | Accepted `Workload`; [all parameters](../parameters/index.md) |
| `get(id)` | Refreshed `Workload` |
| `list(limit=50, offset=0, status=None)` | One page of workloads |
| `list_page(limit=50, offset=0, status=None)` | `(workloads, next_offset)` |
| `iter_workloads(page_size=50, status=None)` | Iterator over offset-based pages |
| `wait(id, poll_seconds=2.0, timeout_seconds=None)` | Terminal workload; inspect `succeeded` |
| `cancel(id, idempotency_key=None)` | Request cancellation; returns `None` |
| `events(id, after=0)` | One page of `Event` objects |
| `iter_events(id, after=0)` | Iterator over event history |
| `stream_events(id, poll_seconds=2.0)` | Poll events until terminal |
| `artifacts(id)` | List of `Artifact` manifests |
| `logs(id, stage=None, generation=None)` | Committed log text |
| `outputs(id)` | List of `Output` objects |
| `download_output(id, name, destination, stage=None)` | Verified local `Path` |
| `routing(id)` | Placement-history dictionaries ordered by stage ID and generation |
| `ledger(id)` | `Ledger` |
| `set_webhook(url, secret=None)` | Webhook configuration response dictionary |
| `get_webhook()` / `delete_webhook()` | Read configuration / remove it |
| `healthz()` / `readyz()` | Deployment health/readiness dictionaries |

Options after resource IDs are keyword-only. Status filters accept enum values,
strings, comma-separated strings, lists, `active`, or `terminal` in Python.
Unknown statuses raise `ValueError`. Pagination uses offsets; concurrent new
submissions can shift pages. It is not a consistent historical snapshot.

`Workload` offers `refresh`, `wait`, `cancel`, events, logs, artifacts, outputs,
download, routing, and ledger methods without repeating the ID. Reads and waits
on the handle mutate it in place. Useful attributes are `id`, `status`,
`succeeded`, `is_terminal`, `route`, `stages`, `meter`, `cost_now_usd`, and `raw`.
Unknown server enum values remain strings for forward compatibility.

## Models

| Type | Useful fields |
|---|---|
| `Event` | `seq`, `id`, `type`, `payload`, `created_at` |
| `StageRun` | `id`, `status`, `completed_units`, `total_units`, optional `last_loss`, `metric_rate`, `metric_step` |
| `Artifact` | `manifest_id`, `stage_id`, `generation`, `sequence`, `final`, `files`, `outputs` |
| `ManifestFile` | `uri`, `sha256`, `bytes`, `media`, `is_tar` |
| `Output` | `name`, `stage_id`, `sha256`, `bytes`, `download` |
| `Route` | `sku`, `compute_class`, `fit_class`, `region`, `memory_gb`, prices and estimated cost |
| `Meter` | `settled_usd`, `accruing_usd`, `total_now_usd`, `accruing_rate_usd_hour`, `as_of` |
| `Ledger` | `entries`, `charged_usd`, `settlement` |

`Event` has `type` and `payload`, not a `message` attribute. Output download
helpers use the authenticated API endpoint; treat returned `download` as
server metadata rather than a URL to which you should forward credentials.

## Optional typed request dictionaries

`Source`, `Requirements`, `Policy`, `ContinuitySpec`, `StageInput`, and `StageSpec`
are `TypedDict` helpers exported by `nodus`. They support autocomplete and static
analysis while producing ordinary dictionaries:

```python
import nodus

requirements = nodus.Requirements(compute_class="accelerator", peak_memory_gb=24)
source = nodus.Source(image="python:3.11-slim", command=["python", "-c", "print(1)"])
stage = nodus.StageSpec(id="example", source=source)
# Supply requirements= and stages=[stage] to client.run(..., budget=5).
```

They do not add runtime validation or defaults. Existing plain dictionaries remain
supported. Stage source commands are argv lists, not shell strings.
