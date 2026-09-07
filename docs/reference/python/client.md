# Python client reference

`Client(api_key=None, base_url=None, timeout=30.0, max_retries=2)` pools HTTP
connections. With no arguments it uses your saved login and the hosted service.
Prefer a `with` block. Otherwise call `close()`.
`AsyncClient` uses `async with` or `await aclose()` and mirrors the methods below.

| Method | Result / behavior |
|---|---|
| `run(**brief)` | Accepted `Workload`. [all parameters](../parameters/index.md) |
| `run_file(path="nodus.toml")` | Accepted `Workload` from a [workload file](../../getting-started/workload-files.md) |
| `assets` | [Upload, import, list, and delete code or dataset assets](../../guides/assets.md) |
| `get(id)` | Refreshed `Workload` |
| `list(limit=50, offset=0, status=None)` | One page of workloads |
| `list_page(limit=50, offset=0, status=None)` | `(workloads, next_offset)` |
| `iter_workloads(page_size=50, status=None)` | Iterator over offset-based pages |
| `wait(id, poll_seconds=2.0, timeout_seconds=None, progress=None)` | Terminal workload. Inspect `succeeded` |
| `cancel(id, idempotency_key=None)` | Request cancellation. Returns `None` |
| `events(id, after=0)` | One page of `Event` objects |
| `iter_events(id, after=0)` | Iterator over event history |
| `stream_events(id, poll_seconds=2.0)` | Poll events until terminal |
| `artifacts(id)` | List of `Artifact` manifests |
| `logs(id, stage=None, generation=None)` | Committed log text |
| `live_logs(id, after="")` | Live log chunks, cursor, and truncation state |
| `outputs(id)` | List of `Output` objects |
| `download_output(id, name, destination, stage=None)` | Verified local `Path` |
| `routing(id)` | Placement-history dictionaries ordered by stage ID and generation |
| `ledger(id)` | `Ledger` |
| `set_webhook(url, secret=None)` | Webhook configuration response dictionary |
| `get_webhook()` / `delete_webhook()` | Read configuration / remove it |
| `healthz()` / `readyz()` | Deployment health/readiness dictionaries |

Options after resource IDs are keyword-only. Status filters accept enum values,
strings, comma-separated strings, lists, `active`, or `terminal` in Python.
Unknown statuses raise `ValueError`. Pagination uses offsets. Concurrent new
submissions can shift pages. It is not a consistent historical snapshot.

`Workload` offers `refresh`, `wait`, `cancel`, events, logs, artifacts, outputs,
download, routing, and ledger methods without repeating the ID. Reads and waits
on the handle mutate it in place. Useful attributes are `id`, `status`,
`succeeded`, `is_terminal`, `route`, `stages`, `meter`, `cost_now_usd`, and `raw`.
Unknown server enum values remain strings for forward compatibility.

`workload.download(destination=None)` downloads all declared customer outputs
and returns a list of local `Path` objects. The default directory is
`outputs/WORKLOAD_ID`, with each file at `STAGE/NAME`. `await workload.download()` is the asynchronous equivalent.
Use `download_output(name, destination, stage=...)` for one specific file.

## Models

| Type | Useful fields |
|---|---|
| `Event` | `seq`, `id`, `type`, `payload`, `created_at` |
| `StageRun` | `id`, `status`, `completed_units`, `total_units`, optional `last_loss`, `metric_rate`, `metric_step`, `metric_total_steps`, `metric_epoch`, `metric_total_epochs` |
| `Artifact` | `manifest_id`, `stage_id`, `generation`, `sequence`, `final`, `files`, `outputs` |
| `ManifestFile` | `uri`, `sha256`, `bytes`, `media`, `is_tar` |
| `Output` | `name`, `stage_id`, `sha256`, `bytes`, `download` |
| `Route` | `sku`, `compute_class`, `fit_class`, `region`, `memory_gb`, prices and estimated cost |
| `Meter` | `settled_usd`, `accruing_usd`, `total_now_usd`, `accruing_rate_usd_hour`, `as_of` |
| `Ledger` | `entries`, `charged_usd`, `settlement` |

`Event` has `type` and `payload`, not a `message` attribute. Output download
helpers use the authenticated API endpoint. Treat returned `download` as
server metadata rather than a URL to which you should forward credentials.

## Optional typed request dictionaries

`Source`, `Requirements`, `Policy`, `ContinuitySpec`, `StageInput`, and `StageSpec`
are `TypedDict` helpers exported by `nodus`. They support autocomplete and static
analysis while producing ordinary dictionaries:

```python
import nodus

requirements = nodus.Requirements(compute_class="accelerator", peak_memory_gb=24)
source = nodus.Source(image="pytorch/pytorch:2.6.0-cuda12.4-cudnn9-runtime", command=["python", "-c", "print(1)"])
stage = nodus.StageSpec(id="example", source=source)
# Supply requirements= and stages=[stage] to client.run(..., budget=5).
```

They do not add runtime validation or defaults. Existing plain dictionaries remain
supported. Stage source commands are argv lists, not shell strings.

## Personal and team history

Use `client.list(scope="mine")` for your submissions or `scope="team"` for the
team. Scope also works with `list_page()` and `iter_workloads()` and combines
with status filters. Personal history requires a member-associated credential.
Listed workloads expose `owner_user_id`, which can be absent for shared keys or
older submissions. Scope filters history and does not change team access.
