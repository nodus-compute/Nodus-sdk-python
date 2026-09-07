# Submission parameters

`Client.run()` and `AsyncClient.run()` share this keyword-only interface. A brief
is translated to the nested HTTP request. The table covers every explicit
submission argument. Omitted flat values are generally absent from the wire,
except the default image, continuity policy, and balanced optimization.

| Python argument | HTTP location | Reference |
|---|---|---|
| `image`, `command` | `source.image`, `source.command` | [Source](source.md) |
| `source_asset_id`, `inputs`, `outputs` | Source asset and stage file declarations | [Files](source.md#input-and-output-files) |
| `framework` | `framework` | [Source](source.md) |
| `model`, `compute_class`, `peak_memory_gb`, `optimization`, `gpu` | `requirements.*` | [Resources](requirements.md) |
| `requirements` | `requirements` | [Resources](requirements.md) |
| `budget`, `finish_by` | `outcome.max_cost_usd`, `outcome.complete_by` | [Budget and deadline](outcome.md) |
| `continuity` | `continuity` | [Recovery](continuity.md) |
| `data_regions`, `policy` | `policy.data_regions`, `policy` | [Policy](policy.md) |
| `stages` | `stages` | [Stages](stages.md) |
| `idempotency_key` | `Idempotency-Key` header | [Safe retries](../../guides/ci-and-idempotency.md) |
| `extra` | Additional top-level fields | [Extensions](#extensions-and-validation) |

Do not pass `env` or `interrupt_tolerance`: they are explicitly unsupported. Unknown Python keywords raise `TypeError` before submission.
Raw dictionaries are advanced interfaces: use the deployed API schema, not
plausible-looking field names. See each page for precedence and defaults.

## Extensions and validation

`extra: dict` adds top-level request fields that the deployed server models but
this SDK version does not expose. It defaults to no additions and has no CLI flag.
It cannot replace a key already built in the request, including `requirements`,
`outcome`, or `continuity`. Collisions raise `TypeError` before network access.
Prefer named arguments and documented typed fields.

A non-colliding name is not proof that a server supports it. Unknown server
fields may be ignored on older deployments. Verify support in the deployed
contract before using extensions. Do not send secrets in arbitrary metadata.

Unsupported top-level arguments are `env` and `interrupt_tolerance`.
Stage inputs have a separate supported shape. See [stages](stages.md).
Python typos raise `TypeError`, distinct from a server `nodus.ValidationError`.
