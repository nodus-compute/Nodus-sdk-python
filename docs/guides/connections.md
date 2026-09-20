# External data connections

Connections are verified, team-owned references to credentials in the tenant
secret store. Supported kinds are `postgres`, `neon`, `supabase` and `wandb`.
The console shows a read-only list. Use the SDK or CLI to create, verify and
delete connections.

## Store a credential

Store a secret from a private UTF-8 file. The value is never returned by the
secret or connection read endpoints. Files contain the exact value, so omit a
trailing newline for API keys.

```sh
nodus secret set LAB_DB --from-file ./database-secret.txt
nodus connection add neon --name lab-db --secret LAB_DB --scope read --region us-east-1
nodus connection ls
nodus connection verify lab-db
```

`--secret` is an existing secret name or ID, never a credential value. Connection
names start with a letter and contain up to 128 letters, digits, underscores or
hyphens. The name `github` and prefix `conn_` are reserved.

| Kind | Secret file content | Additional connection fields |
| --- | --- | --- |
| `postgres` | A `postgres://` or `postgresql://` URL with user, password, hostname and database | None |
| `neon` | A Postgres URL, or JSON with `postgres_url` and optional `api_key` | Optional `branch` |
| `supabase` | JSON with `project_url`, `service_key` and `postgres_url` | None |
| `wandb` | The API key | Required `entity` and `project` |

Database JSON may include `ca_cert` containing a PEM certificate authority for
certificate verification. For Supabase, use the CA downloaded from the project's
database SSL settings. Its session pooler is supported when the direct database
address is unreachable over IPv4. The complete secret must fit the tenant secret
limit of 4096 UTF-8 bytes.

Database URLs support only `sslmode` and `channel_binding` query parameters.
Accepted SSL modes are `require`, `verify-ca` and `verify-full`. Every accepted
mode verifies the server certificate and hostname. TLS is also required when the
parameter is omitted. Arbitrary certificate file paths, plaintext connections,
private addresses and internal hostnames are refused.

Creating a database connection executes `SELECT 1` inside a read-only transaction.
Creating a wandb connection sends a GraphQL viewer query to `api.wandb.ai` with
the key. Verification has a five-second limit. Failure or timeout saves no
connection. Verification confirms authentication, not table permissions or
access to the selected wandb project.

## Python

```python
from nodus import Client

with Client() as client:
    connection = client.connections.create(
        "lab-db",
        "neon",
        secret="LAB_DB",
        scope="read",
        region="us-east-1",
    )
    connections = client.connections.list()
    metadata = client.connections.get(connection["id"])
    verified = client.connections.verify(connection["id"])
    client.connections.delete(connection["id"])
```

`AsyncClient.connections` exposes the same methods with `await`. Scope is `read`,
`write` or `readwrite`. Omitting it selects `read` for database kinds and `write`
for wandb. Region is optional. Connections with no region have no location
restriction.

A connection pins one immutable secret ID and version. Rotating or revoking that
version prevents new uses. Create a new connection to adopt a new version.
Deleting a connection removes its metadata without revoking the underlying
secret. A connection referenced by an export or load cannot be deleted.

## wandb live opt-in

```sh
nodus secret set WANDB_KEY --from-file ./wandb-key.txt
nodus connection add wandb --name lab-wandb --secret WANDB_KEY --entity lab-team --project training --live
nodus connection verify lab-wandb
nodus connection rm lab-wandb
```

Only a current team administrator can enable `live_mode`, using a console session
or an API key owned by that administrator. Member keys and ownerless machine
keys cannot enable it. The action is recorded in team activity.

Only wandb supports the live flag, with `write` or `readwrite` scope. Its declared
hosts are `api.wandb.ai`, `files.wandb.ai` and `storage.googleapis.com`. Database
connections keep credentials on the control plane.

## Attach live wandb to a run

Use an existing administrator-enabled wandb connection with write scope. A run
accepts one connection, by name or ID. Admission pins its metadata and exact
secret version. Rotation and connection deletion do not change an admitted run.
They prevent new admission using that connection. A connection region must match
`policy.data_regions` when that policy is supplied.

```python
from nodus import Client

with Client() as client:
    workload = client.run(
        command=["python", "train.py"],
        connections=["lab-wandb"],
        sweep_id="experiment-42",
        budget=5,
    )
    workload.refresh()
    for link in workload.links:
        print(link.url)
```

The workload image must already contain wandb and your training dependencies.
Your script calls `wandb.init()` normally. Nodus supplies `WANDB_API_KEY`,
`WANDB_ENTITY`, `WANDB_PROJECT`, `WANDB_RUN_GROUP` and `WANDB_NAME` in the process
environment. `NODUS_CONN_LAB_WANDB_KIND` is `wandb`. The default group is the Nodus
workload ID and the name includes the workload and stage IDs. Set the same scalar
`sweep_id` on several runs to group them. This does not schedule a sweep.

The key is delivered to the authenticated execution in memory. It is absent from
payloads, image layers, Docker environment files and checkpoint artifacts.
Captured logs redact the credential, including fragments split between writes.
Managed `WANDB_*`, `NODUS_CONN_*` and `NODUS_SECRET_*` names cannot be overridden
through submit or sandbox exec environment fields.

Live runs use an isolated network namespace. HTTPS can reach the union of the
connection's declared hosts and `policy.egress_allow`. HTTP, unlisted hosts,
private addresses and direct sockets are blocked. Proxy denials appear in
`workload.egress_denied` events. Additional tenant secret references can be
specified with `policy.secret_refs` and are pinned at admission. Deployments
without an enabled isolated execution provider refuse admission before acquiring
capacity. Explicit private pool placement is not supported for live runs.

Run links printed by wandb are validated against the pinned entity and project.
They appear in `workload.links`, workload detail and list responses, and the
console run row. `nodus run` prints a newly captured URL while waiting, including
with `--plain`. `nodus workload get` also displays captured links. Python callers
can use `workload.wait(on_update=callback)` to observe new links on each poll.
The sync and async clients support the same live fields.

A CLI workload file uses the same fields:

```toml
command = ["python", "train.py"]
connections = ["lab-wandb"]
sweep_id = "experiment-42"
budget = 5

[policy]
egress_allow = ["metrics.example.com"]
```

Sandboxes accept `connections=["lab-wandb"]` in `client.sandboxes.create` and
`Sandbox(...)`. Credentials follow the existing authenticated guest boot and
recovery channel. Reconnect preserves the admitted connection references and
secret versions. Sandbox live connections enforce the same HTTPS host union.
