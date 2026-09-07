# Budget and deadline

| Argument | Type | Omitted | CLI | HTTP field |
|---|---|---|---|---|
| `budget` | Positive finite number in USD | No per-workload ceiling. Warning emitted | `--budget` | `outcome.max_cost_usd` |
| `finish_by` | RFC3339 string or `datetime` | No completion deadline | `--finish-by` | `outcome.complete_by` |

Budget constrains cost to completion and admission. It is not a prepaid credit
or reserved capacity. The account spend cap also applies. Warnings about omitted
budgets apply to staged workloads too.

Inside a `with nodus.Client() as client:` block:

```python
from datetime import datetime, timedelta, timezone

workload = client.run(
    command=["python", "-c", "print('deadline example')"],
    budget=5,
    finish_by=datetime.now(timezone.utc) + timedelta(hours=2),
)
```

Use timezone-aware datetimes. A naive datetime is interpreted in the submitting
machine's local timezone before conversion to UTC. A string passes through for
server validation. Use a future timestamp including an offset or `Z`.

`finish_by` is a workload requirement, `expected_runtime_hours` is an estimate,
and `wait(timeout_seconds=...)` bounds client polling. They are different knobs.
See [costs](../../concepts/costs.md) for observed spend and settlement.
