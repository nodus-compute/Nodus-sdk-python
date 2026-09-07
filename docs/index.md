# Nodus documentation

Sign in, run your GPU workload, and retrieve the result. Nodus manages placement
and execution. These guides cover currently supported GPU workloads.

## Start here

1. [Install and authenticate](getting-started/authentication.md)
2. [Run your first workload](../README.md#2-run-your-first-workload)
3. [Read status, logs, and outputs](guides/monitoring-and-outputs.md)

## Workload guides

- [Containers and Python scripts](guides/containers-and-scripts.md)
- [GPU training and fine-tuning](guides/gpu-workloads.md)
- [Async experiments](guides/async-sweeps.md)
- [Multi-stage workloads](guides/multi-stage-workloads.md)
- [CI and safe retries](guides/ci-and-idempotency.md)

## Reference and concepts

- [Every submission parameter](reference/parameters/index.md)
- [Python client and returned objects](reference/python/client.md)
- [CLI commands and flags](reference/cli.md)
- [Lifecycle and reliability](concepts/reliability.md)
- [Budgets, live cost, and settlement](concepts/costs.md)
- [Errors and troubleshooting](operations/errors.md)

## For coding agents

Use this repository's docs for the SDK version checked out. Begin with the
[GPU workload guide](guides/gpu-workloads.md), then look up only the fields you
need in the [parameter index](reference/parameters/index.md).
The [example scripts](../examples/README.md) are complete programs with explicit
configuration requirements. Do not invent `env=`, top-level `inputs=`, local
file upload, GPU SKU selectors, or new API methods. Set a budget and persist
the workload ID and logical submission's idempotency key. Check terminal success.
A successful API response means acceptance, not completed computation.

The source of Python payload construction is [`nodus/_brief.py`](../nodus/_brief.py).
The [release-matched OpenAPI document](../openapi/openapi.yaml) defines HTTP schemas. SDK aliases
and precedence are documented here. Legacy `wiki/` pages link to these pages.
