# The Brief

`run()` takes a flat set of keyword arguments — the *brief* — and translates
them into the nested submission the API expects. Everything is keyword-only,
and everything defaults to `None`, meaning "not stated".

Nothing is strictly required. A brief with no `command` submits the image's own
entrypoint; a brief with no `image` uses the default below.

## What to run

| Parameter | Default | What it means |
|---|---|---|
| `command` | none | Argv for the program. A list is used as-is; a string is split the way a shell would (`'python train.py --name "my run"'` → four arguments). |
| `image` | `python:3.11-slim` | Container image. It must ship `curl`, `wget`, or `python3` or the run bills without ever starting — see [Getting Started](Getting-Started). |
| `framework` | none | A named framework instead of an image and command. Sent through as given. |
| `stages` | none | A list of stage dicts for a multi-stage pipeline. **This replaces the single source entirely** — give a brief either `stages=` or `image=`/`command=`, not both. Each stage's own `source.image` is checked for a fetch tool. |

There is no `env=` and no `inputs=`: the control plane does not model
environment variables or input staging yet, so `run()` refuses both keywords
with a `TypeError` naming them unsupported rather than sending fields the API
would silently throw away. Bake configuration into your image or command line,
and have your program fetch its own data — see [FAQ](FAQ).

## What it needs

| Parameter | Default | What it means |
|---|---|---|
| `model` | none | What the work is, in your words — `"7B fine-tune"`. Free text used to size the request. |
| `peak_memory_gb` | none | The most memory the job will need. Narrows placement to capacity that fits. |
| `compute_class` | none | `"accelerator"` or `"vm"` (or `nodus.ComputeClass.ACCELERATOR`). Leave it out and Nodus decides. |
| `data_regions` | none | List of regions your data is allowed to be in. Sent as `policy.data_regions`, which is where the control plane reads residency from; a caller-supplied `policy=` dict is merged with it, and an explicit `policy["data_regions"]` wins. |
| `requirements` | none | The requirements object as a raw dict, for a key the flat arguments do not spell. **Keys you set here win** — the flat arguments only fill in what you did not state. |
| `policy` | none | A policy dict, sent through as given. |

## Money and time

| Parameter | Default | What it means |
|---|---|---|
| `budget` | none — **uncapped** | Ceiling on *cost to completion* in USD. Not a reservation. |
| `expected_runtime_hours` | none | Your estimate. Informs the cost estimate; it is not a kill timer. |
| `finish_by` | none | Deadline. A `datetime` (naive is read as local time, as Python does) or RFC3339 text like `"2026-08-01T09:00:00Z"`. |

**"No budget" does not mean "a small budget".** Omitting `budget` submits a run
with no cost ceiling of its own: it is admitted against your account's monthly
spend cap alone and will bill whatever it takes to finish. The SDK warns rather
than inventing a number, because inventing one would be picking a limit on
somebody else's money. If you want that warning to be a refusal:

```python
import warnings
warnings.simplefilter("error", UserWarning)
```

A staged brief is not warned about — a pipeline assembled against the schema is
assumed to be deliberate. See [Costs](Costs) for budget vs the account cap.

## Durability

| Parameter | Default | What it means |
|---|---|---|
| `continuity` | `"checkpointed"`, resuming on interruption | `"checkpointed"` (progress is checkpointed and a replacement resumes from the last committed manifest), `"restartable"` (safe to start over), or `"ephemeral"` (losing the run is acceptable; do not pay for durability). |

There is no `interrupt_tolerance=`. The control plane derives the interruption
envelope from `continuity` rather than accepting a declared tolerance, so the
keyword is refused with a `TypeError` naming it unsupported. State how the work
survives interruption — that is the input the router actually prices.

Durability is opt-*out*, not opt-in: losing a long run to a reclaim is the
expensive failure, so the default protects you. Passing `continuity="ephemeral"`
sets `resume_on_interruption` to false; the other two set it true. Passing a
dict (`continuity={"mode": "checkpointed", …}`) is a raw passthrough — the mode
is normalised, nothing else is added, so state every field you want.

## Plumbing

| Parameter | Default | What it means |
|---|---|---|
| `idempotency_key` | a fresh UUID per call | Names this submission. See [Waiting and Reliability](Waiting-and-Reliability) before writing a retry loop. |
| `extra` | none | Merged into the payload last, at the top level, for a field the API models and this SDK version does not. |

## Typos are refused, not forwarded

The API ignores fields it does not model. So a forwarded typo does not fail —
it is *accepted and silently dropped*, and `budget_usd=400` submits a workload
with no cost ceiling at all and answers 202. The SDK refuses any keyword it does
not know, names it, and guesses what you meant:

```python
client.run(model="x", budget_usd=400)
# TypeError: unknown brief field: 'budget_usd' (did you mean 'budget'?).
#   The control plane ignores fields it does not model, so this would have been
#   submitted and silently dropped. Pass extra={...} to send a field deliberately.
```

Nothing leaves the process. Note this is a `TypeError`, not a `NodusError`, so
`except nodus.NodusError` will not catch it — which is correct, because it is a
bug in your code rather than an answer from the API.

`extra=` is the deliberate escape hatch. It is spelled out precisely so a typo
cannot use it by accident:

```python
client.run(model="x", budget=10, extra={"experimental_knob": 3})
```

Because `extra` is merged last at the top level, it can also overwrite
`requirements`, `outcome`, `continuity` or `source` wholesale. That is a sharp
edge, not a feature — prefer the named parameters.

## What reaches the wire

Flat in, nested out. Worth knowing when you read `wl.raw`:

| You pass | Lands at |
|---|---|
| `image`, `command` | `source.image`, `source.command` |
| `model`, `peak_memory_gb`, `expected_runtime_hours`, `compute_class` | inside `requirements` |
| `data_regions` | `policy.data_regions` |
| `budget` | `outcome.max_cost_usd` |
| `finish_by` | `outcome.complete_by` (RFC3339) |
| `continuity` | `continuity.mode` + `continuity.resume_on_interruption` |
| `stages`, `framework`, `policy` | top-level, as given |

Enum members and their wire strings are interchangeable everywhere:
`nodus.ContinuityMode.RESTARTABLE` and `"restartable"` produce identical bytes.
