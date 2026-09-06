# Command-line reference

After [authentication](../getting-started/authentication.md), `nodus --help` and
`nodus COMMAND --help` show available options. Global `--base-url` precedes ordinary
subcommands. Login also accepts it after `login`. There is no API-key CLI flag.

| Command | Options / behavior |
|---|---|
| `login` | `--base-url`, `--no-browser`. Store device credentials |
| `logout` | Remove locally saved key |
| `run` | Submit. Prints workload ID |
| `list` | `--limit` (50), `--status` (one status, `active`, or `terminal`) |
| `get ID` | `--json`, `--wait`, `--poll` (2), `--timeout` (unbounded) |
| `events ID` | `--follow`, `--poll` (2). Prints sequence and type |
| `logs ID` | `--stage`, `--generation`, `--tail` (0, all lines) |
| `artifacts ID` | Manifest and object summaries |
| `explain ID` | Selected route and cost estimate |
| `ledger ID` | `--json`. Billing entries and settlement |
| `cancel ID` | Idempotent cancellation request |

## Submission flags

| Flag | Meaning / default |
|---|---|
| `--image` | Container image. SDK default `python:3.11-slim` |
| `--compute-class` | `vm` or `accelerator`. Current API defaults to accelerator |
| `--model` | Free-text workload hint |
| `--peak-memory-gb` | Memory requirement hint |
| `--hours` | Estimated runtime hours |
| `--budget` | Workload USD ceiling. Unset is uncapped at workload level |
| `--finish-by` | RFC3339 completion deadline |
| `--continuity` | `checkpointed` (default), `restartable`, `ephemeral` |
| `--data-region` | Allowed region. Repeat for several |
| `--idempotency-key` | Stable key for a logical submission |
| `--wait` | Wait after submission |
| `--timeout` | Bound waiting only, in seconds. Default unbounded |
| `--poll` | Poll interval in seconds. Default 2 |

Everything after `--` is the remote process argv. Put all Nodus flags before it:

```bash
nodus run --image python:3.11-slim --budget 5 --continuity restartable --wait   -- python -c 'print("CLI example")'
nodus list --status active --limit 10
nodus get WORKLOAD_ID --json
nodus events WORKLOAD_ID --follow
nodus logs WORKLOAD_ID --tail 50
```

Use Python for advanced dictionaries, multi-stage submission, downloads, and
multi-status filtering. CLI `--status` accepts one parser choice, not a
comma-separated list.

| Exit code | Meaning |
|---|---|
| 0 | Command succeeded |
| 1 | Failed/cancelled workload, unavailable logs, or route not yet selected |
| 2 | API/configuration error or invalid CLI usage |
| 130 | Interrupted locally with Ctrl-C |

Local interruption and wait timeouts do not cancel the remote workload.
