# Extensions and validation

`extra: dict` adds top-level request fields that the deployed server models but
this SDK version does not expose. It defaults to no additions and has no CLI flag.
It cannot replace a key already built in the request, including `requirements`,
`outcome`, or `continuity`. Collisions raise `TypeError` before network access.
Prefer named arguments and documented typed fields.

A non-colliding name is not proof that a server supports it. Unknown server
fields may be ignored on older deployments. Verify support in the deployed
contract before using extensions. Do not send secrets in arbitrary metadata.

Unsupported top-level arguments are `env`, `inputs`, and `interrupt_tolerance`.
Stage inputs have a separate supported shape. See [stages](stages.md).
Python typos raise `TypeError`, distinct from a server `nodus.ValidationError`.
