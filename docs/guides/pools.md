# Measure your own GPU hosts

Sign in with `nodus login` or configure `NODUS_API_KEY`. Pools register
customer-owned GPU hosts for free, read-only measurement on deployments where
Compute is enabled. Your existing scheduler continues running your workloads.
Predict, routing onto these hosts, and automated actions are unavailable.

Create a pool with `nodus pools create Research`. The command prints its pool
ID. Run `nodus pools token POOL_ID`, replacing `POOL_ID` with that returned ID,
to issue an observe enrollment token. This command prints a secret. Keep the
token out of shared logs and source control. Each token can enroll one host
and expires after 24 hours.

Use the Compute enrollment panel's installation instructions on the NVIDIA
host. After enrollment, `nodus pools hosts POOL_ID` shows each host's ID, name,
health, agent mode, and device count. Hosts become lost when their heartbeat
has been absent for three minutes. Use the console to inspect their devices.

## Python methods

`client.pools` and `AsyncClient.pools` expose the same methods. Await methods
on the asynchronous client. IDs always come from the server.

| Method | Result |
|---|---|
| `create(name)` | A `Pool` configured for read-only measurement |
| `list()` | All pools owned by the authenticated team |
| `get(pool_id)` | A `Pool` with its current configuration |
| `update(pool_id, name=..., owned_cost_micros_per_hour=...)` | Updated `Pool`. Supply at least one setting |
| `enrollment_token(pool_id)` | An `EnrollmentToken` with `id`, `token`, `mode`, and `expires_at` |
| `hosts(pool_id)` | `PoolHost` objects with health, inventory, and `HostDevice` objects |
| `drain_host(pool_id, host_id)` | The host marked draining, without stopping customer processes |
| `remove_host(pool_id, host_id)` | Revokes the host credential and removes the host, preserving historical measurements |

`Pool.owned_cost_micros_per_hour` is your supplied hardware cost in USD micros
per hour. It does not create a charge. Missing server cost fields remain
`None`. Full pool and host metadata is available through their `raw` fields.
The token value is accessible through `EnrollmentToken.token` and is excluded
from its printed representation.

Pool requests do not automatically retry or follow redirects. After an
uncertain create response, inspect the pool list before creating another.
An uncertain token response may have issued a token whose secret was lost.
Issue a new token only when you intend to create another credential. Host
removal does not uninstall the agent or stop programs on the machine.
