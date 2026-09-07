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
pip install nodus-compute
nodus login
```

Requires Python 3.10 or newer. Upgrading an existing installation? Use
`pip install --upgrade nodus-compute`. Browser login without a URL requires 0.1.3
or newer.

Your browser opens Nodus sign-in. Sign in and approve the code matching your
terminal. You can then close the tab. The terminal finishes automatically and
saves your credentials. Python clients use that login without extra setup.

For a machine without a browser, use `nodus login --no-browser`.
See [authentication](docs/getting-started/authentication.md) for API keys and
custom deployments.

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
        image="pytorch/pytorch:2.6.0-cuda12.4-cudnn9-runtime",
        command=[
            "python", "-c",
            "import torch\n"
            "assert torch.cuda.is_available()\n"
            "print(torch.cuda.get_device_name(0))",
        ],
        compute_class="accelerator",
        model="GPU-smoke-test",
        budget=5,
    )
    print("Workload:", workload.id)
    done = workload.wait()
    print(done.status, done.cost_now_usd)
    if not done.succeeded:
        raise RuntimeError(f"Workload {done.id} ended: {done.status}")
    print(done.logs())
```

Run it with `python first_workload.py`. It prints the workload ID, waits for
completion, then prints the status, current cost, and GPU name.

`run()` accepts the workload. `wait()` waits for a terminal status, so check
`succeeded` before using results. Ctrl+C while waiting requests cancellation
and remote resource cleanup.

The script prints the GPU name from the workload logs. For files produced by
your own program, see [logs and results](docs/guides/monitoring-and-outputs.md).

## Run your own code

Package your script and dependencies in a container image, then pass its image
and command to `client.run()`. The SDK does not upload your local files.

- [Run a Python script](docs/guides/containers-and-scripts.md)
- [Train or fine-tune a model](docs/guides/gpu-workloads.md)
- [Read logs and download results](docs/guides/monitoring-and-outputs.md)

For individual options, use the [Python reference](docs/reference/python/client.md)
and [parameter reference](docs/reference/parameters/index.md).
See [troubleshooting](docs/operations/errors.md) if a run fails.

## Contributing

See [RELEASING.md](RELEASING.md) for release steps. Licensed under [Apache-2.0](LICENSE).
