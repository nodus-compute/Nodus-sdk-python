# Managed agent groups

An agent group accepts bounded batches of independent or dependent tasks for one
existing managed agent definition. Each task has its own managed run, journal,
session and writable state. Group admission requires an enabled account and
explicit quotas. Existing deployment execution limits still apply.

The [public API contract](../../openapi/openapi.yaml) describes the request fields.

## Create a group

Authenticate with your existing API key. Set `NODUS_BASE_URL` to your HTTPS API
origin without `/v1` and `NODUS_AGENT_ID` to an existing definition that accepts
your task inputs. Keep submission keys stable when retrying an uncertain response.

```sh
curl --fail --silent --show-error \
  --header "Authorization: Bearer ${NODUS_API_KEY}" \
  --header 'Content-Type: application/json' \
  --header 'Idempotency-Key: project-analysis-group' \
  --data "{\"name\":\"project-analysis\",\"agent_id\":\"${NODUS_AGENT_ID}\",\"budget_usd\":5,\"max_active\":1,\"max_held\":2}" \
  "${NODUS_BASE_URL}/v1/agent-groups"
```

Use the returned `id` as `NODUS_GROUP_ID`. The group pins the definition's current
revision unless you explicitly select an existing revision. Updating the
definition afterward does not change accepted group work.

The budget is a sublimit of the deployment's existing budget. Creating another
group does not create additional spending authority. `max_active` and `max_held`
cannot exceed the deployment's worker limit.

## Submit tasks

Submit at most 100 tasks per request, with a maximum complete body of 1 MiB.
Each input is valid JSON bounded to 256 KiB. Your agent entrypoint determines
what the input means.

```sh
curl --fail --silent --show-error \
  --header "Authorization: Bearer ${NODUS_API_KEY}" \
  --header 'Content-Type: application/json' \
  --header 'Idempotency-Key: project-analysis-page-1' \
  --data '{"tasks":[{"task_key":"inspect","input":{"action":"inspect"}},{"task_key":"verify","input":{"action":"verify"},"depends_on":["inspect"]}]}' \
  "${NODUS_BASE_URL}/v1/agent-groups/${NODUS_GROUP_ID}/runs"
```

Dependencies refer to task keys in the same group. They can reference existing
tasks or other tasks in the current batch, regardless of submission order.
Missing references, duplicate task keys within a batch and cycles reject the
entire batch. Existing tasks cannot gain new dependencies.

The dependent task waits until every prerequisite has committed success. A
cancelled or blocked prerequisite does not satisfy the dependency. Dependency
ordering does not copy results into another task's input or share writable
files. Retrieve each run's result through the existing managed-run API.

The batch receipt returns task keys and run IDs in request order. Retrying the
same batch key and content returns that receipt. Reusing a task key with the
same immutable content in another batch returns the original run. Changed
input, dependencies or deadline conflicts. Identity records survive payload
expiry, so a retry cannot silently start the task again.

## Observe progress and limits

```sh
curl --fail --silent --show-error \
  --header "Authorization: Bearer ${NODUS_API_KEY}" \
  "${NODUS_BASE_URL}/v1/agent-groups/${NODUS_GROUP_ID}"
curl --fail --silent --show-error --get \
  --header "Authorization: Bearer ${NODUS_API_KEY}" \
  --data-urlencode 'limit=50' \
  --data-urlencode "after=${NODUS_AFTER:-}" \
  "${NODUS_BASE_URL}/v1/agent-groups/${NODUS_GROUP_ID}/runs"
```

For task listing, set `NODUS_AFTER` to `next_after` and repeat until it is empty.
Each page is a separate database snapshot. A cursor from another group is
rejected. Missing and foreign-owned groups return HTTP 404.

`status` describes group admission. `work_status` summarizes the current tasks.
`logical_runs` gives individual state counts, and `allocations` retains cleanup
liability independently of task success. A completed result does not imply that
its compute and accounting have settled.

| Control | Consumed by |
| --- | --- |
| `max_pending` | All nonterminal tasks, including active, waiting and blocked work |
| `max_active` | Attempts authorized for execution in admitting, starting or running state, including retained waiting workers |
| `max_held` | Every unsettled allocation, including draining and uncertain attempts |
| `max_retained_runs` | Runs whose payloads have not expired, including completed and cancelled tasks |
| Existing workspace limits | Held or saved workspaces and their configured byte limits |

`active_authorized_attempts` can be compared with `max_active`.
`allocations.cleanup_pending_attempts` can be compared with `max_held`.
These are control-plane authorization and liability counts. They do not measure
productive model activity or prove that each runtime is healthy.

Pending defaults to 1,000 per group, bounded by account entitlement up to 10,000.
Raising a pending limit does not raise execution or storage limits. A retained
payload slot is released by the existing retention policy, not by task completion.
A run waiting for workspace capacity remains accepted and reports
`waiting_for_workspace_capacity` without discarding earlier saved state.

Other waiting reasons distinguish dependencies, group execution capacity,
allocation cleanup and budget exhaustion. `reserved_usd` includes full unsettled
attempt budgets, even when a closed marker lacks cleanup evidence. `cost_usd`
contains settled costs. Both remain within the existing deployment account.

## Cancel and recover a receipt

```sh
curl --fail --silent --show-error \
  --request POST \
  --header "Authorization: Bearer ${NODUS_API_KEY}" \
  --header 'Idempotency-Key: project-analysis-cancel' \
  "${NODUS_BASE_URL}/v1/agent-groups/${NODUS_GROUP_ID}/cancel"
```

Cancellation commits a durable fence against new group work. Compatible
executors then cancel nonterminal members and reconcile their resources.
Completed results remain available under the existing retention policy.
HTTP 202 confirms the fence, not completed physical cleanup.

Reads, cancellation and replay of accepted batch receipts remain available when
new group admission is disabled. Monitor allocation liability until cleanup is
settled. Do not use a new submission key to work around an uncertain response.
