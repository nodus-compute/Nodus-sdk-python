# Run GPU workloads and agent sandboxes with Nodus

Run training, fine-tuning, and batch experiments that need GPU capacity beyond
your local machine. Submit your command from Python or a workload file, follow
its progress, and retrieve logs and output files through the same interface.
For interactive agents, create a durable sandbox and execute multiple commands
with streamed output and stdin.
Nodus selects the cheapest compatible on-demand capacity by full hourly price.
Set a workload budget to limit spending. Optimization tiers are not supported.

Install [nodus-compute from PyPI](https://pypi.org/project/nodus-compute/)
with Python 3.10 or newer, then sign in to get started.

## Get started

1. [Install and sign in](getting-started/authentication.md)
2. [Run your first workload](../README.md#2-run-your-first-workload)
3. [Read logs and results](guides/monitoring-and-outputs.md)

## Run your code

- [Run a Python script](guides/containers-and-scripts.md)
- [Attach code and datasets](guides/assets.md)
- [Manage external data connections](guides/connections.md)
- [Train or fine-tune a model](guides/gpu-workloads.md)
- [Run from a workload file](getting-started/workload-files.md)
- [Run an agent sandbox](guides/agent-sandboxes.md)
- [Connect MCP tools](guides/mcp.md)
- [Install Codex, Claude Code and Cursor plugins](guides/plugins.md)

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

- [Devbox preview](guides/devboxes.md) explains named development sessions and server defaults.
