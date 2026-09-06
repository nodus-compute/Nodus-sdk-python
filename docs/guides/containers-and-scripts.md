# Run a container or Python script

The command executes remotely inside the selected image. A local `train.py` is
not uploaded by `client.run()`. Package your code and dependencies first.

For example, put this `hello.py` beside a `Dockerfile`:

```python
import torch

print(torch.cuda.get_device_name(0))
```

```dockerfile
FROM pytorch/pytorch:2.6.0-cuda12.4-cudnn9-runtime
WORKDIR /app
COPY hello.py /app/hello.py
```

Build and push to a registry your deployment can pull from. Replace the image
name with your actual registry namespace:

```bash
docker build -t YOUR_REGISTRY/hello:v1 .
docker push YOUR_REGISTRY/hello:v1
nodus run --compute-class accelerator --image YOUR_REGISTRY/hello:v1 \
  --budget 5 --wait -- python /app/hello.py
```

Use absolute program paths so runner working-directory conventions do not affect
code lookup. Pin image versions or digests for repeatable submissions. The SDK
has no registry-credential argument. Verify private-image access with your
deployment before submitting.

For dependencies, install them in the image during build. For input datasets,
your program must fetch accessible data or consume declared upstream stage
inputs. Do not put credentials in a container layer or command line. This SDK
currently has no general environment/secret injection API.
