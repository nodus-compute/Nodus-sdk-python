# Managed durable agents

Managed agent methods require SDK 0.7.0 or later. Availability depends on
account admission and deployment qualification.

Managed agents require both a qualified tools environment and account admission
on the deployment. They are a gated CPU capability. Their presence in the SDK
does not establish production qualification or general CPU workload support.
Authenticate with `nodus login` or provide `NODUS_API_KEY` through your secret
manager. Never put that account key in the uploaded project.

For bounded task batches with independent state and dependency ordering, see
[agent groups](agent-groups.md).

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

Connect GitHub in the console and grant access to deploy from a public or private
repository. This uses the existing connection without passing GitHub credentials
to the CLI:

```bash
nodus agent deploy worker \
  --github-repo your-org/private-agent \
  --github-ref main \
  --budget 20
```

Omit `--github-ref` to use the repository default branch. The CLI adds the GitHub
network permission and runs `--setup` after checkout when provided. Choose one
of `--github-repo`, `--project` or `--source-asset-id`. Repository subdirectory
selection is not supported. Use a dotted Python module in `--entrypoint` when
your agent is inside a package.

Inputs can include `session="customer-42"` to request ordered processing for
that session. A timezone-aware `deadline` is optional. Use a new event key for
new input. Retrying the same event key with changed input is a conflict.

`AsyncClient.agents` exposes the same operations. Await methods that perform
requests and use `async for` with `.iterate()`.

## Observe lifecycle and cleanup

Read lifecycle observations through the authenticated HTTP endpoint. Set
`NODUS_API_KEY` through your secret manager, `NODUS_BASE_URL` to your HTTPS API
origin without `/v1`, and `NODUS_AGENT_ID` to the definition's `agent.id`.

```bash
curl --fail --silent --show-error --get \
  --header "Authorization: Bearer ${NODUS_API_KEY}" \
  --data-urlencode "limit=50" \
  --data-urlencode "after=${NODUS_AFTER:-}" \
  "${NODUS_BASE_URL}/v1/agents/${NODUS_AGENT_ID}/observations"
```

Leave `NODUS_AFTER` unset for the first page. Set it to the returned `next_after`
and repeat until that value is empty. Each page has its own `observed_at` time
and may reflect newer state. `logical_runs` covers all runs of the definition.
`allocations` includes retained attempts and resources, including warm attempts
not assigned to a run, while `runs` contains the current page. The endpoint returns
404 for definitions that do not exist or belong to another tenant.

Allocation counts can overlap. A completed run can still have pending cleanup.
In `managed-agent-observations-v1`, `productive_active_agents` and each run's
`first_useful_command_at` are always null, with their status fields set to
`measurement_unavailable`. Lifecycle observations do not independently verify
productive work or accepted task outputs. Check those against your workload's
expected results.

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

New deployments admitted with qualified application-state recovery use
`checkpoint-v1`. Check `run.recovery_policy` to see the accepted policy. Existing
deployments retain their recovery policy when updated, and each run retains its
accepted code revision.

With `checkpoint-v1`, Nodus saves the files in `NODUS_CHECKPOINT_DIR` before
recording a completed step, entering a durable wait, continuing as new or
finishing the run. The step result and its verified state version commit
together. On a replacement attempt, Nodus restores that exact committed state
before starting the entrypoint. Completed steps replay their recorded results.
Uncommitted changes in the state folder are discarded before retrying.

Your application must serialize its in-memory state into that folder and load
those files on restart. Finish, flush and close every state writer before a step
returns or the driver calls a durable wait or continuation. Join background
writers first. Nodus checks for changed files during capture but does not freeze
background processes or provide an atomic filesystem snapshot. If a save fails,
let the driver exit so recovery can restore committed state before another
attempt. An empty baseline does not claim saved progress, and an empty folder
cannot replace an earlier useful recovery point.

Inspect `run.checkpoint_id` and `run.last_checkpoint_at` for committed state.
`run.checkpoint_status` and `run.checkpoint_error` describe the latest capture.
The `ready` status means files are verified but the associated step or wait has
not committed yet. Empty baselines have no `last_checkpoint_at`. The console
shows the last useful save separately from pending saves and gives a next action
when a capture fails. CLI run output includes the same fields.

Project files, recovery state and downloadable outputs serve different purposes.
Files outside `NODUS_CHECKPOINT_DIR` are not part of these state commits. Rebuild
dependencies on replacement compute. Revisions without `checkpoint-v1` retain
explicit file recovery, with file saves separate from step completion. Neither
file restoration nor replay restores arbitrary process memory.

JSON inputs and results are bounded. Inside the managed driver,
`nodus.agent.put_blob(data)` commits up to 32 MiB of bytes and returns an immutable
reference suitable for a step result. `nodus.agent.get_blob(reference)` checks
the complete content hash and length before returning bytes. Blobs are scoped
to the logical run and remain usable across worker replacement and continuations.
Run and account storage limits still apply. Blob operations commit independently.
Record their verified references in step results when they are needed for replay.

Unknown external effects remain blocked for explicit resolution. File recovery
cannot determine whether a remote API call succeeded when its response was lost.
Inspect `run.steps()` and independently verify the remote effect, then use
`run.resolve()` with the observed step revision, decision, reason and evidence
digest. With `checkpoint-v1`, a `completed` decision also requires an explicit
`checkpoint_id` whose files match the recorded result. Choose the run's current
committed checkpoint or the verified candidate returned for that uncertain step.
Step entries expose the candidate's `checkpoint_id` and `checkpoint_status`.
Do not select an older checkpoint that would discard other completed progress.
Omit `checkpoint_id` for `no_effect` or `cancelled` decisions, which retain the
current committed state.

The CLI provides `nodus agent steps` and `nodus agent resolve --checkpoint-id`
with the same evidence requirements. The console requires an explicit saved-state
selection and confirmation for completed effects. Finite tests do not guarantee
completion of every run.
