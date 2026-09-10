# Terminal commands

Use `nodus --help` for command groups and `nodus COMMAND --help` for options.
Replace `ID` with a workload ID. These commands describe SDK 0.3.x.

## Setup

| Command | What it does |
|---|---|
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
| `nodus download ID` | Download declared output files under `outputs/ID` |
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

## Code and datasets

| Command | What it does |
|---|---|
| `nodus upload FILE` | Upload a file or archive and print its asset ID |
| `nodus assets` | List stored assets |

See [code and datasets](../guides/assets.md) for imports and attaching assets to work.

## Estimate before submission

| Command | What it does |
|---|---|
| `nodus estimate` | Preview `nodus.toml` without creating a workload |
| `nodus estimate train.toml --stage main` | Preview one declared stage |
| `nodus estimate train.toml --json` | Print the server response, preserving null ranges |

See [estimate results](../guides/estimates.md) for statuses, expiry, and diagnostics.
An unavailable preview returns exit code 0 and reports why evidence is missing.

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
