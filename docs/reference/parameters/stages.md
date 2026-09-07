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
| `outputs` | Mapping from logical name to relative output path | Empty. Files must actually be produced |
| `requirements` | Requirements dictionary | Zero/empty fields inherit workload-level values |
| `continuity` | `{mode: str, resume_on_interruption: bool}` | Missing/empty mode inherits the whole workload continuity object |
| `total_units` | Integer progress-unit count | Zero. Describes units for compatible restartable work |

A stage-specific nonempty `continuity.mode` does not receive the SDK top-level
resume default: provide `resume_on_interruption` explicitly. Setting only that
flag without a mode does not override the inherited object.

| Input field | Meaning |
|---|---|
| `name` | Local logical input name |
| `from_stage` | Upstream stage ID. Also include it in `depends_on` |
| `from_output` | Declared output name on the upstream stage |

Always declare producer outputs and complete input references so a typo can be
caught before execution. Output paths are relative to the runner's working
directory. Runtime integrations expose resolved inputs through `NODUS_INPUT_<name>`.
The value is a local file path, not the original storage URI.

Stage requirements support the same six fields as workload requirements:
`model`, `compute_class`, `dataset_bytes`,
`peak_memory_gb`, and `notes`. See [resources](requirements.md) and the complete
[multi-stage example](../../guides/multi-stage-workloads.md).
