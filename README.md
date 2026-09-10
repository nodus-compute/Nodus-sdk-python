<div align="center">

# Nodus Python SDK

**One interface for running AI workloads on GPUs.**

[![PyPI version](https://img.shields.io/pypi/v/nodus-compute)](https://pypi.org/project/nodus-compute/)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)](https://github.com/nodus-compute/Nodus-sdk-python/blob/main/pyproject.toml)
[![License: Apache 2.0](https://img.shields.io/badge/license-Apache%202.0-blue)](https://github.com/nodus-compute/Nodus-sdk-python/blob/main/LICENSE)

[Documentation](https://nodus-compute.ai/docs/) · [Parameter reference](https://nodus-compute.ai/docs/reference/parameters/) · [Examples](https://github.com/nodus-compute/Nodus-sdk-python/blob/main/examples/README.md) · [Issues](https://github.com/Nodus-compute/Nodus-sdk-python/issues)

</div>

Run training, fine-tuning, and batch experiments when your local machine lacks
the GPU memory or capacity they need. Provide your container image and command,
then use one Python client to submit work, follow progress, and retrieve results.
Nodus matches the workload to available GPU capacity. Add a budget to set a
workload spending limit.

## 1. Install and sign in

```bash
pip install nodus-compute
nodus login
```

Get [nodus-compute on PyPI](https://pypi.org/project/nodus-compute/).
Requires Python 3.10 or newer. Upgrading an existing installation? Use
`pip install --upgrade nodus-compute`. These docs cover SDK 0.3.x.

Your browser opens Nodus sign-in. Sign in and approve the code matching your
terminal. You can then close the tab. The terminal finishes automatically and
saves your credentials. Python clients use that login without extra setup.

Running `nodus login` again reuses a valid login. Use `nodus login --force` for
a fresh sign-in.

For a machine without a browser, use `nodus login --no-browser`.
See [authentication](https://nodus-compute.ai/docs/getting-started/authentication/) for API keys and
custom deployments.

Before starting a workload, open [Billing](https://console.nodus-compute.ai/?view=billing)
and add a payment method. New accounts start with $30 in credits, but a card is
required to use them. Adding a card does not purchase credits. If you joined
a shared workspace, its administrator manages the payment method.

## 2. Run your first workload

This GPU smoke test prints the available GPU name. No local script is uploaded.
It submits paid compute with a $5 workload budget. Available capacity and account
limits still determine admission.

Save this as `first_workload.py`:

<!-- test: first-workload -->
```python
import nodus

with nodus.Client() as client:
    workload = client.run(
        image="pytorch/pytorch:2.8.0-cuda12.9-cudnn9-runtime",
        command=[
            "python", "-c",
            "import torch\n"
            "assert torch.cuda.is_available()\n"
            "print(torch.cuda.get_device_name(0))",
        ],
        budget=5,
    )
    print("Workload:", workload.id)
    done = workload.wait()
    print(done.status, done.cost_now_usd)
    if not done.succeeded:
        raise RuntimeError(f"Workload {done.id} ended: {done.status}")
    print(done.logs())
```

Run it with `python first_workload.py`. It prints the workload ID and shows live logs, lifecycle events, and elapsed
time while waiting. Training workloads also show reported steps or epochs.
The final output includes status, current cost, and GPU name.

`run()` accepts the workload. `wait()` waits for a terminal status, so check
`succeeded` before using results. Ctrl+C while waiting requests cancellation
and remote resource cleanup.

The script prints the GPU name from the workload logs. For files produced by
your own program, see [logs and results](https://nodus-compute.ai/docs/guides/monitoring-and-outputs/).

## Prefer the terminal?

```bash
nodus init
nodus run
```

`init` creates `nodus.toml` with the GPU smoke test and a $5 budget. Review the
file, then `run` submits it and waits for completion. Edit the image, command,
and budget to run your own workload. See [workload files](https://nodus-compute.ai/docs/getting-started/workload-files/).

```bash
nodus status WORKLOAD_ID
nodus logs WORKLOAD_ID
nodus cancel WORKLOAD_ID
```

## Choose an optional preference

Set `optimization="lowest_cost"`, `"lower_cost"`, `"balanced"`, `"faster"`, or
`"fastest"`. The default is balanced. Nodus balances expected completion cost
and completion time when qualified estimates are available, using price and
GPU performance signals otherwise. Preferences do not guarantee total cost or
runtime. Older deployments may record the preference without applying it.

Set `gpu="H100"` to require a GPU model, or omit it to let Nodus choose.
No runtime estimate is needed. See [resource options](https://nodus-compute.ai/docs/reference/parameters/requirements/).

GPU enforcement, live logs, login verification, and spending limits require a
compatible Nodus backend. Installing the SDK alone does not enable these server
features. See [backend compatibility](https://github.com/nodus-compute/Nodus-sdk-python/blob/main/docs/operations/errors.md#backend-compatibility) before relying on them with a custom or older deployment.

## Run your own code

Upload your script with `client.assets.upload()` or package it in a container.
Choose an image with your dependencies and pass its command to `client.run()`.

- [Run a Python script](https://nodus-compute.ai/docs/guides/containers-and-scripts/)
- [Attach code and datasets](https://nodus-compute.ai/docs/guides/assets/)
- [Train or fine-tune a model](https://nodus-compute.ai/docs/guides/gpu-workloads/)
- [Read logs and download results](https://nodus-compute.ai/docs/guides/monitoring-and-outputs/)
- [Use Nodus with a coding agent](https://nodus-compute.ai/docs/guides/agents/)

For individual options, use the [Python reference](https://nodus-compute.ai/docs/reference/python/client/)
and [parameter reference](https://nodus-compute.ai/docs/reference/parameters/).
See [troubleshooting](https://nodus-compute.ai/docs/operations/errors/) if a run fails.

## Contributing

See [RELEASING.md](https://github.com/nodus-compute/Nodus-sdk-python/blob/main/RELEASING.md) for release steps. Licensed under [Apache-2.0](https://github.com/nodus-compute/Nodus-sdk-python/blob/main/LICENSE).
