# Container image and command

`image` chooses the container environment where your code runs. A container
image packages the runtime, system libraries, and installed dependencies. For example,
`pytorch/pytorch:2.6.0-cuda12.4-cudnn9-runtime` selects an environment with
PyTorch and CUDA libraries. Choose an image containing the packages your program needs.

`command` tells that environment which program to start and which arguments to
pass. In `command=["python", "train.py"]`, the first item starts Python and the
second names the script. This list is also called an argument vector, or argv.
The script must already be in the image or attached as uploaded code. Naming a
local file in `command` does not upload it.

To attach your code, upload it with `client.assets.upload()` and pass the
returned asset ID as `source_asset_id`. Nodus extracts that code into the workload
working directory. See [run your own Python script](../../guides/containers-and-scripts.md)
for a complete upload-and-run example.

In the HTTP API, `source` groups the image, command, and optional code asset.
Python callers pass `image`, `command`, and `source_asset_id` directly to
`client.run()`. The SDK builds the `source` object for you.

| Argument | Type | Default / omission | Workload file |
|---|---|---|---|
| `image` | `str` | `python:3.11-slim` for a single source | `image` |
| `command` | `list[str]` or `str` | No command is sent | `command` |
| `framework` | `"train_eval"` | Absent | `framework = "train_eval"` |

Use an explicit image and command. Omitting the command is accepted by this SDK,
but is not a portable way to invoke an image entrypoint: deployment bootstrap
controls execution. It is unsuitable for a first workload.

Inside a `with nodus.Client() as client:` block:

```python
workload = client.run(
    image="pytorch/pytorch:2.6.0-cuda12.4-cudnn9-runtime",
    command=["python", "-c", "print('ready')"],
    budget=5,
)
```

A string command uses `shlex.split`. It does not invoke a shell. Prefer an argv
list. Pipes, redirects, variable expansion, and `&&` need an explicit shell, such
as `command=["sh", "-c", "python preprocess.py && python train.py"]`.

Images must contain a bootstrap fetch tool (`curl`, `wget`, or `python3`), plus
your program dependencies. Upload source files explicitly with `client.assets.upload()`. `framework` is
passed through to the control plane. It does not install a framework or replace
the need to prepare runnable code. The current compiler supports `train_eval`: it runs the same command in
prepare, train, and eval stages. Code must branch on `NODUS_STAGE_ID` and honor
the declared handoffs. Prefer explicit stages when each command differs. Do not
combine `framework` with `stages`, because framework expansion takes precedence.

When `stages` is nonempty, its stage sources replace the top-level source.
Combining it with nonempty `image`, `command`, or `source_asset_id` raises `TypeError`.

## Input and output files

| Argument | Purpose |
|---|---|
| `source_asset_id` | Uploaded/imported code asset extracted into the working directory |
| `inputs` | Named asset inputs such as `[{"name": "training", "asset_id": "ASSET_ID"}]` |
| `outputs` | Declared files such as `{"model": "model.bin"}` relative to the working directory |

`outputs` creates a single stage named `main`. Do not combine it with `stages`
or `framework`. Put `source.asset_id` on each explicit stage instead of using
`source_asset_id`. Top-level asset `inputs` can also supply staged workloads. See [code and datasets](../../guides/assets.md) for asset creation and input
paths, and [logs and results](../../guides/monitoring-and-outputs.md) for downloads.

### File declaration constraints

Use asset IDs returned by upload or import, not paths or URLs. Their format is
`asset_` followed by 1 to 64 letters, digits, or hyphens. Omitted
`source_asset_id` attaches no code asset. Omitted `inputs` attaches no named assets.

`inputs` accepts at most eight dictionaries, each containing exactly `name` and
`asset_id`. Names must be unique, start with a letter, and contain at most 64
letters, digits, or underscores. For example, `training_data` is valid and
`training-data` is not. The input directory is exposed as `NODUS_INPUT_training_data`.

Output names use only letters, digits, dots, underscores, or hyphens. They must
be distinct without regard to case, cannot be `.` or `..`, and cannot end in a
dot. Reserved file names `CON`, `PRN`, `AUX`, `NUL`, `COM1` through `COM9`, and
`LPT1` through `LPT9` are rejected without regard to case, including names with
extensions such as `CON.txt`. Stage IDs with declared outputs follow these
same portability rules in addition to the [stage ID rules](stages.md).

Output paths identify files inside the workload working directory. Use `/` for
subdirectories, such as `results/model.bin`. Absolute paths, backslashes,
colons, control characters, empty path components, and `.` or `..` components
are rejected. Omitted `outputs` declares no downloadable customer files.
