# Run GPU workloads with Nodus

Run training, fine-tuning, and batch experiments that need GPU capacity beyond
your local machine. Submit your command from Python or a workload file, follow
its progress, and retrieve logs and output files through the same interface.
Nodus matches your requirements to available capacity. Optional budgets and
cost or speed preferences let you express what matters for each run.

Install [nodus-compute from PyPI](https://pypi.org/project/nodus-compute/)
with Python 3.10 or newer, then sign in to get started.

## Get started

1. [Install and sign in](getting-started/authentication.md)
2. [Run your first workload](../README.md#2-run-your-first-workload)
3. [Read logs and results](guides/monitoring-and-outputs.md)

## Run your code

- [Run a Python script](guides/containers-and-scripts.md)
- [Attach code and datasets](guides/assets.md)
- [Train or fine-tune a model](guides/gpu-workloads.md)
- [Run from a workload file](getting-started/workload-files.md)

## Go further

- [Concurrent experiments](guides/async-sweeps.md)
- [Stages and downloadable files](guides/multi-stage-workloads.md)
- [CI and safe retries](guides/ci-and-idempotency.md)

## Reference

- [Python client](reference/python/client.md)
- [Terminal commands](reference/cli.md)
- [Workload parameters](reference/parameters/index.md)
- [GPU models and resources](reference/parameters/requirements.md#gpu-model)
- [Troubleshooting](operations/errors.md)

## For coding agents

Follow the [coding agent guide](guides/agents.md) to prepare workloads, observe
their progress, and collect results. Use the [parameter reference](reference/parameters/index.md)
for supported arguments and the [OpenAPI specification](../openapi/openapi.yaml)
for HTTP schemas. Set a budget, keep the workload ID, and check `succeeded` after
waiting. An accepted workload is not necessarily a completed workload.
