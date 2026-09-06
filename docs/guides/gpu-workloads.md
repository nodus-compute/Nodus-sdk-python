# GPU training and fine-tuning

Start by verifying CUDA in a known PyTorch image:

```python
import nodus

with nodus.Client() as client:
    workload = client.run(
        image="pytorch/pytorch:2.6.0-cuda12.4-cudnn9-runtime",
        command=["python", "-c", "import torch\nassert torch.cuda.is_available()\nprint(torch.cuda.get_device_name(0))"],
        compute_class="accelerator",
        peak_memory_gb=24,
        expected_runtime_hours=0.1,
        budget=5,
        continuity="restartable",
    )
    print(workload.id)
    if not workload.wait().succeeded:
        raise RuntimeError("GPU smoke test did not complete successfully")
```

The budget is illustrative, not a price guarantee. A feasible route still needs
to fit your account, region policy, and available capacity.

For training, [build an image](containers-and-scripts.md) containing your code,
framework dependencies, and data-access logic. Then run its actual command:

```bash
nodus run --image YOUR_REGISTRY/trainer:v1 --model 'LoRA fine-tune'   --peak-memory-gb 24 --hours 2 --budget 25 --continuity restartable --wait   -- python /app/train.py --epochs 3
```

This is a template: `/app/train.py` and `--epochs` belong to your application.
`model` is a sizing hint, not a model download. GPU count, distributed rendezvous,
and framework flags must not be inferred from a memory hint. Validate your
multi-GPU launch with your deployment before scaling it.

A restartable run may redo work after interruption. For long jobs, integrate the
runner's supported checkpoint/restore contract and select `checkpointed` only
after testing restore. Declare final model files as stage outputs when you need
SDK downloads. See [multi-stage workloads](multi-stage-workloads.md).
