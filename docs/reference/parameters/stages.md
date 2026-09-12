# Stage parameters

`stages` is a list of dictionaries or `nodus.StageSpec` values. A nonempty list
replaces the top-level source. Use Python or `[[stages]]` entries in a
[workload file](../../getting-started/workload-files.md). Do not combine `framework` with explicit stages: a recognized
framework takes precedence in the current compiler.

| Stage field | Type | Omission / purpose |
|---|---|---|
| `id` | Unique string, 1–64 characters | Required. Letters, digits, `_`, `-`, `.`. Cannot start with `.` or `-` |
| `source` | `{image: str, command: list[str], asset_id?: str}` | Give explicit executable argv and image. Missing image defaults to Python image server-side |
| `depends_on` | List of stage IDs | Empty. Dependency edges must be acyclic |
| `inputs` | List of input references below | Empty |
| `outputs` | Mapping from logical name to relative output path. [Name and path constraints](source.md#file-declaration-constraints) | Empty. Files must actually be produced |
| `requirements` | Requirements dictionary | Zero/empty fields inherit workload-level values |
| `continuity` | `nodus.ContinuitySpec` | Missing/empty mode inherits workload mode and resume behavior. Missing/empty `checkpoint_paths` inherits workload paths |
| `total_units` | Nonnegative integer progress-unit count | `0`. Describes units for compatible restartable work, not GPUs or replicas |

A stage-specific nonempty `continuity.mode` does not receive the SDK top-level
resume default: provide `resume_on_interruption` explicitly. Setting only that
flag without a mode does not override inherited mode and resume behavior.

A nonempty stage `checkpoint_paths` list overrides workload paths independently
of mode. New workloads default to `["state"]`. Use `["."]` to opt a stage into
preserving the whole code folder. See [checkpoint paths](continuity.md#files-saved-for-recovery).

| Input field | Meaning |
|---|---|
| `name` | Local logical input name |
| `from_stage` | Upstream stage ID. Also include it in `depends_on` |
| `from_output` | Declared output name on the upstream stage |

Always declare producer outputs and complete input references so a typo can be
caught before execution. Output paths are relative to the runner's working
directory. Runtime integrations expose resolved inputs through `NODUS_INPUT_<name>`.
The value is a local file path, not the original storage URI.

Stage requirements support `model`, `compute_class`, `dataset_bytes`,
`peak_memory_gb`, `disk_gb`, `vcpus`, `gpu`, and `notes`. The `optimization`
field remains accepted for compatibility but has no preference effect on new runs. See [resources](requirements.md) and the complete
[multi-stage example](../../guides/multi-stage-workloads.md).
