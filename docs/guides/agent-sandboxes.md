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
spending limit. New sandboxes use the server's CPU default unless accelerator
resources are requested. These examples require a deployment with CPU sandbox preview enabled.
They do not establish generally available CPU workload execution. Use a
published image with an explicit non-root `USER`, a writable working directory
and the programs your agent will execute. Replace
`ghcr.io/your-org/research-agent:1` below with that image. A root-only base image
must be rebuilt with a non-root user before submission.

CPU defaults require SDK 0.5.3 or later. SDK 0.5.2 and earlier send accelerator
requirements for ordinary sandboxes even when no GPU is named.
When retrying an uncertain submission across an SDK upgrade, preserve its
original resource requirements and idempotency key. Changing the compute class
is a different request and can produce an idempotency conflict.

```python
import nodus

client = nodus.Client()
sandbox = client.sandboxes.create(
    image="ghcr.io/your-org/research-agent:1",
    name="research-agent",
    requirements={
        "peak_memory_gb": 4,
        "vcpus": 2,
        "disk_gb": 10,
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

Pass `cache_image=True` when creating a sandbox to allow verified image layers
to be reused within your team and execution region. Cache storage shares the
workspace size and count limits. Retention charges stay with the first sandbox
that cached the layer, under its existing budget, even after it terminates.
Storage billing remains disabled until a rate is configured. Unavailable or
corrupt cache entries fall back to the pinned image registry content.

When a sandbox reaches `failed`, `sandbox.failure` contains the server's
`code`, `message`, and `fix` guidance. It is `None` when no failure is returned.
Call `sandbox.refresh()` to read the latest state. The sandbox's console link
shows the same failure guidance.

Nodus matches infrastructure from the resource requirements. The customer API
does not accept supplier names or supplier machine identifiers.

Existing sandboxes retain their resource configuration when reconnected by
name. Explicit GPU requirements retain their request semantics, but do not
establish GPU access inside a sandbox. Use a GPU workload for training or
other commands that require an accelerator.

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
time. `iter_output()` follows until the execution is terminal.
`iter_output(follow=False)` drains every page available when reading starts
without waiting for new output. Use `output(after=SEQUENCE, wait=False)` when
your application manages cursors.

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
    requirements={"vcpus": 2, "peak_memory_gb": 4, "disk_gb": 10},
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

SDK 0.5.2 and later accepts an active exact name or sandbox ID for `NAME_OR_ID` in
`exec`, `logs`, `cost` and `rm`. Names and IDs are shown by `sandbox ls`. Use the
ID for historical sessions. Name lookup never creates a replacement sandbox.
See [CLI retry guidance](../reference/cli.md#agent-sandboxes) for
recovering an uncertain request without submitting duplicate work.

```bash
nodus sandbox new ghcr.io/your-org/research-agent:1 --name research-agent --budget 5
nodus sandbox ls
nodus sandbox exec NAME_OR_ID "python agent.py"
nodus sandbox logs NAME_OR_ID EXEC_ID
nodus sandbox cost NAME_OR_ID
nodus sandbox rm NAME_OR_ID
```

## Repository bootstrap preview

Connect the GitHub App to your account and grant it read access to the selected
repository. Use an image with a non-root user, a writable `/workspace` directory
and the tools your project needs. Bootstrap requires `github.com` in the egress
allowlist. Include any additional hosts needed by your setup command.

```python
import nodus

with nodus.Client() as client:
    sandbox = client.sandboxes.create(
        name="api-agent",
        image="ghcr.io/your-org/research-agent:1",
        budget=5,
        bootstrap={
            "repo": "your-org/api",
            "ref": "main",
            "setup": "make deps",
        },
        policy={
            "network": "allowlist",
            "egress_allow": ["github.com"],
        },
    )
    print(sandbox.id)
```

Creation returns the sandbox ID while startup continues. The checkout lives in
`/workspace`. The setup command appears as an ordinary execution whose ID begins
with `ex_bootstrap_`. Inspect its output and completion before running commands
that depend on it. Setup failure leaves the sandbox usable and adds a
`bootstrap_failed` warning to its API response and SDK `warnings` list.

The optional `bootstrap["dotfiles"]` selects a second connected repository in
`owner/repo` form. Bootstrap clones it into `$HOME/.dotfiles` and runs its
`install.sh` before setup. `bootstrap["ref"]` accepts a branch name or
`refs/tags/name`. Repository URLs and embedded credentials are rejected.

The installation credential is never supplied to customer commands or saved in
the checkout. Later authenticated Git operations require your own connection
method. Repository bootstrap does not turn the root filesystem into persistent
storage. Production checkout and setup qualification is pending. Use the
[reconnect and terminate](#reconnect-and-terminate) commands to manage the
existing sandbox.

## Async agents

`AsyncClient` provides the same resource model.

```python
import asyncio
import nodus


async def main():
    async with nodus.AsyncClient() as client:
        sandbox = await client.sandboxes.create(
            image="ghcr.io/your-org/research-agent:1",
            requirements={"vcpus": 2, "peak_memory_gb": 4, "disk_gb": 10},
            budget=5,
        )
        execution = await sandbox.exec(["python", "agent.py"])
        async for frame in execution.iter_output():
            print(frame.text, end="")
        await execution.wait()
        await sandbox.terminate()


asyncio.run(main())
```

## Resource measurements and alerts

Read current measurements and the last 24 hours of history with
`box.metrics()` or `await box.metrics()` for an asynchronous sandbox.
The response includes `latest`, `history`, `rollup_24h`, `last_output_at`, and
the billing `meter`. Missing measurements are `None`. Check `observed_at` on
the latest sample because an idle, suspended, or unreachable runtime may have
no recent measurement. Samples normally arrive every 30 seconds.

CPU seconds are cumulative within one runtime generation. Resident memory sums
process RSS and can count shared pages more than once. Disk usage measures
regular file logical bytes in the workspace. Rollups measure counter changes
within each generation and never fill gaps with estimated usage.

Set `stuck_after_s=60` when creating a sandbox to request an alert after an
active command has produced no output for one minute. The default is 30 minutes.
This no-output signal also applies to quiet servers and does not stop execution.
The account webhook and `box.events()` receive `sandbox.stuck`,
`sandbox.spend_rate`, and `sandbox.budget_warning` events. Spend-rate alerts use
the existing billing rate and budget, and repeat only after the qualifying rate
at least doubles.
