# CLI

`pip install nodus_compute` puts a `nodus` command on your PATH. It reads the
same two environment variables as the SDK — see
[Getting Started](Getting-Started).

```bash
nodus --version
nodus --base-url https://… list      # global flags go BEFORE the subcommand
```

There is no `--api-key` flag; the key comes from `NODUS_API_KEY` only, so it
never lands in your shell history or a process listing.

## The `--` rule

Everything after the first bare `--` is the command run **inside** the
workload. Your program's own flags do not have to survive two layers of
parsing, and nothing needs escaping:

```bash
nodus run --budget 20 -- python train.py --epochs 3 --lr 1e-4
```

Without `--`, `--epochs` would be read as a flag for `nodus`. The split happens
on the raw arguments before anything else is parsed, so put it after all the
`nodus run` flags and before your program.

## `nodus run`

```bash
nodus run --model "7B fine-tune" --image python:3.11-slim \
  --peak-memory-gb 24 --hours 6 --budget 40 --wait \
  -- python train.py --epochs 3
```

Prints the workload id immediately. With `--wait` it then blocks and prints one
summary line when the run is over.

| Flag | Default |
|---|---|
| `--model`, `--image`, `--finish-by`, `--idempotency-key` | unset |
| `--peak-memory-gb`, `--hours`, `--budget` | unset (**no budget means uncapped**) |
| `--continuity` | `checkpointed` — or `restartable`, `ephemeral` |
| `--data-region` | unset; repeat the flag for several |
| `--wait` / `--timeout` / `--poll` | off / none (no deadline of its own) / `2.0` s |

Omitting `--budget` prints a `UserWarning` on stderr and submits an uncapped
run. See [The Brief](The-Brief).

## `nodus list`

```bash
nodus list
nodus list --status active --limit 10
nodus list --status completed,failed
```

Newest first. `--status` takes `active`, `terminal`, a concrete status, or a
comma-separated list. One line per workload —

```
wl_0192…  running       nodus:a100-80-us-east        $8.50
```

— and that dollar figure is `cost_now_usd`, settled plus accruing, so running
rows show what they are actually costing. See [Costs](Costs).

## `nodus get` and `nodus events`

```bash
nodus get wl_0192…
nodus get wl_0192… --json          # the whole response body
nodus get wl_0192… --wait --poll 5 --timeout 3600

nodus events wl_0192…              # everything recorded so far
nodus events wl_0192… --follow     # live, until the workload is terminal
```

`events` prints `seq` and event type, one per line. `--follow` outlives a
transient network failure and stops on a permanent one.

## `nodus logs`

```bash
nodus logs wl_0192…
nodus logs wl_0192… --tail 50
nodus logs wl_0192… --stage stg_train --generation 3
```

The log is a committed artifact, not a live tail. Before the first checkpoint
carries one you get a sentence saying so, and exit code 1 — that is the normal
answer for a young workload, not a fault. `--generation` picks which attempt to
read after a reclaim; see
[Waiting and Reliability](Waiting-and-Reliability).

## `nodus artifacts`

One line per committed manifest, then one per object it names — `final` marks
the manifest that completed the stage, and the rest are checkpoints, which exist
to be restored from rather than consumed.

```
$ nodus artifacts wl_0192…
stg_train  gen2/seq7  final  cm_1
    output model  cdcdcdcdcdcd  20B
    file   key:wl_1/stg_train/gen2/seq7/checkpoint.bin  abababababab  10B
```

## `nodus explain`

Why this route — read back from the control plane that made the decision, so
what you see is the routing that happened, not a re-derivation of it. Exits 1
if the workload was accepted but not placed yet.

```
$ nodus explain wl_0192…
workload  wl_0192…

  catalog SKU            nodus:a100-80-us-east
  fit                    a100-80  ·  80 GB  ·  us-east
  rate                   $2.5000/h
  expected hours         18.00
  expected cost          $300.00
  remaining budget       $100.00

  expected cost is cost to completion: the run plus the recovery reserve,
  not rate x hours. It is the number the budget is checked against.
```

## `nodus ledger` and `nodus cancel`

```bash
nodus ledger wl_0192…            # entries, then the settlement line
nodus ledger wl_0192… --json
nodus cancel wl_0192…            # requests a safe stop; idempotent
```

## Exit codes

| Code | Meaning |
|---|---|
| `0` | Success |
| `1` | The question was answered, and the answer is no: `run --wait` or `get` on a workload that ended failed or cancelled, `logs` with nothing recorded yet, `explain` before a route exists |
| `2` | A Nodus error reached the top; the message is on stderr as `error: …`. Also what argparse exits with on a usage mistake |
| `130` | Ctrl-C |

Because a failed workload exits 1 and an API problem exits 2, this composes in
CI without parsing output:

```bash
nodus run --budget 20 --wait -- python train.py || exit $?
```
