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

## Files saved for recovery

New submissions save only the `state` folder by default. Write model weights,
optimizer state and training progress there and load them when your program
restarts. `NODUS_CHECKPOINT_DIR` points to this folder in the code directory.
Nodus does not search other folders for training state.

Use `checkpoint_paths` when your program saves recovery files elsewhere:

```python
continuity = {
    "mode": "checkpointed",
    "resume_on_interruption": True,
    "checkpoint_paths": ["checkpoints", "progress.json"],
}
```

Paths are relative to the code folder and are literal, not glob patterns.
Specify up to 64 paths, with at most 512 UTF-8 bytes each. Absolute paths,
parent traversal, control characters and the runner-private `.nodus` folder
are rejected. Missing paths are skipped without expanding the selection.

Omitting the workload list or passing an empty list selects `["state"]` on the
server. The SDK preserves the supplied list without resolving this default.
A stage inherits workload paths unless it supplies a nonempty list, even when
it sets its own continuity mode. Use `["."]` to explicitly preserve the whole
code folder, except runner-private files, for a workload or stage. This can
include dependencies and caches that your command placed in the code folder.
Selecting other paths does not change `NODUS_CHECKPOINT_DIR`.
Framework shortcuts apply workload checkpoint paths to each generated stage.
Use explicit stages without a framework shortcut when each stage needs its
own selection. Stage-specific checkpoint paths combined with a framework
shortcut are rejected.

Your program must load its saved state when it restarts. Files outside the
selection are not restored from the checkpoint. Recreate dependencies from
the image or command, and put rebuildable custom installations under
`$TMPDIR/runtime` to keep them outside checkpoint storage. Download caches and
temporary files already use runner-private locations.

Existing submitted workloads retain their saved checkpoint selection.

Final downloadable result files are configured separately with `outputs`.
They can be outside the checkpoint paths.

`interrupt_tolerance` is not an input. The control plane derives interruption
behavior from continuity. Recovery enters a nonterminal `recovering` state.
[waiting](../../concepts/reliability.md) continues through it.
