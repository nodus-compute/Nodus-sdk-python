# MCP tools

Connect Claude, Cursor, Codex or another MCP client to Nodus. Ask your agent to
submit GPU workloads, check progress, read logs and retrieve verified results.

For the shortest setup, [choose your coding agent](connect.md). It includes
copyable commands, a Cursor install link and configurations for other clients.

For Codex, Claude Code or Cursor, use the [Nodus plugin](plugins.md) to install
the tools and setup guidance together. The manual configuration below works
with other MCP clients too.

## Hosted connection

Use the [connection page](connect.md#quick-connection) for hosted HTTP MCP with
browser authorization. Tools use a revocable grant bound to your account and
team. Read-only grants omit submission and cancellation tools.

Hosted `get_workload_output` returns a download URL valid for ten minutes,
plus the file's SHA-256 and byte count. Download with your agent's own file
tools, without an Authorization header and without following redirects.
Verify the checksum before reporting delivery. Treat the URL as a secret.
The hosted server cannot write to your local filesystem. Revoking the
connection invalidates its download links.

## Local connection in two steps

You need [uv](https://docs.astral.sh/uv/getting-started/installation/) installed.
`uvx` downloads the public Nodus package and starts the server for your client.
It manages the Python runtime and package dependencies for you.

**1. Sign in once.** Run this in your terminal and complete browser sign-in:

```sh
uvx --from 'nodus-compute[mcp]==0.5.2' nodus login
```

**2. Add Nodus to your MCP client.** In Claude Desktop or Cursor, add this to
your MCP server configuration and reload the connection:

```json
{
  "mcpServers": {
    "nodus": {
      "command": "uvx",
      "args": ["--from", "nodus-compute[mcp]==0.5.2", "nodus-mcp"]
    }
  }
}
```

For Codex, run this instead of editing JSON:

```sh
codex mcp add nodus -- uvx --from 'nodus-compute[mcp]==0.5.2' nodus-mcp
```

The server uses your saved sign-in. There is no API key to paste into the
configuration, private repository to clone or executable to compile.

Ask your agent: **"List my Nodus workloads."** This checks the connection
without starting paid compute. Your local client should discover nine tools.

## Already using pip?

Install the MCP extra and reuse your existing Nodus sign-in:

```sh
pip install --upgrade 'nodus-compute[mcp]==0.5.2'
nodus login
```

Set your client's command to `nodus` and its arguments to `["mcp"]`.
The `nodus-mcp` executable is also installed by the package. Both commands
start the same local server over standard input and output.

## Authentication and custom deployments

The server reads the same saved credentials as the Python SDK. For automation,
set `NODUS_API_KEY` in the server process environment. For a custom deployment,
set `NODUS_BASE_URL` to its API origin without `/v1`, or sign in with
`nodus login --base-url https://your-api.example`.

Environment settings take priority over saved settings. Keep keys out of
prompts, tool arguments and committed files. See
[authentication](../getting-started/authentication.md) for more details.

## Tool reference

Arguments below are the JSON object passed to the named tool. Hosted connections
use `get_workload_output` instead of `download_workload_output`. Local downloads
write on the machine running MCP and require an existing destination directory.

| Tool | Required arguments | Optional arguments | Result |
| --- | --- | --- | --- |
| `validate_workload` | `workload` | None | Validation without admission, spending or capacity reservation |
| `download_workload_output` | `workload_id`, `name`, `destination` | `stage` | Local verified download, existing files refused |
| `submit_workload` | `idempotency_key`, `workload` | None | The API's submission response, including the workload ID |
| `list_workloads` | None | `scope`, `limit`, `offset` | Workloads and `next_offset` when another page exists |
| `get_workload` | `workload_id` | None | Workload details, status and current meter |
| `cancel_workload` | `workload_id` | None | Cancellation requested, followed by asynchronous cleanup |
| `get_workload_events` | `workload_id` | `after` | Up to 100 lifecycle events after the supplied event ID |
| `get_workload_logs` | `workload_id` | None | Retained workload log text |
| `list_workload_outputs` | `workload_id` | None | Output metadata and download paths |

Workload IDs contain only letters, digits, underscores and hyphens. The
idempotency key is a nonempty printable ASCII string without spaces or line
breaks. `workload` is the HTTP
workload request object, not the keyword arguments to Python `client.run()`.
For example, the HTTP budget field is `outcome.max_cost_usd`, not `budget`.
Use the [OpenAPI contract](../../openapi/openapi.yaml) for the full request
schema and the [parameter reference](../reference/parameters/index.md) for
field descriptions.

`scope` accepts `team` or `mine`. Omitted scope uses the team's workloads.
`mine` requires a credential associated with a team member. `limit` accepts
integers from 1 to 100. `offset` accepts integers from 0 to 2147483647.
`after` accepts integer event IDs from 0 to 9007199254740991.

The tools return MCP text content containing API JSON or log text. API failures
return a tool result with `isError: true` and the API error details. These are
workload tools. Use the [sandbox guide](agent-sandboxes.md) for interactive
sandbox commands and streaming output.

## Submit and monitor a workload

This example checks the remote GPU and allows up to $1 in workload spending.
Choose a budget within your authorization before submitting. An accepted
request does not guarantee completion within that limit.

Call `submit_workload` with:

```json
{
  "idempotency_key": "gpu-check-unique-run-id",
  "workload": {
    "source": {
      "image": "pytorch/pytorch:2.8.0-cuda12.8-cudnn9-runtime",
      "command": [
        "python",
        "-c",
        "import torch\nassert torch.cuda.is_available()\nprint(torch.cuda.get_device_name(0))"
      ]
    },
    "requirements": {
      "gpu_count": 1
    },
    "outcome": {
      "max_cost_usd": 1
    }
  }
}
```

Choose a new unique key for each intentional run. If a submission times out,
retry the exact same request with its original key. Do not create a second
paid run by changing the key during an uncertain retry.

Save the returned workload ID. Pass it to `get_workload`,
`get_workload_logs` or `list_workload_outputs`:

```json
{
  "workload_id": "wl_your_workload_id"
}
```

Check workload status until terminal and verify it succeeded before reporting
success. The example writes its result to the log. For downloadable files,
your program must write the output files described in
[logs and results](monitoring-and-outputs.md).

`list_workload_outputs` lists metadata. It does not download files to your
machine. Call `download_workload_output` locally or `get_workload_output`
on a hosted connection to retrieve them.

## Read subsequent pages

For `list_workloads`, pass the response's `next_offset` in the next call.
For example, when `next_offset` is 20:

```json
{
  "limit": 20,
  "offset": 20
}
```

For `get_workload_events`, pass the last event's `id` as `after`. For example,
when the last event ID is 100:

```json
{
  "workload_id": "wl_your_workload_id",
  "after": 100
}
```

Keep the cursor to read later events without requesting the first page again.
An empty page means there are no later events at that moment.

## Cancel and troubleshoot

To stop a workload, call `cancel_workload` with only `workload_id`.
Cancellation does not take an idempotency key or workload body and can be
requested again for the same workload. Continue checking `get_workload`
until the workload reaches a terminal state. A cancellation acknowledgement
does not mean resource cleanup has finished.

If your client cannot find `uvx`, restart the client after installing uv or
set `command` to the full path printed by `command -v uvx` on macOS and Linux,
or `where.exe uvx` on Windows.

If a tool asks you to sign in, run the sign-in command above from the same
computer and user account as the MCP client. For expired or rejected sign-in,
run it with `--force`. For `scope: mine`, use a credential associated with a
team member.

If `nodus mcp` asks for MCP support, install the `[mcp]` extra using the pip
command above. The plain Python SDK install keeps MCP dependencies optional.

Custom API origins must use HTTPS, except for local loopback development.
HTTP redirects are refused. Set the final API origin directly. The server
reuses HTTP connections between tool calls and closes them on shutdown.
Saved sign-in changes apply to the next call without restarting the server.
API requests have a 30 second timeout. A timeout does not cancel a remote workload.

Log calls support the API's 8 MiB log payload plus truncation notices.
Responses larger than the server's 16 MiB bound return a tool error.
