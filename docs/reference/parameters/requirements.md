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
cost to fastest. Nodus balances expected completion cost and completion time when qualified
estimates are available, using price and GPU performance signals otherwise.
The preference does not guarantee total cost or runtime.

### Combining optimization and an explicit GPU

When a run does not have qualified completion estimates, Nodus uses the
following GPU eligibility groups. An explicit `gpu` must fit the selected group
as well as your memory, CPU, disk, and location requirements.

| Optimization | Explicit GPU families eligible without completion estimates |
|---|---|
| `lowest_cost` | `RTX 3090`, `RTX 4090`, `RTX A6000` |
| `lower_cost` | `RTX 3090`, `RTX 4090`, `RTX A6000`, `A100`, `L40S` |
| `balanced` | `RTX 4090`, `L40S`, `A100` with 80 GB, `H100` |
| `faster` | `B200`, `H200`, `H100` |
| `fastest` | `B200`, `H200`, `H100` with SXM form factor |

For example, use `gpu="RTX 3090", optimization="lower_cost"` or
`gpu="RTX 4090", optimization="balanced"`. `RTX 4090` with `fastest` does
not fit the last group. Nodus reports no eligible capacity instead of changing
your GPU. The SDK selects model families, not form factors. Nodus applies any
memory or form-factor restriction shown above when choosing the machine.

When qualified estimates are available, optimization can rank the broader
compatible GPU pool by expected completion cost and time. Explicit GPU and
resource requirements still apply. Accepted model names such as `A10`, `A10G`,
`L4`, `L40`, `T4`, `V100`, and `RTX 5090` are not in the current eligibility
groups. Accepting their spelling does not establish routable capacity.
Omit `gpu` for the widest available choice within your optimization preference.

An omitted or empty preference in a stage's requirements inherits the workload
preference. An empty value in the workload requirements lets the API use its
balanced default. The flat `optimization` shortcut requires one of the five
named choices.

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
