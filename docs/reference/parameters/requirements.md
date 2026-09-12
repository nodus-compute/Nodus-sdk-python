# Resource requirements

| Argument | Type / values | Omitted | HTTP field |
|---|---|---|---|
| `model` | Free-text workload description | No model hint | `requirements.model` |
| `compute_class` | `"accelerator"` for GPU workloads | Accelerator | `requirements.compute_class` |
| `peak_memory_gb` | Positive number in GB | No explicit memory hint | `requirements.peak_memory_gb` |
| `optimization` | `"lower_cost"`, `"balanced"`, `"faster"`. Legacy aliases: `"lowest_cost"`, `"fastest"` | `"balanced"` | `requirements.optimization` |
| `gpu` | `"A100"`, `"H100"`, `"H200"`, `"B200"`, `"A10"`, `"A10G"`, `"L4"`, `"L40"`, `"L40S"`, `"T4"`, `"V100"`, `"RTX A6000"`, `"RTX 3090"`, `"RTX 4090"`, `"RTX 5090"`. [Examples and aliases](#gpu-model) | Nodus chooses | `requirements.gpu` |
| `requirements` | Dictionary | Optional resource hints | `requirements` |

The workload file uses the same argument names. You do not need to predict how
long your program will run. Provide memory only when you know the requirement.
`model` describes your workload and does not download model weights.

## Optimization

Choose one of three preferences for your run:

| Preference | Goal when trustworthy, comparable completion estimates are available |
|---|---|
| `lower_cost` | Give more weight to expected completion cost |
| `balanced` | Give equal weight to expected completion cost and time |
| `faster` | Give more weight to expected completion time |

The legacy values `lowest_cost` and `fastest` remain accepted as aliases for
`lower_cost` and `faster`, respectively. They do not add separate preferences.

All preferences consider the same pool of compatible GPUs. Without trustworthy,
comparable completion estimates, all three prefer configurations with lower
full hourly prices. A lower hourly price does not guarantee a lower total cost,
and `faster` does not guarantee a shorter runtime.

### Combining optimization and an explicit GPU

An explicit `gpu`, memory, CPU, disk, image compatibility, location and budget
constrain every preference. Optimization never relaxes these requirements.

For example, use `gpu="RTX 3090", optimization="lower_cost"` or
`gpu="RTX 4090", optimization="balanced"`. RTX 4090 can also be used with
`optimization="faster"`. An explicit GPU model is never replaced by another
model.

Accepted model names such as `A10`, `A10G`,
`L4`, `L40`, `T4`, `V100`, and `RTX 5090` do not establish available capacity.
Omit `gpu` to give Nodus more compatible models to choose from.

An omitted or empty preference in a stage's requirements inherits the workload
preference. An empty value in the workload requirements lets the API use its
balanced default. The flat `optimization` shortcut requires one of the three
preferences or a legacy alias.

## GPU model

`gpu` is a hard requirement. Nodus never substitutes another model, including
when retrying a run. If matching capacity is unavailable, the run reports that
condition. Omit `gpu` to let Nodus choose compatible capacity.
An accepted model name does not guarantee matching capacity. GPU, memory,
and other resource requirements must all fit an available machine.

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

Names are case-insensitive. Whitespace, hyphens, and underscores are ignored. An optional `NVIDIA`
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
names, optimization choices, and numeric resource bounds for both typed and
ordinary dictionaries before submission. Booleans and nonfinite numbers are
not valid resource quantities. Explicit `peak_memory_gb` must be positive.
