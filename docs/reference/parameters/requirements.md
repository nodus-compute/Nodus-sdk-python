# Resource requirements

| Argument | Type / values | Omitted | Workload file | HTTP field |
|---|---|---|---|---|
| `model` | Free-text `str` | No model hint | `model` | `requirements.model` |
| `compute_class` | `"accelerator"` for GPU workloads | Accelerator on the current API | `compute_class` | `requirements.compute_class` |
| `peak_memory_gb` | Number in GB | No explicit memory hint | `peak_memory_gb` | `requirements.peak_memory_gb` |
| `requirements` | Dictionary | Empty object | Same key or table | `requirements` |

Provide a positive memory requirement when your workload needs one. Nodus manages
runtime estimates. You do not need to predict how long your command will take.
`model` describes the workload. It does not
download weights. The memory hint alone is not a request for a specific GPU SKU
or topology. Inspect the selected route after placement.

Inside a `with nodus.Client() as client:` block:

```python
workload = client.run(
    image="pytorch/pytorch:2.6.0-cuda12.4-cudnn9-runtime",
    command=["python", "-c", "import torch\nprint(torch.cuda.is_available())"],
    compute_class="accelerator",
    peak_memory_gb=24,
    budget=5,
)
```

An explicit dictionary key wins over the matching flat shortcut:
`requirements={"peak_memory_gb": 48}, peak_memory_gb=24` sends 48.
Use dictionaries only for documented server fields. No public provider or GPU
SKU selection argument is defined by this SDK.

The raw requirements dictionary additionally accepts `dataset_bytes` (nonnegative
integer, dataset size hint) and `notes` (free text). Neither stages data or installs
dependencies. Omitted values are zero/empty on the server.

Use `nodus.Requirements(...)` for optional static typing. The helper is a plain
dictionary and does not validate inputs at runtime.
