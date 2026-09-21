# Terminal commands

Use `nodus --help` for command groups and `nodus COMMAND --help` for options.
Replace `ID` with a workload ID. Use the installed command help to confirm
which capabilities your SDK version provides.

## Setup

| Command | What it does |
|---|---|
| `nodus mcp` | Start the local MCP server using your saved login. Requires `nodus-compute[mcp]` |
| `nodus login` | Reuse a valid login or open browser sign-in |
| `nodus login --force` | Start a fresh browser sign-in |
| `nodus logout` | Remove the locally saved key |
| `nodus init` | Create a starter `nodus.toml` without submitting work |

For headless machines and automation, see [authentication](../getting-started/authentication.md).

## Run

| Command | What it does |
|---|---|
| `nodus run` | Submit `nodus.toml` and wait for completion |
| `nodus run train.toml` | Submit another file and wait |
| `nodus submit train.toml` | Submit and print the ID without waiting |
| `nodus list` | List your workloads |
| `nodus list active` | List active workloads |
| `nodus list mine` | List workloads attributed to your member login |
| `nodus list team` | List workloads across your team |

Personal history requires a member-associated login. Shared keys can use team history.

`submit` also defaults to `nodus.toml` when no path is given.
`list --limit N` accepts 1 through 100.
Set your image, command, budget, and advanced options in a
[workload file](../getting-started/workload-files.md).

## Monitor and collect results

| Command | What it does |
|---|---|
| `nodus status ID` | Show status and current cost |
| `nodus wait ID` | Wait for a terminal status |
| `nodus logs ID` | Print committed logs |
| `nodus download ID` | Download published result files and archives under `outputs/ID` |
| `nodus cancel ID` | Request cancellation and remote cleanup |

Interactive waits show lifecycle events, live logs, elapsed time, and reported
training progress. Redirected output has no animation. `nodus logs` retrieves
saved log snapshots. Declare files as outputs to download them.
`logs --tail N` selects the last N lines, with 0 meaning all lines.
`logs --generation N` selects an attempt number starting at 1.

Ctrl+C during `run`, `wait`, or `events --follow` requests cancellation. Cleanup
happens remotely after acceptance. If the request fails, the CLI reports that
cancellation is unconfirmed and prints `nodus cancel ID`. A second Ctrl+C stops
the cancellation attempt. A wait timeout ends observation without cancelling.

If submission ends with an uncertain outcome, the CLI prints a recovery key.
Add that `idempotency_key` to the same workload file before retrying. Keep its
other settings unchanged to avoid submitting duplicate work.

## Agent sandboxes

SDK 0.5.1 accepts active names or exact sandbox IDs for the commands below.
Older releases require IDs for sandbox commands.

| Command | What it does |
|---|---|
| `nodus sandbox new IMAGE --name NAME --budget USD` | Admit a sandbox and print its ID while startup continues |
| `nodus sandbox ls` | Show sandbox IDs, names, states and costs |
| `nodus sandbox exec NAME_OR_ID COMMAND` | Run a command and stream its output |
| `nodus sandbox logs NAME_OR_ID EXEC_ID` | Read all currently stored command output |
| `nodus sandbox cost NAME_OR_ID` | Read the reported cost |
| `nodus sandbox rm NAME_OR_ID` | Request termination of the existing sandbox |

Name lookup selects one active exact match in your account. It never creates a
replacement. Use an ID for a terminated sandbox or when a name is ambiguous.
UUID-shaped sandbox IDs are treated as exact IDs even when absent. If you used
an ID-shaped name, use the actual ID returned at creation.
An accepted create request does not confirm runtime readiness, and accepting
termination does not confirm that remote cleanup has finished.

`sandbox new`, `sandbox exec` and `sandbox rm` accept
`--idempotency-key`. If a mutation has an uncertain outcome, preserve the printed
key and retry the unchanged operation. Use the printed sandbox ID when available.
For `exec`, put options before the sandbox reference so they are not interpreted
as part of the remote command.

