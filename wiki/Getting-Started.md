# Getting Started

## Install

```bash
pip install nodus_compute
```

Python 3.10 or newer. The only dependency is `httpx`. The package ships
`py.typed`, so your type checker sees the annotations.

## Point it at your account

Two settings, and there is no built-in fallback for either:

```bash
export NODUS_API_KEY=nk_live_…
export NODUS_BASE_URL=https://…
```

Both are shown in the console at <https://nodus.run/console/>. Sign in, create
an API key — it is displayed once — and the quickstart panel on that page comes
with your key and the API address already filled in. Copy those two lines.

Or pass them straight in, which is what you want in a notebook or a test:

```python
client = nodus.Client(api_key="nk_live_…", base_url="https://…")
```

If either is missing, `nodus.Client()` raises `ConfigurationError` **before it
opens a socket**, and the message names what is missing, where to get it, and
the exact lines to run. It does not guess an address: any address the SDK could
invent is either a domain that does not resolve or an account you are not on,
and both would answer a setup mistake with a confusing network error.

Check the wiring without submitting anything:

```python
with nodus.Client() as client:
    print(client.healthz())   # {'status': 'ok'}
```

## Your first run

```python
import nodus

with nodus.Client() as client:
    wl = client.run(
        image="python:3.11-slim",
        command=["python", "train.py", "--epochs", "3"],
        peak_memory_gb=24,          # narrows the search to capacity that fits
        expected_runtime_hours=6,   # informs the cost estimate
        budget=40,                  # a ceiling on cost to completion, in USD
    )
    print(wl.id)                    # accepted — not placed yet

    done = client.wait(wl.id)       # blocks until the run is over
    print(done.status, done.cost_now_usd)
```

`run()` returns as soon as the workload is **accepted**. It is not running yet
and has no route yet. Everything after that hangs off `wl.id`.

Use one client per process — it pools connections — and keep it in a `with`
block so the connections get closed. Every parameter is covered in
[The Brief](The-Brief).

## Reading the result

`wait()` gives you a handle whose fields are already filled in; no second round
trip needed.

```python
done.status          # WorkloadStatus.COMPLETED / FAILED / CANCELLED
done.succeeded       # True only for COMPLETED
done.is_terminal     # True for all three
done.error           # the failure message, when there is one
done.cost_now_usd    # what it cost — see the Costs page
done.budget_usd      # the ceiling this run was held to
done.route.sku       # the Nodus catalog route it landed on
done.stages          # per-stage progress: completed_units / total_units
done.raw             # the whole response body, for anything not modelled yet
```

Then the things you actually came for:

```python
print(done.logs())                       # what the program printed
for art in done.artifacts():             # committed manifests
    print(art.stage_id, art.generation, art.final)
print(done.ledger().settlement.status)   # billing evidence
```

`logs()` is not a live tail — the log is collected as a committed artifact, so
it lags the process by a checkpoint and raises `NotFoundError` until the first
manifest carries one. To watch progress live, use `stream_events()`; see
[Waiting and Reliability](Waiting-and-Reliability).

## The free image check

The Nodus runner installs itself onto the rented host by fetching a small
artifact — with `curl`, then `wget`, then a stdlib `python3`. **An image with
none of those three cannot start your work, and you are billed for the host
anyway.** This is the single most expensive beginner mistake, and it costs
nothing to rule out.

The SDK warns for images it has measured and knows ship none. Measured
2026-08-29:

| Image | Fetch tool | Verdict |
|---|---|---|
| `python:3.11-slim` (the default) | `python3` | fine |
| `pytorch/pytorch:2.6.0-cuda12.4-cudnn9-runtime` | `python3` | fine |
| `ubuntu:22.04` | none | warns — will bill and never start |

An image that is not in that table is left alone rather than guessed at, so
silence is not approval. Check yours yourself, locally, for free:

```bash
docker run --rm --entrypoint sh your-image:tag \
  -c 'command -v curl || command -v wget || command -v python3 || echo NONE'
```

To make the SDK's warning a hard stop instead of a line on stderr — the warning
is raised while the brief is still free, before anything is submitted:

```python
import warnings
warnings.simplefilter("error", UserWarning)   # unbootable image now raises
```

Same switch catches the "no budget" warning. See [The Brief](The-Brief).

## Next

- [The Brief](The-Brief) — every `run()` parameter
- [Waiting and Reliability](Waiting-and-Reliability) — what `wait()` promises
- [Errors](Errors) — when something surprises you
