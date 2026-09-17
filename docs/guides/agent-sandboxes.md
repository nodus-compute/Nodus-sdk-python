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
spending limit. Customer sandboxes currently use GPU infrastructure. Use a
published image with an explicit non-root `USER`, a writable working directory
and the programs your agent will execute. Replace
`ghcr.io/your-org/research-agent:1` below with that image. A root-only base image
must be rebuilt with a non-root user before submission.

```python
import nodus

client = nodus.Client()
sandbox = client.sandboxes.create(
    image="ghcr.io/your-org/research-agent:1",
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
        "egress_allow": ["api.anthropic.com"],
    },
    idempotency_key="research-agent-20260913",
)
print(sandbox.id, sandbox.state, sandbox.cost_usd)
```

Creating a sandbox is a paid operation when Nodus starts infrastructure. The
control plane requires available credits and enough account headroom. A payment
method is optional when credits cover the work. The
sandbox budget limits its customer-funded usage. Acceptance can precede
readiness. Calling `exec` waits for the environment and then runs the command,
so application code does not need a readiness loop.

When a sandbox reaches `failed`, `sandbox.failure` contains the server's
`code`, `message`, and `fix` guidance. It is `None` when no failure is returned.
Call `sandbox.refresh()` to read the latest state. The sandbox's console link
shows the same failure guidance.

Nodus matches infrastructure from the resource requirements. The customer API
does not accept supplier names or supplier machine identifiers.

## Execute commands and stream output

Pass a string for a shell command or an argument vector for exact process
arguments.

```python
execution = sandbox.exec(
    "python -c \"print('tool result')\"",
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

## Read network usage and denied destinations

Allowlist mode supports HTTP and HTTPS clients that respect the supplied proxy
environment variables. Only ports 80 and 443 are supported. Direct external
sockets remain unavailable. Use hostnames without URL schemes or paths. An exact
name permits only that host. A leading `*.` permits its subdomains, not the bare
parent name. Private and metadata addresses remain blocked.

For direct model APIs, permit `api.anthropic.com` for the
[Claude API](https://platform.claude.com/docs/en/api/overview) or `api.openai.com`
for the [OpenAI API](https://platform.openai.com/docs/api-reference/introduction).
Other API gateways and package downloads need their own exact hostnames.

```python
sandbox.refresh()
print(sandbox.network_usage)

cursor = 0
for event in sandbox.events(after=cursor):
    cursor = event.seq
    if event.type == "sandbox.egress_denied":
        print(event.payload["hostname"], event.payload["count"])
```

Keep the last `event.seq` and pass it as `after` on the next poll. Each call
returns at most 100 events. Async handles provide the same methods with `await`.
Denial events contain hostnames and counts, without URLs, headers or bodies.

`network_usage` contains the last reported `sent_bytes` and `received_bytes`
across sandbox generations. These are proxied HTTP and TLS stream bytes, not
customer charges. Refresh to read newer reports. Abrupt host loss can leave the
last unreported bytes unknown. Older API responses leave this field as `None`.

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

For the common case, the top-level constructor creates or reattaches by name.
Its context manager terminates the sandbox when the block exits.

```python
with nodus.Sandbox(
    name="research-agent",
    image="ghcr.io/your-org/research-agent:1",
    requirements={"gpu": "L40S", "peak_memory_gb": 32},
    budget=5,
) as sandbox:
    process = sandbox.exec("python agent.py")
    for frame in process.iter_output():
        print(frame.text, end="")
    process.wait()
```

Use the same name without an image to reattach. Call `close()` when the local
handle is no longer needed and the remote sandbox should keep running.

```python
sandbox = nodus.Sandbox(name="research-agent")
try:
    print(sandbox.cost_usd, sandbox.url)
finally:
    sandbox.close()
```

The CLI uses the same nouns and verbs.

```bash
nodus sandbox new ghcr.io/your-org/research-agent:1 --name research-agent --budget 5
nodus sandbox ls
nodus sandbox exec SANDBOX_ID "python agent.py"
nodus sandbox logs SANDBOX_ID EXEC_ID
nodus sandbox cost SANDBOX_ID
nodus sandbox rm SANDBOX_ID
```

## Async agents

`AsyncClient` provides the same resource model.

```python
import asyncio
import nodus


async def main():
    async with nodus.AsyncClient() as client:
        sandbox = await client.sandboxes.create(
            image="ghcr.io/your-org/research-agent:1",
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
