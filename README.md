<div align="center">

# Nodus Python SDK

**One interface for running AI workloads across compute providers.**

[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)](pyproject.toml)
[![License: Apache 2.0](https://img.shields.io/badge/license-Apache%202.0-blue)](LICENSE)

[Documentation](docs/index.md) · [Parameter reference](docs/reference/parameters/index.md) · [Examples](examples/README.md) · [Issues](https://github.com/Nodus-compute/Nodus-sdk-python/issues)

</div>

Nodus accepts a workload's code, resource requirements, budget, and recovery policy,
then manages placement and execution. Use the same Python client for a single run,
a batch of experiments, or a multi-stage pipeline.

## 1. Install and sign in

```bash
python -m pip install nodus_compute
nodus login --base-url https://YOUR_NODUS_API_HOST
```

Replace the URL with the API address provided for your Nodus deployment. Approve
the displayed code in your browser. The CLI saves credentials for subsequent
commands and Python clients. Device login requires a deployment with device
authorization enabled; API keys also work directly.

| Where you run | Authentication |
|---|---|
| Laptop | `nodus login --base-url https://YOUR_NODUS_API_HOST` |
| Headless server | Add `--no-browser`; open the displayed URL on another device |
| CI / production | Set both `NODUS_API_KEY` and `NODUS_BASE_URL` using your secret manager |
| Explicit configuration | `nodus.Client(api_key=key, base_url=url)` |

Settings resolve individually: explicit arguments → environment → saved login.
See [authentication](docs/getting-started/authentication.md) for setup and logout.

## 2. Run your first workload

This command is self-contained: it does not depend on a local script being uploaded.
It submits paid compute with a $5 workload budget; available capacity and account
limits still determine admission.

```bash
nodus run --compute-class vm --image python:3.11-slim --budget 5 --continuity restartable --wait \
  -- python -c 'print("Hello from Nodus")'
```

Or use Python (`nodus_compute` is the package name; `nodus` is the import):

<!-- test: first-workload -->
```python
import nodus

with nodus.Client() as client:
    workload = client.run(
        image="python:3.11-slim",
        command=["python", "-c", "print('Hello from Nodus')"],
        compute_class="vm",
        budget=5,
        continuity="restartable",
    )
    print("Workload:", workload.id)
    done = workload.wait()
    print(done.status, done.cost_now_usd)
    if not done.succeeded:
        raise RuntimeError(f"Workload {done.id} ended: {done.status}")
```

`run()` returns after acceptance. `wait()` returns for completed, failed, or
cancelled work; check `succeeded`. Closing the client does not cancel the workload.

## 3. Inspect and manage the run

Use the workload ID printed above:

```bash
nodus get WORKLOAD_ID
nodus events WORKLOAD_ID --follow
nodus logs WORKLOAD_ID
nodus artifacts WORKLOAD_ID
nodus explain WORKLOAD_ID
nodus ledger WORKLOAD_ID
nodus cancel WORKLOAD_ID
```

Events show lifecycle progress. Logs are committed artifacts, so they may be
unavailable before the first commit. [Monitoring and outputs](docs/guides/monitoring-and-outputs.md)
explains how to retrieve results.

## Run your own workloads

| Goal | Guide |
|---|---|
| Package and run a Python script | [Containers and scripts](docs/guides/containers-and-scripts.md) |
| Run a GPU training or fine-tuning command | [GPU workloads](docs/guides/gpu-workloads.md) |
| Submit concurrent experiments | [Async sweeps](docs/guides/async-sweeps.md) |
| Connect stages and declared outputs | [Multi-stage workloads](docs/guides/multi-stage-workloads.md) |
| Retry safely in automation | [CI and idempotency](docs/guides/ci-and-idempotency.md) |
| Choose resource, cost, and recovery settings | [All parameters](docs/reference/parameters/index.md) |

Your script, dependencies, and accessible data must be available inside the image
or fetched by your program. The SDK does not upload your working directory.
Container images need `curl`, `wget`, or `python3` for runner bootstrap.

## Reliability essentials

- Always set `budget`; omitting it leaves only the account spend cap.
- Reuse an `idempotency_key` for retries of the same logical submission.
- A wait timeout ends polling; call `cancel()` to request stopping the workload.
- Select continuity to match your application's recovery support. Choosing
  `checkpointed` alone does not make arbitrary training code resumable.

Read [reliability](docs/concepts/reliability.md), [costs](docs/concepts/costs.md),
and [errors](docs/operations/errors.md) for the detailed behavior.

## Development

```bash
python -m pip install -e '.[dev]'
python -m pytest
```

See [RELEASING.md](RELEASING.md) for release steps. Licensed under [Apache-2.0](LICENSE).
