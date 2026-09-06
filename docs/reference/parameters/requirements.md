# Resource requirements

| Argument | Type / values | Omitted | CLI | HTTP field |
|---|---|---|---|---|
| `model` | Free-text `str` | No model hint | `--model` | `requirements.model` |
| `compute_class` | `"accelerator"`, `"vm"`, or `ComputeClass` enum | Accelerator on the current API | `--compute-class` | `requirements.compute_class` |
| `peak_memory_gb` | Number in GB | No explicit memory hint | `--peak-memory-gb` | `requirements.peak_memory_gb` |
| `expected_runtime_hours` | Number in hours | No explicit duration estimate | `--hours` | `requirements.expected_runtime_hours` |
| `requirements` | Dictionary | Empty object | Python only | `requirements` |

Provide realistic positive memory and runtime estimates. Runtime informs routing
and pricing. It is not a stop timer. `model` describes the workload. It does not
download weights. The memory hint alone is not a request for a specific GPU SKU
or topology. Inspect the selected route after placement.

```python
workload = client.run(
    image="pytorch/pytorch:2.6.0-cuda12.4-cudnn9-runtime",
    command=["python", "-c", "import torch\nprint(torch.cuda.is_available())"],
    compute_class="accelerator",
    peak_memory_gb=24,
    expected_runtime_hours=0.1,
    budget=5,
    continuity="restartable",
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
