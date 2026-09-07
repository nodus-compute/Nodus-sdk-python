# Run GPU workloads with Nodus

Install the SDK, sign in, and run your code from Python. Nodus handles execution.

## Get started

1. [Install and sign in](getting-started/authentication.md)
2. [Run your first workload](../README.md#2-run-your-first-workload)
3. [Read logs and results](guides/monitoring-and-outputs.md)

## Run your code

- [Package a Python script](guides/containers-and-scripts.md)
- [Train or fine-tune a model](guides/gpu-workloads.md)

## Reference

- [Python client](reference/python/client.md)
- [Workload parameters](reference/parameters/index.md)
- [Troubleshooting](operations/errors.md)

## For coding agents

Start with the quickstart above. Use the [parameter reference](reference/parameters/index.md)
for supported arguments and the [OpenAPI specification](../openapi/openapi.yaml)
for HTTP schemas. Set a budget, keep the workload ID, and check `succeeded` after
waiting. An accepted workload is not necessarily a completed workload.
