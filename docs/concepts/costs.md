# Budgets and observed cost

| Value | Meaning |
|---|---|
| Submission `budget` | Per-workload cost-to-completion ceiling |
| Account spend cap | Account-level admission limit across workloads |
| `workload.cost_now_usd` | `max(spend_usd, meter.settled_usd) + meter.accruing_usd`. Without a meter, `spend_usd` |
| `workload.spend_usd` | Settled workload charges |
| `workload.meter.as_of` | Timestamp of the observed live meter |
| `ledger.charged_usd` | Sum of customer-charge credits in the ledger |
| `ledger.settlement.balance_usd` | Residual accounting balance. Not workload price |

Live cost includes accruing charges from open leases. Settled spend can lag
execution. A cleanly closed settlement can have a zero balance even when a
workload cost money. There is no `settlement.total_usd` field.

```python
workload = client.get(workload_id)
print(workload.cost_now_usd)
ledger = workload.ledger()
print(ledger.charged_usd, ledger.settlement.status)
```

When duration is unknown, Nodus reserves an initial execution window and
monitors spending as the run continues. `route.initial_reservation_usd` is
not a quote for the whole run. A route with a completion estimate uses
`expected_cost_usd` instead. `nodus explain WORKLOAD_ID` labels which is available.
You never need to supply a runtime estimate.

Budget and account limits are separate. A workload can meet one and fail the
other. `BudgetExceededError` may include `monthly_cap_usd`, `month_to_date_usd`,
`estimated_cost_usd`, and `headroom_usd`. Any may be absent. Reduce actual work or
adjust the applicable limit. Spending limits remain in force while work runs.
