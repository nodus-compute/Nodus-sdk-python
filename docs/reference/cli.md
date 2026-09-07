# Terminal commands

Use `nodus --help` for command groups and `nodus COMMAND --help` for options.
Replace `ID` with a workload ID. These commands describe SDK 0.3.0.

## Setup

| Command | What it does |
|---|---|
| `nodus login` | Open browser sign-in and save credentials |
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

Interactive waits show a spinner and elapsed time. Redirected output has no
animation. Logs are snapshots, not a live stdout stream. Files must be declared
as outputs to be downloadable.

Ctrl+C during `run`, `wait`, or `events --follow` requests cancellation. Cleanup
happens remotely after acceptance. If the request fails, the CLI reports that
cancellation is unconfirmed and prints `nodus cancel ID`. A second Ctrl+C stops
the cancellation attempt. A wait timeout ends observation without cancelling.

## Code and datasets

| Command | What it does |
|---|---|
| `nodus upload FILE` | Upload a file or archive and print its asset ID |
| `nodus assets` | List stored assets |

See [code and datasets](../guides/assets.md) for imports and attaching assets to work.

## Assets

| Command | What it does |
|---|---|
| `nodus upload FILE` | Upload a file or archive and print its asset ID |
| `nodus assets` | List your uploaded and imported assets |

Put the returned ID in `source_asset_id` or an input entry in your workload file.
For imports and programmatic uploads, see [code and datasets](../guides/assets.md).

## Advanced diagnostics

| Command | What it does |
|---|---|
| `nodus events ID` | Execution event history |
| `nodus artifacts ID` | Artifact manifests |
| `nodus explain ID` | Selected route and cost estimate |
| `nodus ledger ID` | Billing entries and settlement |

Use command help for JSON output, polling, stage selection, and other diagnostic
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

## Terminal output

`list` includes column headings. `status` shows the run, status, placement, and
current cost. `run` and `wait` show elapsed time, events, and available logs.
Use `--json` where supported for structured output. `submit` prints only the
workload ID so it can be captured by scripts. Colors adapt to the terminal and
respect `NO_COLOR`.
