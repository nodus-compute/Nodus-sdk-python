# Resource requirements

| Argument | Type / values | Omitted | HTTP field |
|---|---|---|---|
| `model` | Free-text workload description | No model hint | `requirements.model` |
| `compute_class` | `"accelerator"` for GPU workloads | Accelerator | `requirements.compute_class` |
| `peak_memory_gb` | Positive number in GB | No explicit memory hint | `requirements.peak_memory_gb` |
| `optimization` | `"lowest_cost"`, `"lower_cost"`, `"balanced"`, `"faster"`, `"fastest"` | `"balanced"` | `requirements.optimization` |
| `gpu` | Standard GPU model name | Nodus chooses | `requirements.gpu` |
| `requirements` | Dictionary | Optional resource hints | `requirements` |

The workload file uses the same argument names. You do not need to predict how
long your program will run. Provide memory only when you know the requirement.
`model` describes your workload and does not download model weights.

## Optimization

Choose the preference closest to your goal. The five choices run from lowest
cost to fastest. Nodus currently records this preference, while all choices use
the existing routing behavior. Preference-specific routing is not active yet.

## GPU model

`gpu` is a hard requirement. Nodus never substitutes another model, including
when retrying a run. If matching capacity is unavailable, the run reports that
condition. Omit `gpu` to let Nodus choose compatible capacity.

Supported names are `A100`, `H100`, `H200`, `B200`, `A10`, `A10G`, `L4`, `L40`,
`L40S`, `T4`, `V100`, `RTX A6000`, `RTX 3090`, `RTX 4090`, and `RTX 5090`.
Names are case-insensitive. Compact RTX names and an optional NVIDIA prefix
are accepted. These names describe models, not a guarantee of current capacity.
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

The requirements dictionary also accepts `dataset_bytes` (nonnegative integer)
and `notes` (text). These are hints and do not transfer data or install dependencies.
`nodus.Requirements(...)` provides optional static typing. Submission validates
the values for both typed and ordinary dictionaries.
