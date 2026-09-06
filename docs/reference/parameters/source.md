# Source: image and command

| Argument | Type | Default / omission | CLI |
|---|---|---|---|
| `image` | `str` | `python:3.11-slim` for a single source | `--image` |
| `command` | `list[str]` or `str` | No command is sent | Arguments after `--` |
| `framework` | `str` | Absent | Python only |

Use an explicit image and command. Omitting the command is accepted by this SDK,
but is not a portable way to invoke an image entrypoint: deployment bootstrap
controls execution. It is unsuitable for a first workload.

```python
workload = client.run(
    image="python:3.11-slim",
    command=["python", "-c", "print('ready')"],
    budget=5,
    continuity="restartable",
)
```

A string command uses `shlex.split`; it does not invoke a shell. Prefer an argv
list. Pipes, redirects, variable expansion, and `&&` need an explicit shell, such
as `command=["sh", "-c", "python preprocess.py && python train.py"]`.

Images must contain a bootstrap fetch tool (`curl`, `wget`, or `python3`), plus
your program and dependencies. Local files are not uploaded. `framework` is
passed through to the control plane; it does not install a framework or replace
the need to prepare runnable code. The current compiler supports `train_eval`: it runs the same command in
prepare, train, and eval stages; code must branch on `NODUS_STAGE_ID` and honor
the declared handoffs. Prefer explicit stages when each command differs. Do not
combine `framework` with `stages`, because framework expansion takes precedence.

When `stages` is nonempty, its stage sources replace the top-level source.
Combining it with nonempty `image` or `command` raises `TypeError`.
