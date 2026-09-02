# Costs

## The one number to read: `cost_now_usd`

```python
wl = client.get("wl_…")
print(wl.cost_now_usd)     # what this workload has cost, as of that read
```

Use this everywhere. `spend_usd` is the other number and it will mislead you if
you reach for it first.

| Field | What it is |
|---|---|
| `spend_usd` | **Settled charges only.** A charge is booked when a lease closes, so this does not move while your work runs — a running workload reads `0.0` here. |
| `cost_now_usd` | Settled **plus** what is accruing right now. This is the answer to "what is this costing me". |

`cost_now_usd` is a property, not a field: it returns the meter's total when the
response carried one, and falls back to `spend_usd` when it did not.

## The meter

The API sends a meter on reads and on list rows precisely so a running row does
not claim it cost nothing. It is `None` until a response carries one.

```python
m = wl.meter
if m:
    print(m.settled_usd)             # closed leases
    print(m.accruing_usd)            # open lease, not yet booked
    print(m.accruing_rate_usd_hour)  # what ticks it forward between polls
    print(m.total_now_usd)           # settled + accruing == wl.cost_now_usd
    print(m.as_of)                   # the instant that total was true
```

`as_of` is part of the number, not decoration: a live figure without the instant
it was true cannot be read. `client.list()` rows carry the meter too, so a
dashboard of running workloads shows real figures rather than a column of zeros.

## Two different ceilings

There are two, they are set in different places, and they fail differently.

**`budget=` — per workload, set in the brief.** A ceiling on *cost to
completion* in USD. Omit it and the run has no ceiling of its own; see
[The Brief](The-Brief). What the brief asked for comes back on the handle as
`budget_usd`, so you can always see what a run is being held to.

**The monthly spend cap — per account, set in the console.** Applies across
every workload on the key. A submission that would breach it is refused with
`BudgetExceededError` (HTTP 402) before anything is placed.

A brief can pass one and fail the other. A $25 budget on an account with $10 of
headroom left is refused.

## Cost to completion, not rate × hours

The route chosen for a workload carries the arithmetic:

```python
r = wl.route
r.sku                    # the Nodus catalog route, e.g. "nodus:a100-80-us-east"
r.fit_class              # the capability class behind it
r.price_usd_hour
r.expected_hours
r.expected_cost_usd      # cost to completion
r.remaining_budget_usd
r.interruptible
```

`expected_cost_usd` is the run **plus the recovery the router expects to pay for
on this route**. That is why it can be more than `price_usd_hour ×
expected_hours`, and it is the number your `budget` is checked against. The
`nodus explain` command prints exactly this; see [CLI](CLI).

## When a budget refuses you

`BudgetExceededError` carries the whole arithmetic as numbers, so you can
resubmit against real headroom instead of guessing:

```python
try:
    wl = client.run(command=["python", "train.py"], budget=400)
except nodus.BudgetExceededError as e:
    print(e.monthly_cap_usd)      # the cap on the key this billing period
    print(e.month_to_date_usd)    # settled spend so far this period
    print(e.estimated_cost_usd)   # what the router priced this brief at
    print(e.headroom_usd)         # what is left under the cap
```

Any of them can be `None` if the server did not send that field. `headroom_usd`
is computed from cap minus month-to-date when the server did not state it
directly.

What to do about it: raise the cap in the console, or lower `budget` or
`expected_runtime_hours` and resubmit. The error message says this too — see
[Errors](Errors).

## The ledger

Billing evidence for one workload, once there is any:

```python
led = wl.ledger()

for e in led.entries:
    print(e.entry_type, e.debit_usd, e.credit_usd, e.currency, e.created_at)
    print(e.evidence)             # what the entry was booked from

print(led.settlement.status)      # "none" until it settles
print(led.settlement.total_usd)
```

Entries are the audit trail; `settlement` is the final figure. Both are also
available from the command line as `nodus ledger <id>` or, whole, as
`nodus ledger <id> --json`.

## A note on timing

Because charges are booked when leases close, these three can legitimately
disagree at any given instant:

- `cost_now_usd` — settled plus accruing, as of `meter.as_of`
- `spend_usd` — settled only
- `ledger().settlement.total_usd` — the settled total once settlement has run

On a finished, settled workload they converge. Mid-run they do not, and
`cost_now_usd` is the honest one.
