# Source: image and command

| Argument | Type | Default / omission | Workload file |
|---|---|---|---|
| `image` | `str` | `python:3.11-slim` for a single source | `image` |
| `command` | `list[str]` or `str` | No command is sent | `command` |
| `framework` | `str` | Absent | Same key or table |

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
Combining it with nonempty `image` or `command` raises `TypeError`.

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
