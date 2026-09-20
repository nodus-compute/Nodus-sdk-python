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

Connection creation is submitted once and is not automatically retried, including
on transport errors or transient HTTP responses. If the response is lost, the
connection may already exist. Look it up with `client.connections.get("lab-db")`
or `nodus connection ls` before submitting another create request. The name is
unique within your team.

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
connections keep credentials on the control plane. Database query imports are
described below.

## Import a database query

Export a query to a normal input asset. The query runs on the control plane and
the database credential never reaches the workload.

```python
from nodus import Client

with Client() as client:
    dataset = client.assets.import_query(
        "lab-db", "SELECT id, uri, label FROM clips WHERE split='train'"
    )
    metadata = client.assets.get(dataset.id)
    print(metadata.export["row_count"])
```

Pass `dataset.id` in your workload's `inputs`, for example
`inputs={"clips": dataset.id}`. The input is a directory containing
`data.parquet`. A training program can read it with
`pandas.read_parquet(os.path.join(os.environ["NODUS_INPUT_clips"], "data.parquet"))`.
Install the appropriate Parquet reader in your training environment.

```sh
nodus asset import-query lab-db "SELECT id, uri, label FROM clips" --format parquet
nodus asset import-query lab-db "SELECT id, uri, label FROM clips" --format csv --reuse
```

`AsyncClient.assets.import_query` and `get` provide the same interface with
`await`. `format` defaults to `parquet`. CSV exports contain a header and use an
empty field for SQL nulls. Parquet preserves nullable integer, floating point,
boolean, UTF-8 text, UTC microsecond timestamp and JSON logical types. Other
Postgres types become UTF-8 strings. Use unique nonempty column aliases.

Queries must begin with `SELECT` or `WITH`. Each export uses a read-only
transaction, a ten-minute timeout and 10000-row cursor batches. Modifying CTEs
and multiple statements are refused. The maximum is 5 GB or 50 million rows,
with no request override. Existing storage quota can impose a smaller byte
limit. A limit error includes the row count reached.

`reuse=True` may return an existing ready asset from the last 24 hours for the
same team, connection, SQL, branch and format. Only outer SQL whitespace is
ignored. Rotated or revoked credentials cannot create or reuse an export.
`reuse=False` creates a fresh asset. An optional `branch` must match the Neon
branch already configured and verified on the connection. Create a separate
connection to use another branch.

An admitted export keeps its original credential version even if the secret is
rotated during execution. Delete the export asset before removing its connection.
Failed imports release their asset reservation after object cleanup succeeds.
