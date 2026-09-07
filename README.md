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

Run it with `python first_workload.py`. The workload ID appears after acceptance.
The final line reports the terminal status and current cost.

## 3. Read the result

Keep these calls inside the client context after `wait()`:

```python
print(done.logs())
for output in done.outputs():
    print(output.name, output.bytes)
```

The smoke test prints the GPU name in its logs. Your own workloads can also
produce downloadable files. See [monitoring and outputs](docs/guides/monitoring-and-outputs.md)
for progress and downloads, or the [CLI reference](docs/reference/cli.md) if you
prefer terminal commands.

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

## Contributing

See [RELEASING.md](RELEASING.md) for release steps. Licensed under [Apache-2.0](LICENSE).
