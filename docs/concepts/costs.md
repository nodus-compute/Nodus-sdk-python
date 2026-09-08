# Budgets and observed cost

`budget` is an optional hard spending limit for one workload. Available team
credits and any configured account spending limit also apply. Without `budget`,
there is no separate limit for that run. Not every account has a monthly cap.
You do not need to provide an expected runtime.

Nodus starts work when the available spending allowance covers the selected
capacity's initial billing window. It checks the remaining allowance as work
continues and stops when more spending cannot be authorized. Acceptance does
not promise completion within your budget.

| Value | Meaning |
|---|---|
| `budget` | Optional hard limit in USD for one workload |
| Account spend cap | Shared monthly limit, when configured |
| Available credits | Team credit balance after charges and pending reservations |
| `workload.cost_now_usd` | Current settled and accruing customer cost |
| `workload.spend_usd` | Settled workload charges |
| `workload.meter.as_of` | Timestamp of the live meter |
| `ledger.charged_usd` | Settled customer charge |
| `ledger.settlement.balance_usd` | Accounting balance, not workload price |

Stopping and settlement can take time. The final customer charge stays within
the authorized allowance. Lowering a limit does not refund charges already
incurred or remove an existing authorization. It prevents further authorization
when no headroom remains.

```python
workload = client.get(workload_id)
print(workload.cost_now_usd)
ledger = workload.ledger()
print(ledger.charged_usd, ledger.settlement.status)
```

Route estimates are planning information, not a final bill or a required
customer input. Use the meter while running and the ledger after settlement.
Completion and resource cleanup can happen before financial settlement.
During the credit pilot, pending usage continues to reserve credits until
final accounting is available. A zero settled charge does not mean a free run.
`BudgetExceededError` includes available account headroom when the server can
provide it. Review the workload budget, credit balance, or account limit before retrying.
