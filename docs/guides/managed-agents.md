# Managed durable agents

These additions are unreleased and are not available in the published 0.6.0
package. Candidate builds use an exact source revision for qualification.

Managed agents require both a qualified tools environment and account admission
on the deployment. They are a gated CPU capability. Their presence in the SDK
does not establish production qualification or general CPU workload support.
Authenticate with `nodus login` or provide `NODUS_API_KEY` through your secret
manager. Never put that account key in the uploaded project.

## Deploy and submit

Save this synchronous entrypoint as `agent.py` in your project:

```python
import nodus

@nodus.step(name="calculate", version="1", effect="pure")
def calculate(values):
    return sum(values)

def main(event):
    total = calculate(event["values"], _step_id="calculate")
    return {"total": total}
```

Deploy the project and submit input from your authenticated client:

```python
import nodus

with nodus.Client() as client:
    agent = client.agents.create(
        name="calculator",
        project=".",
        entrypoint="agent:main",
        budget=20,
        max_workers=4,
        idempotency_key="calculator-deployment-001",
    )
    run = agent.submit(
        {"values": [1, 2, 3]},
        idempotency_key="calculation-001",
    )
    print(agent.id, agent.url, run.id, run.status)
```

The $20 limit is an explicit authorization across the agent's workers. The
server controls cost accounting and reserved exposure. Defaults are supplied
by the server. Omitted scaling settings use zero minimum workers and four
maximum workers. Deployment acceptance does not mean a worker is ready.

Use `setup` to persist a dependency installation command with the revision.
The CLI provides `--setup`. Enable the matching package network permissions.
Advanced deployments can supply `policy` with explicit network allowlists.
Use `agent.revisions(after=...)` and `agent.revision(number)` to inspect
immutable definitions. `agent.update(expected_revision=..., ...)` admits a
new definition only if the observed current revision still matches.

Read `client.agents.get(agent_id)` and `agent.runs.get(run_id)` to observe
progress and the returned `result`. Use `.list_page(after=...)` or `.iterate()`
on `client.agents` and `agent.runs` to traverse history. `agent.pause()` stops
new dispatch and `agent.resume()` allows it again. `run.cancel()` requests
cancellation. Each mutation accepts an idempotency key. Run submission and
signals require an explicit key so uncertain responses can be retried safely.
When the server reports exhausted retries, `run.retry()` explicitly requests
another attempt within the existing authorization. It does not resolve an
unknown external effect or raise the spending limit.

The CLI exposes the same operations. `nodus agent deploy NAME --budget USD`
uploads the current directory. Add `--project PATH` to select another project
or `--source-asset-id ID` to reuse one already uploaded. Use `nodus agent list`,
`detail`, `submit`, `runs`, `signal`, `pause`, `resume` and `cancel` to operate
the deployment. `submit` accepts JSON through `--input` or `--input-file` and
requires `--idempotency-key`.

Inputs can include `session="customer-42"` to request ordered processing for
that session. A timezone-aware `deadline` is optional. Use a new event key for
new input. Retrying the same event key with changed input is a conflict.

`AsyncClient.agents` exposes the same operations. Await methods that perform
requests and use `async for` with `.iterate()`.

## Wait without keeping a worker busy

Inside `main`, call `nodus.agent.wait_for_event("approval", wait_id="approval:1")`
to durably wait for an event. Deliver it with
`run.signal("approval", {"accepted": True}, idempotency_key="approval-event-1")`.
The wait replays the recorded input when the driver resumes.

Use `nodus.agent.sleep_until(when, wait_id="wake:1")` with a timezone-aware
datetime for a timer. Keep the time and wait ID stable across replay.
`nodus.agent.continue_as_new(next_input, continuation_id="page:2")` commits
the next segment's input and ends the current execution. Do not catch these
execution yields with `except BaseException`.

The managed runtime starts the assigned entrypoint and connects the scoped
durable session automatically. Application code does not call `resume` or
select sandbox IDs. Waits and continuations run outside decorated steps.
Keep code outside steps deterministic. Record model calls and external effects
with the effect contracts in [durable steps](../durable-steps.md).

## Recovery boundaries

Completed step results replay from the journal. Application recovery files
belong in `NODUS_CHECKPOINT_DIR`. Project files, recovery state and downloadable
outputs serve different purposes. Rebuild dependencies on replacement compute.
Neither file restoration nor replay restores arbitrary process memory.

JSON inputs and results are bounded. Inside the managed driver,
`nodus.agent.put_blob(data)` commits up to 32 MiB of bytes and returns an immutable
reference suitable for a step result. `nodus.agent.get_blob(reference)` checks
the complete content hash and length before returning bytes. Blobs are scoped
to the logical run and remain usable across worker replacement and continuations.
Run and account storage limits still apply. These operations do not provide
filesystem atomicity or save arbitrary process memory.
Unknown external effects remain blocked for explicit resolution rather than
being silently repeated. Inspect `run.steps()` and verify the remote effect,
then use `run.resolve()` with the observed step revision, decision, reason and
evidence digest. The CLI provides `nodus agent steps` and `nodus agent resolve`
with the same evidence requirements. Finite tests do not guarantee completion
of every run.
