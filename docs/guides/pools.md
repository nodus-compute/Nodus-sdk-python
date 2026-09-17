# Measure your own GPU hosts

Sign in with `nodus login` or configure `NODUS_API_KEY`. Pools register
customer-owned GPU hosts for free, read-only measurement on deployments where
Compute is enabled. Your existing scheduler continues running your workloads.
Predict adds an optional paid forecast and advisory recommendations. Routing
onto these hosts and automated actions are unavailable.

Create a pool with `nodus pools create Research`. The command prints its pool
ID. Run `nodus pools token POOL_ID`, replacing `POOL_ID` with that returned ID,
to issue an observe enrollment token. This command prints a secret. Keep the
token out of shared logs and source control. Each token can enroll one host
and expires after 24 hours.

Use the Compute enrollment panel's installation instructions on your Linux
host from your infrastructure provider or data center. After enrollment, `nodus pools hosts POOL_ID` shows each host's ID, name,
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
| `utilization(pool_id, from_=..., to=..., bucket=...)` | `PoolUtilization` with a summary, host summaries, and time buckets |
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


## Read the utilization ledger

Run `nodus pools utilization POOL_ID` to see measured allocated, busy, and
busy-of-allocated percentages. Add `--json` for host buckets and observed
foreign device IDs. All durations are device-seconds, except the Route-only
queued duration. Unknown readings are `None` in Python and `null` in JSON.
A measured zero is distinct from an unknown reading.

The default window is the last seven days of complete UTC hours. Use `--from`
and `--to` with RFC 3339 timestamps to choose a window of at most 31 days.
Both boundaries must align to a UTC hour. The end is exclusive. Use
`--bucket hour` or `--bucket day` to choose the grouping. The corresponding
Python keywords are `from_`, `to`, and `bucket`. Omitting them leaves defaults
to the server.

`PoolUtilization.summary` contains `UtilizationMetrics`. Each entry in
`hosts` contains a host summary and `buckets`. Every bucket carries `start`,
`end`, `metrics`, and `foreign_device_ids`. The response's `from_`, `to`, and
`bucket` identify the measured window. The unchanged JSON is in `raw`.

`data_status` is `complete`, `partial`, or `no_data`. Partial coverage hides
derived totals and percentages. Foreign device IDs still identify allocation
observed during a partial bucket. Summaries cover retained ready device time. A complete summary does not claim
continuous coverage of every host hour. Empty buckets report `no_data`.
Missing history is never counted as idle.
Fragmentation, queued, and burst metrics are unavailable without Route.


## Forecasts and advisory recommendations

Observe measurements remain free. Predict costs **$99 per account per UTC
calendar month**, with no additional pool or device fee. The first activation
charges the full current month without proration. Enabling another pool in an
already-active period adds no charge. An active period can reflect an accepted
postpaid charge and does not mean an invoice has been paid. Disable Predict on every pool to stop
future renewal. Disabling does not refund the current period.

Read `client.pools.forecast(pool_id, horizon=7)` or use
`nodus pools forecast POOL_ID --horizon 7 --json` to inspect the server's
current subscription rate and cached forecast. The supported horizons are
7 and 30 days. Omit `horizon` to use the server default of 30 days. Existing
cached forecasts and recommendations remain readable without a refresh
charge when Predict is disabled or paused. Only administrators can change
the subscription or record recommendation outcomes.

A forecast response contains `subscription`, `refresh_status`, and `snapshot`.
A missing snapshot remains `None`. The snapshot identifies its model, creation
time, history coverage, hourly p10, p50, and p90 device-hour bands, owned device
count, and any advisory market price. Four complete weeks of measured history
are required. The weekly seasonal baseline is identified explicitly. Missing
history does not become zero demand, and the SDK does not invent a learned
model or a market price.

The `calibration` object evaluates predictions issued before their target
hours against subsequently observed outcomes. Unknown coverage and pinball
losses remain `None`. Hourly coverage is the observed fraction within the
issued p10 to p90 band. Daily coverage counts fully evaluated UTC days whose
every hour fell within the band. A nominal hourly band does not guarantee
that a whole day falls inside it.

Enable Predict only after reviewing the returned price. Python callers use
`set_predict(pool_id, True, accepted_rate_version=...,
accepted_monthly_micros=...)`, supplying the exact rate version and integer
USD micros they accept. The SDK has no default consent or amount. With the
current rate, the CLI is:

```sh
nodus pools predict POOL_ID on \
  --accept-rate-version predict-account-monthly-v1 \
  --accept-monthly-micros 99000000
```

Disable with `client.pools.set_predict(pool_id, False)` or
`nodus pools predict POOL_ID off`. After an uncertain subscription response,
refresh the forecast response before deciding whether to try again.

Read advice with `client.pools.recommendations(pool_id)` or
`nodus pools recommendations POOL_ID --json`. Each recommendation includes
its expiration, advisory evidence, state, and any customer-reported outcome.
Rightsizing evidence exposes released device count, owned hourly cost,
expected burst device-hours, advisory burst price, and estimated savings.
These are scenario estimates, not measured savings or a guaranteed workload
completion price. Idle-reclaim advice identifies a host and device, sampled
low-utilization allocation, its recent trigger interval, and whether foreign
allocation was observed. Its estimated saving is unavailable. Nodus does not
identify or stop a customer process through this advice. Drain-window advice
identifies a host and a prospective UTC interval with at least three
consecutive hours whose forecast p90 demand is below one device. Its evidence
requires four complete weeks of host history. It is not an availability
guarantee, does not estimate a saving, and does not drain the host.
Placement-consolidation advice requires 336 complete hours of Route
observations and measured waits for requests needing multiple devices.
It reports workload-seconds above the stated policy threshold. Its packing
policy fills hosts first, excludes moving foreign jobs, and does not claim
an estimated saving. Missing Route observations produce no such advice.

Advice is paginated, with up to 100 records per response. Follow
`next_cursor` with the same pool and optional state filter:

```python
page = client.pools.recommendations(pool_id, state="expired", limit=25)
while page.next_cursor is not None:
    page = client.pools.recommendations(
        pool_id, state="expired", limit=25, cursor=page.next_cursor
    )
```

Omit `state` for all records, or use `open`, `done`, or `expired`.
The CLI accepts the same `--state`, `--limit`, and `--cursor` options and
prints the next cursor when older records remain.

After making a change yourself, record it with
`client.pools.recommendation_done(pool_id, recommendation_id, outcome)` or:

```sh
nodus pools mark-done POOL_ID RECOMMENDATION_ID \
  --outcome "Reduced capacity in our scheduler"
```

The returned `RecommendationOutcome` is explicitly customer-reported. Leave
`reported_saving_micros` unset when your saving is unknown. If you have an
independently assessed amount, pass nonnegative integer USD micros in Python
or add `--reported-saving-micros` in the CLI. Neither the SDK nor the console
copies an estimated saving into your reported outcome. Recording an outcome
does not execute a host action.

All these Python methods are also available on `AsyncClient.pools` and must
be awaited. Forecasts return `PoolForecast`, recommendations return
`PoolRecommendations`, and their unchanged response JSON is available in
`raw`. Subscription changes return `Pool`.
