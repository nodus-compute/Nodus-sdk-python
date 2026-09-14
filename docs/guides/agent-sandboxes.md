# Run tool-driven agents in sandboxes

The Sandbox API runs interactive or multi-step agent code in a durable remote
environment. It has its own resources and methods. Use regular workloads for a
single submitted job with collected final outputs. Use a sandbox when an agent
needs to execute several commands, read their output, send input, or reconnect
to the same environment later.

## Authenticate and create

Install the SDK, sign in, and add a payment method in console Billing.

```bash
python -m pip install --upgrade nodus-compute
nodus login
```

Create a sandbox with a container image, resource requirements, and customer
spending limit. Customer sandboxes currently use GPU infrastructure.

```python
import nodus

client = nodus.Client()
sandbox = client.sandboxes.create(
    image="pytorch/pytorch:2.8.0-cuda12.8-cudnn9-runtime",
    name="research-agent",
    requirements={
        "gpu": "L40S",
        "peak_memory_gb": 32,
        "vcpus": 4,
        "disk_gb": 50,
    },
    budget=5,
    lifecycle={
        "idle_timeout_s": 300,
        "max_lifetime_s": 3600,
        "on_idle": "terminate",
    },
    policy={
        "network": "allowlist",
        "egress_allow": ["api.example.com"],
    },
    idempotency_key="research-agent-20260913",
)
print(sandbox.id, sandbox.state, sandbox.cost_usd)
```

Creating a sandbox is a paid operation when Nodus starts infrastructure. The
control plane requires a valid payment method and enough account headroom. The
sandbox budget limits its customer-funded usage. Acceptance can precede
readiness, so call `sandbox.refresh()` to observe the current state.

Nodus matches infrastructure from the resource requirements. The customer API
does not accept supplier names or supplier machine identifiers.

## Execute commands and stream output

An execution command is an argument vector. It is not parsed by a shell.

```python
execution = sandbox.exec(
    ["python", "-c", "print('tool result')"],
    cwd="/workspace",
    env={"AGENT_MODE": "live"},
    timeout_seconds=120,
    idempotency_key="research-agent-step-1",
)

for frame in execution.iter_output():
    print(frame.stream, frame.text, end="")

execution.wait()
if not execution.succeeded:
    raise RuntimeError(
        f"Command state={execution.state} exit={execution.exit_code} "
        f"failure={execution.failure_code}"
    )
```

Output frames preserve the order recorded by the control plane. Each frame has
a sequence, stream name, byte offset, raw `data`, decoded `text`, and creation
time. `iter_output()` follows until the execution is terminal. Use
`output(after=SEQUENCE, wait=False)` when your application manages cursors.

## Send stdin

Set `stdin=True` when creating the execution, then write text or bytes. Send an
empty frame with `eof=True` to close input without sending more data.

```python
execution = sandbox.exec(
    ["python", "interactive_agent.py"],
    stdin=True,
)
execution.write("next task\n")
execution.write(eof=True)
execution.wait()
```

Each stdin write is idempotent. Supply a stable `idempotency_key` when an
application may retry the write after losing its response.

## Reconnect and terminate

Keep the sandbox ID in durable application state. Another process can reconnect
without creating a second environment.

```python
with nodus.Client() as client:
    sandbox = client.sandboxes.from_id("sb_example")
    print(sandbox.state, sandbox.cost_usd)
    sandbox.terminate(idempotency_key="stop-sb-example")
```

Termination stops future execution and schedules resource cleanup. A client
timeout does not terminate the sandbox. Reconnect and read its current state
before deciding whether to retry an operation.

## Async agents

`AsyncClient` provides the same resource model.

```python
import asyncio
import nodus


async def main():
    async with nodus.AsyncClient() as client:
        sandbox = await client.sandboxes.create(
            image="pytorch/pytorch:2.8.0-cuda12.8-cudnn9-runtime",
            requirements={"gpu": "L40S", "peak_memory_gb": 32},
            budget=5,
        )
        execution = await sandbox.exec(["python", "agent.py"])
        async for frame in execution.iter_output():
            print(frame.text, end="")
        await execution.wait()
        await sandbox.terminate()


asyncio.run(main())
```
