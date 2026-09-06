# Submission parameters

`Client.run()` and `AsyncClient.run()` share this keyword-only interface. A brief
is translated to the nested HTTP request. The table covers every explicit
submission argument. Omitted flat values are generally absent from the wire,
except the default image and continuity policy.

| Python argument | HTTP location | Reference |
|---|---|---|
| `image`, `command` | `source.image`, `source.command` | [Source](source.md) |
| `framework` | `framework` | [Source](source.md) |
| `model`, `compute_class`, `peak_memory_gb`, `expected_runtime_hours` | `requirements.*` | [Resources](requirements.md) |
| `requirements` | `requirements` | [Resources](requirements.md) |
| `budget`, `finish_by` | `outcome.max_cost_usd`, `outcome.complete_by` | [Budget and deadline](outcome.md) |
| `continuity` | `continuity` | [Recovery](continuity.md) |
| `data_regions`, `policy` | `policy.data_regions`, `policy` | [Policy](policy.md) |
| `stages` | `stages` | [Stages](stages.md) |
| `idempotency_key` | `Idempotency-Key` header | [Safe retries](../../guides/ci-and-idempotency.md) |
| `extra` | Additional top-level fields | [Extensions](extensions.md) |

Do not pass `env`, top-level `inputs`, or `interrupt_tolerance`: they are explicitly
unsupported. Unknown Python keywords raise `TypeError` before submission.
Raw dictionaries are advanced interfaces: use the deployed API schema, not
plausible-looking field names. See each page for precedence and defaults.
