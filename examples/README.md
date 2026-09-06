# Runnable examples

Authenticate first using the [setup guide](../docs/getting-started/authentication.md).
Run commands from this repository root. These examples submit paid workloads;
budgets are illustrative and do not guarantee admission or available capacity.
All programs use only standard Python plus the Nodus SDK. Submission requires a
running Nodus deployment; no local workload simulator is included.

| Example | Command | Purpose |
|---|---|---|
| [Basic](basic.py) | `python examples/basic.py --budget 5` | Self-contained VM command |
| [Async sweep](async_sweep.py) | `python examples/async_sweep.py --run-id sweep-001 --budget-per-run 5` | Three bounded concurrent submissions |
| [Multi-stage](multi_stage.py) | `python examples/multi_stage.py --submission-id pipeline-001 --budget 10` | Produce, consume, and download a declared output |
| [CI](ci_submit.py) | `python examples/ci_submit.py --submission-id build-001 --budget 5` | Stable submission key and terminal success handling |

Reuse a submission/run ID only for an identical logical request. IDs must be
unique for new work. Stopping an example locally does not cancel remote workloads.
For training code packaged in your own image, see the
[GPU guide](../docs/guides/gpu-workloads.md).
