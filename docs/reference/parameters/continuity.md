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
A training program needs a supported integration or its own save and restore code.
Do not assume a default checkpoint mode guarantees lossless recovery for any
container. Confirm your framework and deployment's runner integration before a
long training run. Use restartable for short self-contained smoke tests.

## Application checkpoint integration

The optional `integration` field selects application save and resume support for
source submissions. A stage can override the workload selection.

| Value | Behavior |
|---|---|
| `auto` | Attempt a supported integration. Unsupported training configurations retain the original command and declared file-saving behavior |
| `none` | Use the application's existing save and restore code |
| `hf-trainer-v1` | Require the versioned Hugging Face Trainer integration. Unsupported configurations fail rather than silently restarting |

The SDK preserves omission so the server can apply its configured policy.
Explicit `auto` and `hf-trainer-v1` require checkpointed continuity and the
dedicated `state` folder. Preparation availability depends on the deployment.

`hf-trainer-v1` accepts unmodified Transformers Trainer with PyTorch 2.7.1,
Transformers 4.57.6, Accelerate 1.12.0 and Datasets 4.4.2. Its supported profile
uses one training process and device, float32 model state, a fingerprinted map-style Hugging
Face Dataset, zero dataloader workers, the default data collator and standard
built-in callbacks. Trainer creates the optimizer and scheduler. Complete
model state must contain tensors only and fit within four billion bytes.
Mixed precision, distributed training, streaming data, PEFT, quantized models,
custom Trainer subclasses, custom loss functions and TRL require separate
qualification. The profile requires `save_only_model=False`,
`ignore_data_skip=False`, `load_best_model_at_end=False` and `push_to_hub=False`.

The integration saves complete model, optimizer, scheduler, random generator
and training-step state at an optimizer boundary. Nodus requests saves and
preserves completed versions. Recovery checks the program, dataset,
configuration, model structure, trainable parameters, device type and framework
identities before resuming. An incompatible
managed checkpoint fails even when `integration` is `auto`.

Check that your GPU environment is qualified before relying on automatic recovery.
Progress after the last committed checkpoint can still be lost.

## Files saved for recovery

New submissions save only the `state` folder by default. Write model weights,
optimizer state and training progress there and load them when your program
restarts. `NODUS_CHECKPOINT_DIR` points to this folder in the code directory.
Nodus does not search other folders for training state.

When your application manages its own state, write a complete version outside
the selected paths and publish it with an atomic rename on the same filesystem.
Do not overwrite files while Nodus may be copying them. A multi-file checkpoint
needs a complete, immutable version that remains available during capture.

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