If output observation fails after a command is accepted, the CLI prints its
execution ID and a `sandbox logs` command. Resume observation with that command
instead of submitting `exec` again.

```bash
nodus sandbox exec --idempotency-key tool-call-001 research-agent python agent.py
```

## Code and datasets

| Command | What it does |
|---|---|
| `nodus upload FILE` | Upload a file or archive and print its asset ID |
| `nodus assets` | List stored assets |
| `nodus asset get ID` | Inspect one asset and its safe export error |
| `nodus asset import-query CONNECTION SQL` | Export a database query and wait for its asset |

See [code and datasets](../guides/assets.md) for imports and attaching assets to work.

## Customer-owned compute

| Command | What it does |
|---|---|
| `nodus pools create NAME` | Register a customer-owned host pool |
| `nodus pools token POOL_ID` | Print a secret single-use enrollment token |
| `nodus pools token POOL_ID --mode execute --host-id HOST_ID` | Print a secret token for explicit reenrollment of one existing host |
| `nodus pools route POOL_ID off` | Disable new private admission while retaining cleanup |
| `nodus pools route-settings POOL_ID --wait-policy after_wait --wait-alpha 0.1` | Update future placement policy |
| `nodus pools hosts POOL_ID` | Inspect enrolled hosts |
| `nodus pools utilization POOL_ID --json` | Read measured utilization and host buckets |
| `nodus pools forecast POOL_ID --horizon 7 --json` | Read cached forecast evidence and the subscription rate |
| `nodus pools recommendations POOL_ID --state open --limit 25 --json` | Read one page of advice, following `--cursor` for older records |
| `nodus pools predict POOL_ID off` | Disable paid refresh for one pool |
| `nodus pools mark-done POOL_ID RECOMMENDATION_ID --outcome TEXT` | Record a manual outcome without executing a host action |

Observe is free. Predict activation requires explicit consent to its account
monthly charge. See [customer-owned pools](../guides/pools.md) for activation,
renewal, cached reads while paused, and optional reported savings. Enrollment
tokens are secrets and must not be written to shared logs.

## Advanced diagnostics

| Command | What it does |
|---|---|
| `nodus events ID` | Execution event history |
| `nodus artifacts ID` | Artifact manifests |
| `nodus explain ID` | Selected route and cost estimate |
| `nodus ledger ID` | Billing entries and settlement |

Use `nodus --debug COMMAND` for technical error details. Use command help for JSON output, polling, stage selection, and other diagnostic
options. Agents can use the [Python client](python/client.md) for structured
results without parsing terminal output.

| Exit code | Meaning |
|---|---|
| 0 | Command succeeded |
| 1 | Failed/cancelled workload, unavailable logs, or route not yet selected |
| 2 | API/configuration error or invalid CLI usage |
| 130 | Interrupted with Ctrl+C |

## Upgrading from 0.1

Replace `nodus get ID` with `nodus status ID`, and `nodus get ID --wait` with
`nodus wait ID`. Submission flags have moved into workload files. Use `nodus run`
to submit and wait, or `nodus submit` to return immediately. Python `client.get()`
and `client.run()` keep their existing behavior.

### Burst proposals

`nodus pools proposals POOL_ID` reads retained burst intent. Optional `--limit`
accepts 1 to 100, `--cursor` follows the returned continuation, and `--state`
filters `pending`, `approved`, `rejected`, `expired`, `no_op`, `applying`, or
`applied`. Use `--json` for the typed public response.

`nodus pools approve POOL_ID PROPOSAL_ID` approves the immutable proposed amount.
`nodus pools reject POOL_ID PROPOSAL_ID` rejects pending intent. These commands
require a current account admin. Approval does not itself rent capacity and does
not change the original expiry. Inspect the amount with `proposals` first.

### Database output load state

`nodus workload outputs WORKLOAD_ID` lists output names, sizes and database sink
load state. Add `--json` for the API fields. Retry a saved sink output with
`--reload NAME` and add `--stage STAGE` when output names repeat across stages.
Loading happens independently of workload completion.
