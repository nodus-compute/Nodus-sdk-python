# Continuity and recovery

`continuity` accepts a string, `nodus.ContinuityMode`, or dictionary.
Workload files accept a `continuity` string or a `[continuity]` table.

| Mode | Intended application behavior | Default `resume_on_interruption` |
|---|---|---|
| `checkpointed` | Restore progress from a committed checkpoint | `true` |
| `restartable` | Safely repeat unfinished work. Supported unit boundaries can preserve progress | `true` |
| `ephemeral` | Accept loss of this attempt | `false` |

Omitting continuity sends `{"mode": "checkpointed", "resume_on_interruption": true}`.
Dictionary input also defaults a missing mode to `checkpointed` and a missing
resume flag according to the mode. An explicit resume flag is retained.

```python
continuity = {"mode": "restartable", "resume_on_interruption": True}
```

These values select a recovery policy. They do not instrument arbitrary code.
A training program must write supported checkpoint state and restore it correctly.
Do not assume a default checkpoint mode guarantees lossless recovery for any
container. Confirm your framework and deployment's runner integration before a
long training run. Use restartable for short self-contained smoke tests.

`interrupt_tolerance` is not an input. The control plane derives interruption
behavior from continuity. Recovery enters a nonterminal `recovering` state.
[waiting](../../concepts/reliability.md) continues through it.
