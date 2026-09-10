# Preview runtime and cost

After [signing in](../getting-started/authentication.md), use `estimate` to preview
a GPU workload before deciding whether to submit it. A preview does not create,
run, or poll a workload. It does not reserve capacity or charge for execution.
It is an estimate, not a price guarantee.

## Python

The same workload arguments accepted by `run` work with `estimate`, except for
`idempotency_key`. Pass `stage_id` to preview one declared stage. Omit it for the
whole workload. The SDK sends the workload requirements and returns server
estimates without calculating prices locally.

```python
import nodus


def preview_training(client: nodus.Client) -> nodus.Estimate:
    return client.estimate(
        image="pytorch/pytorch:2.6.0-cuda12.4-cudnn9-runtime",
        command=["python", "train.py"],
        model="meta-llama/Llama-3.1-8B",
        peak_memory_gb=24,
        budget=5,
    )


with nodus.Client() as client:
    estimate = preview_training(client)
    if estimate.compute_cost_usd is None:
        for diagnostic in estimate.diagnostics:
            print(diagnostic.message, diagnostic.action)
    else:
        print(estimate.compute_cost_usd.low, estimate.compute_cost_usd.high)
```

The function above is an ordinary Python function. Any agent framework can call
it or register a wrapper using its own tool interface. No framework dependency
is needed. Decide explicitly whether to call `client.run` after reviewing the
preview. Keep submission and its idempotency key in that separate decision.
The image must contain `train.py`, or attach uploaded code using
[`source_asset_id`](assets.md).

Async applications use the same arguments and result types:

```python
import asyncio
import nodus


async def preview_file():
    async with nodus.AsyncClient() as client:
        estimate = await client.estimate_file("train.toml")
        print(estimate.status)


asyncio.run(preview_file())
```

`client.estimate_file(path="nodus.toml", stage_id=None)` validates the same
workload file as `run_file`. A file's `idempotency_key` is ignored for the preview
and preserved for later submission. The file is not modified.

## Terminal

```sh
nodus estimate
nodus estimate train.toml
nodus estimate train.toml --stage main
nodus estimate train.toml --json
```

Readable output includes seconds, compute cost in USD, expiry, and diagnostics.
Use `--json` for the server response with explicit `null` values. An unavailable
preview is a successful response with status `unavailable`, so its exit code is
0. Authentication, validation, and transport failures exit with code 2.

## Read the result

- `estimated` means all three total ranges are present.
- `partial` means some evidence is available. A whole-workload total stays
  `None` when any relevant stage lacks that metric. Inspect `stages` for the
  available stage estimates.
- `unavailable` means no numeric evidence is available. Read `reasons` and
  `diagnostics`, including each suggested `action`. Missing cost is not zero.

`execution_seconds` describes execution occupancy. `completion_seconds`
describes elapsed time to completion and can be lower than summed execution
for parallel stages. `compute_cost_usd` describes compute cost, not all possible
account charges. Every range has `low` and `high` bounds.

`valid_until` is a timezone-aware datetime or `None`. Check it before acting and
request another preview if it has expired. Versions and `provenance` identify
server evidence when available. `raw` preserves the original response,
including the distinction between omitted fields and explicit nulls.
