# Observed cost and billing

Nodus records usage as your work runs. Customer spending caps and per-run,
sandbox, agent and benchmark budgets are not enforced. Payment requirements
and available team credits still apply.

Nodus checks that available credits cover the selected capacity's initial
billing window. It renews funding as work continues. Work can stop when the
account cannot fund additional usage. An accepted request does not guarantee
completion or a final price.

| Value | Meaning |
|---|---|
| Available credits | Team credit balance after charges and pending reservations |
| `workload.cost_now_usd` | Current settled and accruing customer cost |
| `workload.spend_usd` | Settled workload charges |
| `workload.meter.as_of` | Timestamp of the live meter |
| `ledger.charged_usd` | Settled customer charge |
| `ledger.settlement.balance_usd` | Accounting balance |

The meter separates compute, platform fees, storage, model and subscription
charges. Use its aggregate totals to follow spending. Stopping compute and
final settlement can take time. Pending usage retains its credit reservation
until accounting is complete.

```python
workload = client.get(workload_id)
print(workload.cost_now_usd)
ledger = workload.ledger()
print(ledger.charged_usd, ledger.settlement.status)
```

Route estimates are planning information. Use the live meter while running and
the ledger after settlement. Legacy `budget`, `budget_usd` and
`outcome.max_cost_usd` fields are accepted for compatibility and do not set a
spending limit. Cancel work or stop unused compute when you are finished.
