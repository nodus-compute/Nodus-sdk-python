# Multi-stage workloads and final outputs

Use an explicit stage list when work has dependencies or publishes downloadable
outputs. Even one stage can declare an output. Multiple stages can reference
those names without sharing a machine or filesystem.

Run the complete example:

```bash
python examples/multi_stage.py --submission-id pipeline-001 --budget 10
```

The first stage writes `numbers.json`. The second reads the resolved upstream
input from `NODUS_INPUT_numbers`, writes `result.json`, and declares it as
`result`. After successful completion, the example downloads that output to
`results/result.json`. Both commands use the Python standard library, and each
stage explicitly requests VM compute with restartable continuity.

This example requires the deployment's runner to support declared outputs and
resolved input environment variables. It illustrates the runtime contract. It
is not a promise that every deployed runner revision supports every feature.

See [`examples/multi_stage.py`](../../examples/multi_stage.py) for the code and
[every stage field](../reference/parameters/stages.md) for inheritance and rules.
No top-level `image` or `command` is passed when stages provide their own sources.
