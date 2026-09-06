<div align="center">

# Nodus Python SDK

**One interface for running AI workloads on GPUs.**

[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)](pyproject.toml)
[![License: Apache 2.0](https://img.shields.io/badge/license-Apache%202.0-blue)](LICENSE)

[Documentation](docs/index.md) · [Parameter reference](docs/reference/parameters/index.md) · [Examples](examples/README.md) · [Issues](https://github.com/Nodus-compute/Nodus-sdk-python/issues)

</div>

Provide a container image, a command, and a budget. Nodus finds GPU capacity and
runs your workload. Use the same Python client for training, fine-tuning, or a
batch of experiments.

## 1. Install and sign in

```bash
git clone https://github.com/nodus-compute/Nodus-sdk-python.git
cd Nodus-sdk-python
python -m pip install -e .
nodus login --base-url https://YOUR_NODUS_API_HOST
```

This checkout contains version 0.1.2. Until it appears on PyPI, install from
source as shown above. After publication, install with
`python -m pip install --upgrade "nodus_compute>=0.1.2"`.

Obtain your account access and API address from your Nodus onboarding contact.
Replace the placeholder URL with that address. Approve
the displayed code in your browser. The CLI saves credentials for subsequent
commands and Python clients. Device login requires a deployment with device
authorization enabled. API keys also work directly.

| Where you run | Authentication |
|---|---|
| Laptop | `nodus login --base-url https://YOUR_NODUS_API_HOST` |
| Headless server | Add `--no-browser`. Open the displayed URL on another device |
| CI / production | Set both `NODUS_API_KEY` and `NODUS_BASE_URL` using your secret manager |
| Explicit configuration | `nodus.Client(api_key=key, base_url=url)` |

Settings resolve individually: explicit arguments → environment → saved login.
See [authentication](docs/getting-started/authentication.md) for setup and logout.

## 2. Run your first workload

This GPU smoke test prints the available GPU name. No local script is uploaded.
It submits paid compute with a $5 workload budget. Available capacity and account
limits still determine admission.

```bash
nodus run --compute-class accelerator \
  --image pytorch/pytorch:2.6.0-cuda12.4-cudnn9-runtime \
  --model GPU-smoke-test --budget 5 --wait \
  -- python -c 'print(__import__("torch").cuda.get_device_name(0))'
```

Or use Python (`nodus_compute` is the package name. `nodus` is the import):

<!-- test: first-workload -->
```python
import nodus

with nodus.Client() as client:
    workload = client.run(
        image="pytorch/pytorch:2.6.0-cuda12.4-cudnn9-runtime",
        command=["python", "-c", "import torch\nassert torch.cuda.is_available()\nprint(torch.cuda.get_device_name(0))"],
        compute_class="accelerator",
        model="GPU-smoke-test",
        budget=5,
    )
    print("Workload:", workload.id)
    done = workload.wait()
    print(done.status, done.cost_now_usd)
    if not done.succeeded:
        raise RuntimeError(f"Workload {done.id} ended: {done.status}")
```

`run()` returns after acceptance. `wait()` returns for completed, failed, or
cancelled work. Check `succeeded`. The Python example requests cancellation on
Ctrl+C. The CLI does the same while waiting. Resource cleanup happens remotely
after the cancellation request. Closing a client alone does not cancel work.

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
| Look up advanced submission options | [All parameters](docs/reference/parameters/index.md) |

Your script, dependencies, and accessible data must be available inside the image
or fetched by your program. The SDK does not upload your working directory.
Container images need `curl`, `wget`, or `python3` for runner bootstrap.

## Before submitting

Set a budget, use an image containing your code and dependencies, and keep the
returned workload ID. Reuse an `idempotency_key` when retrying the same submission.
Nodus currently supports GPU workloads. CPU-only VM provisioning is not offered.

The first example prints a GPU name in its logs. For downloadable files, declare
stage outputs as shown in [multi-stage workloads](docs/guides/multi-stage-workloads.md).
See [troubleshooting](docs/operations/errors.md) if submission or execution fails.

## Development

```bash
python -m pip install -e '.[dev]'
python -m pytest
```

See [RELEASING.md](RELEASING.md) for release steps. Licensed under [Apache-2.0](LICENSE).
