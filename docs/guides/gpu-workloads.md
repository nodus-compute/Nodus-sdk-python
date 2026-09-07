# GPU training and fine-tuning

Start by verifying CUDA in a known PyTorch image:

```python
import nodus

with nodus.Client() as client:
    workload = client.run(
        image="pytorch/pytorch:2.6.0-cuda12.4-cudnn9-runtime",
        command=[
            "python", "-c",
            "import torch\n"
            "assert torch.cuda.is_available()\n"
            "print(torch.cuda.get_device_name(0))",
        ],
        peak_memory_gb=24,
        expected_runtime_hours=0.1,
        budget=5,
    )
    print(workload.id)
    done = workload.wait()
    if not done.succeeded:
        raise RuntimeError("GPU smoke test did not complete successfully")
```

The budget is illustrative, not a price guarantee. A feasible route still needs
to fit your account, region policy, and available capacity.

For training, [build an image](containers-and-scripts.md) containing your code,
framework dependencies, and data-access logic. Then run its actual command:

```python
import nodus

with nodus.Client() as client:
    workload = client.run(
        image="YOUR_REGISTRY/trainer:v1",
        command=["python", "/app/train.py", "--epochs", "3"],
        model="LoRA-fine-tune",
        peak_memory_gb=24,
        expected_runtime_hours=2,
        budget=25,
    )
    print(workload.id)
    done = workload.wait()
    if not done.succeeded:
        raise RuntimeError(f"Training ended: {done.status}")
    print(done.logs())
```

This is a template: `/app/train.py` and `--epochs` belong to your application.
`model` is a sizing hint, not a model download. Choose memory and runtime estimates appropriate for your program.

Declare final model files as stage outputs when you need SDK downloads. See
[multi-stage workloads](multi-stage-workloads.md). Nodus handles placement and
execution. Advanced application integrations are documented separately in the
[parameter reference](../reference/parameters/index.md).
