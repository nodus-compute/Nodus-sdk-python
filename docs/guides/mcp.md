# MCP tools

Connect an MCP client to Nodus to submit GPU workloads, inspect progress,
read logs and find output files. The `nodus-mcp` server runs locally over
standard input and output using MCP protocol version `2025-06-18`.

## Set up the server

You need a `nodus-mcp` executable for your machine and a Nodus API key.
The executable is separate from the Python SDK. Installing `nodus-compute`
with pip does not install the MCP server.

If you have access to the Nodus source repository, build it from that checkout:

```sh
go build -o nodus-mcp ./cmd/nodus-mcp
```

This requires the Go toolchain specified by the repository. If you do not have
source access or an executable from your team, request access from Nodus.
There is no hosted MCP URL to enter in your client. Configure a local command.

Set these environment variables for the process your MCP client starts:

| Variable | Value |
| --- | --- |
| `NODUS_API_KEY` | Required Nodus API key, supplied through your client's secret or environment settings |
| `NODUS_API_URL` | Optional API origin, defaults to `https://api.nodus.run`. Do not append `/v1` |

The MCP server reads `NODUS_API_KEY` directly. It does not read the Python SDK's
saved `nodus login` session or `NODUS_BASE_URL` setting. See
[authentication](../getting-started/authentication.md) for API key guidance.
Keep the key out of prompts, tool arguments and committed configuration files.

In your MCP client, set the server command to the absolute executable path.
For clients that accept an `mcpServers` JSON configuration, the shape is:

```json
{
  "mcpServers": {
    "nodus": {
      "command": "/absolute/path/to/nodus-mcp",
      "env": {
        "NODUS_API_KEY": "your-nodus-api-key"
      }
    }
  }
}
```

Replace the path and key placeholder in your local configuration. Reload the
client's MCP connection, then check that it discovers the seven tools below.
Call `list_workloads` with `{}` to check access without submitting compute.

## Tool reference

Arguments below are the JSON object passed to the named tool.

| Tool | Required arguments | Optional arguments | Result |
| --- | --- | --- | --- |
| `submit_workload` | `idempotency_key`, `workload` | None | The API's submission response, including the workload ID |
| `list_workloads` | None | `scope`, `limit`, `offset` | Workloads and `next_offset` when another page exists |
| `get_workload` | `workload_id` | None | Workload details, status and current meter |
| `cancel_workload` | `workload_id` | None | Cancellation requested, followed by asynchronous cleanup |
| `get_workload_events` | `workload_id` | `after` | Up to 100 lifecycle events after the supplied event ID |
| `get_workload_logs` | `workload_id` | None | Retained workload log text |
| `list_workload_outputs` | `workload_id` | None | Output metadata and download paths |

IDs and the idempotency key are nonempty strings. `workload` is the HTTP
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
machine. Use the authenticated HTTP download paths in the response or the
Python SDK's output download methods to retrieve them.

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

If the server exits with `NODUS_API_KEY is required`, set the key in the
launched process environment and reconnect. For authorization errors, verify
that the key belongs to the account that owns the workload. For `scope: mine`,
use a credential associated with a team member.

Custom API origins must use HTTPS, except for local loopback development.
HTTP redirects are refused. Set the final API origin directly. API requests
have a 30 second timeout. A timeout does not cancel a remote workload.

Log calls support the API's 8 MiB log payload plus truncation notices.
Responses larger than the server's 16 MiB bound return a tool error.
