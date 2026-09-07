# Budget and deadline

| Argument | Type | Omitted | Workload file | HTTP field |
|---|---|---|---|---|
| `budget` | Positive finite number in USD | Account spending limit only | `budget` | `outcome.max_cost_usd` |
| `finish_by` | RFC3339 string or `datetime` | No completion deadline | `finish_by` | `outcome.complete_by` |

Budget is a hard workload spending limit, not a completion-price promise.
The account spending limit also applies. Omit budget to use that account limit.

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

`finish_by` expresses a completion deadline. `wait(timeout_seconds=...)` only
bounds local observation and does not cancel the workload.
See [costs](../../concepts/costs.md) for observed spend and settlement.
