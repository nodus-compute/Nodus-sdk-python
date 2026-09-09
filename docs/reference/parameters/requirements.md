# Resource requirements

| Argument | Type / values | Omitted | HTTP field |
|---|---|---|---|
| `model` | Free-text workload description | No model hint | `requirements.model` |
| `compute_class` | `"accelerator"` for GPU workloads | Accelerator | `requirements.compute_class` |
| `peak_memory_gb` | Positive number in GB | No explicit memory hint | `requirements.peak_memory_gb` |
| `optimization` | `"lowest_cost"`, `"lower_cost"`, `"balanced"`, `"faster"`, `"fastest"` | `"balanced"` | `requirements.optimization` |
| `gpu` | `"A100"`, `"H100"`, `"H200"`, `"B200"`, `"A10"`, `"A10G"`, `"L4"`, `"L40"`, `"L40S"`, `"T4"`, `"V100"`, `"RTX A6000"`, `"RTX 3090"`, `"RTX 4090"`, `"RTX 5090"`. [Examples and aliases](#gpu-model) | Nodus chooses | `requirements.gpu` |
| `requirements` | Dictionary | Optional resource hints | `requirements` |

The workload file uses the same argument names. You do not need to predict how
long your program will run. Provide memory only when you know the requirement.
`model` describes your workload and does not download model weights.

## Optimization

Choose the preference closest to your goal. The five choices run from lowest
cost to fastest. Compatible deployments use the preference during placement.
It does not guarantee total cost or runtime. Older deployments may record the
value without changing placement.

An omitted or empty preference in a stage's requirements inherits the workload
preference. An empty value in the workload requirements lets the API use its
balanced default. The flat `optimization` shortcut requires one of the five
named choices.

## GPU model

On a compatible backend, `gpu` is a hard requirement. Nodus never substitutes another model, including
when retrying a run. If matching capacity is unavailable, the run reports that
condition. Omit `gpu` to let Nodus choose compatible capacity.
An older backend may ignore this field. Confirm
[backend compatibility](../../operations/errors.md#backend-compatibility)
before relying on GPU enforcement.

These are all accepted canonical model names. Use the Python argument shown in
`client.run()`, or the same quoted value for `gpu` in a workload file.

| GPU model | Exact Python argument |
|---|---|
| A100 | `gpu="A100"` |
| H100 | `gpu="H100"` |
| H200 | `gpu="H200"` |
| B200 | `gpu="B200"` |
| A10 | `gpu="A10"` |
| A10G | `gpu="A10G"` |
| L4 | `gpu="L4"` |
| L40 | `gpu="L40"` |
| L40S | `gpu="L40S"` |
| T4 | `gpu="T4"` |
| V100 | `gpu="V100"` |
| RTX A6000 | `gpu="RTX A6000"` |
| RTX 3090 | `gpu="RTX 3090"` |
| RTX 4090 | `gpu="RTX 4090"` |
| RTX 5090 | `gpu="RTX 5090"` |

Names are case-insensitive and whitespace is ignored. An optional `NVIDIA`
prefix and compact RTX names are accepted, such as `"nvidia h100"` and
`"RTX4090"`. `"A6000"` is an alias for `"RTX A6000"`.
These names describe models, not a guarantee of current capacity.
Choose the model family and specify memory separately, such as `gpu="A100"`
with `peak_memory_gb=80`. Supplier names and machine IDs are not GPU names.

Inside a `with nodus.Client() as client:` block:

```python
workload = client.run(
    image="pytorch/pytorch:2.6.0-cuda12.4-cudnn9-runtime",
    command=["python", "-c", "import torch\nprint(torch.cuda.get_device_name(0))"],
    gpu="H100",
    optimization="balanced",
    budget=5,
)
```

An explicit dictionary key wins over the matching flat shortcut:
`requirements={"peak_memory_gb": 48}, peak_memory_gb=24` sends 48.
Workload files reject duplicate flat and nested settings so the choice is clear.

## Additional dictionary fields

These fields belong inside `requirements={...}` in Python or `[requirements]`
in a workload file. They are not flat `run()` arguments.

| Field | Type and units | Omitted |
|---|---|---|
| `disk_gb` | Finite nonnegative number in GB | No explicit disk requirement |
| `vcpus` | Finite nonnegative number of virtual CPUs | No explicit CPU requirement |
| `dataset_bytes` | Nonnegative integer in bytes | No dataset-size hint |
| `notes` | Text with additional workload context | No notes |

Omitted or zero disk and CPU values in a stage inherit the workload requirements.
These fields do not transfer data or install dependencies.
`nodus.Requirements(...)` provides optional static typing. The SDK validates GPU
names and optimization choices for both typed and ordinary dictionaries. The
API validates the remaining resource hints when you submit the workload.
