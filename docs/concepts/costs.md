# Budgets and observed cost

| Value | Meaning |
|---|---|
| Submission `budget` | Per-workload cost-to-completion ceiling |
| Account spend cap | Account-level admission limit across workloads |
| `workload.cost_now_usd` | `max(spend_usd, meter.settled_usd) + meter.accruing_usd`; without a meter, `spend_usd` |
| `workload.spend_usd` | Settled workload charges |
| `workload.meter.as_of` | Timestamp of the observed live meter |
| `ledger.charged_usd` | Sum of customer-charge credits in the ledger |
| `ledger.settlement.balance_usd` | Residual accounting balance; not workload price |

Live cost includes accruing charges from open leases. Settled spend can lag
execution. A cleanly closed settlement can have a zero balance even when a
workload cost money. There is no `settlement.total_usd` field.

```python
workload = client.get(workload_id)
print(workload.cost_now_usd)
ledger = workload.ledger()
print(ledger.charged_usd, ledger.settlement.status)
```

A selected route's `expected_cost_usd` includes the router's expected recovery
cost, so it can exceed `price_usd_hour * expected_hours`. It is an estimate,
not the final ledger. `nodus explain WORKLOAD_ID` shows current route details.

Budget and account limits are separate. A workload can meet one and fail the
other. `BudgetExceededError` may include `monthly_cap_usd`, `month_to_date_usd`,
`estimated_cost_usd`, and `headroom_usd`; any may be absent. Reduce actual work or
adjust the applicable limit. Do not understate runtime merely to pass admission.
