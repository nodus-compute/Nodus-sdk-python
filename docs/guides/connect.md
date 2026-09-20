# Connect your coding agent

Connect Nodus to Claude Code, Codex, Cursor or another coding agent. Your agent
can submit GPU workloads, follow progress, inspect logs and retrieve output
metadata. Use the [connection page](https://nodus-compute.ai/connect/) for
copy buttons and the Cursor install link.

## Quick connection

Run one command in your terminal. Setup installs its own tools and Python,
then lets you choose one or more agents. It opens browser sign-in, adds Nodus
tools and skills, and verifies the tools by listing workloads. No paid compute
starts during setup.

macOS or glibc Linux, on Intel or ARM64:

```sh
curl -fsSL https://nodus-compute.ai/install | sh
```

64-bit Windows PowerShell:

```powershell
irm https://nodus-compute.ai/install.ps1 | iex
```

Choose Claude Code, Codex, Cursor, VS Code, Gemini CLI or OpenCode. Select
several to connect them together. Then restart the selected agents and approve
Nodus if the client asks. Ask **List my Nodus workloads.**

Setup uses your user configuration. VS Code uses its default user profile.
Other MCP clients can import the generated `~/.nodus/mcp.json` themselves.
Run setup on the machine where the agent runs, including remote environments.

Existing settings and servers are preserved. Modified files receive an adjacent
`.nodus-backup-…` copy. JSON comments and formatting are normalized in the active
file, while the backup keeps the original bytes. An existing different Nodus
entry, an invalid file or a symbolic link stops setup with instructions.
Repeating the same setup keeps matching Nodus entries and skills.

The installer uses a dedicated runtime under `~/.nodus/agent-tools`, so agents
do not depend on your terminal's PATH. It does not require administrator access.
Skills are named `nodus-setup` and `nodus-workloads`. If you already use the
Nodus plugin, keep that installation instead of adding a second connection.

You can inspect the [shell installer](https://nodus-compute.ai/install) or
[PowerShell installer](https://nodus-compute.ai/install.ps1) before running it.
Both are generated from the public [installer source](../../install/connect.py)
and the pinned plugin package. The individual client instructions below remain
available for manual setup and custom profiles.

## Sign in once

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), then run
this in your terminal and complete browser sign-in:

```sh
uvx --from 'nodus-compute[mcp]==0.4.2' nodus login
```

The package downloads automatically. Local clients running as the same OS
user reuse this login. Sign in on the machine where the MCP server runs,
including remote development environments. Keep API keys out of chat and
configuration files. See [authentication](../getting-started/authentication.md)
for unattended environments and custom deployments.

## Claude Code

Run this in your terminal to add Nodus across your projects:

```sh
claude mcp add --scope user --transport stdio nodus -- uvx --from 'nodus-compute[mcp]==0.4.2' nodus-mcp
```

Restart Claude Code or reconnect through `/mcp`.

## Codex

Run this in your terminal, then start a new Codex session:

```sh
codex mcp add nodus -- uvx --from 'nodus-compute[mcp]==0.4.2' nodus-mcp
```

## Cursor

Select Cursor on the [connection page](https://nodus-compute.ai/connect/) and
click **Add to Cursor**. Review the configuration in Cursor and enable Nodus.
For manual setup, merge this into `~/.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "nodus": {
      "command": "uvx",
      "args": ["--from", "nodus-compute[mcp]==0.4.2", "nodus-mcp"]
    }
  }
}
```

Keep existing servers. The same file is available at
[mcp.json](https://nodus-compute.ai/mcp.json). It is configuration, not a
hosted MCP endpoint.

## Verify the connection

Reload the client's MCP connection or start a new session. Ask your agent:

```text
List my Nodus workloads.
```

The agent should discover seven tools and call `list_workloads`. Check its
actual response. An empty list is valid. This read does not start paid compute.
If it fails, use the [MCP troubleshooting guide](mcp.md#cancel-and-troubleshoot).

For your first workload, provide your image, command, GPU requirements and
spending limit. Ask the agent to prepare your command with a maximum you
specify and show the request before submission. Do not invent a budget or
start compute to test the connection. The [workload guide](agents.md) covers
execution and downloaded result verification. MCP output listing returns
metadata. Use the SDK or authenticated API to download the files you need.

## VS Code and GitHub Copilot

Merge this into `.vscode/mcp.json` in your project, then enable Nodus in chat:

```json
{
  "servers": {
    "nodus": {
      "type": "stdio",
      "command": "uvx",
      "args": ["--from", "nodus-compute[mcp]==0.4.2", "nodus-mcp"]
    }
  }
}
```

For all workspaces, run **MCP: Open User Configuration** in the command palette
and merge it there. See [VS Code MCP setup](https://code.visualstudio.com/docs/agent-customization/mcp-servers).

## Gemini CLI

Merge the `mcpServers` configuration from the Cursor section into
`~/.gemini/settings.json`, then restart Gemini CLI. Run `/mcp list` to inspect
the connection. See [Gemini CLI MCP setup](https://geminicli.com/docs/tools/mcp-server/).

## OpenCode

Merge this into `opencode.json` in your project:

```json
{
  "mcp": {
    "nodus": {
      "type": "local",
      "command": ["uvx", "--from", "nodus-compute[mcp]==0.4.2", "nodus-mcp"],
      "enabled": true
    }
  }
}
```

Restart OpenCode. See [OpenCode MCP setup](https://opencode.ai/docs/mcp-servers/).

## Other MCP clients

Choose a **local** or **stdio** server in your client's MCP settings. Set the
command to `uvx` and the arguments to
`["--from", "nodus-compute[mcp]==0.4.2", "nodus-mcp"]`.
For clients that accept an `mcpServers` object, merge the configuration from
the Cursor section. Preserve unrelated settings and servers.

| Client | Where to add the local MCP server |
| --- | --- |
| Claude Desktop | Settings, Developer, Edit Config |
| Windsurf legacy Cascade | MCP settings, View raw config |
| Cline | MCP Servers, Configure MCP Servers |
| Other local MCP clients | Their stdio server configuration, using the command and arguments above |

See the current setup guides for
[Claude Desktop](https://modelcontextprotocol.io/docs/develop/connect-local-servers),
[Windsurf](https://docs.windsurf.com/windsurf/cascade/mcp) and
[Cline](https://docs.cline.bot/mcp/configuring-mcp-servers).
Client policy or administrator settings can restrict local servers.
For the Devin Local agent, use the Devin CLI configuration described in
the linked Windsurf documentation.

Nodus supplies a local stdio server. A client that only accepts a hosted HTTP
MCP URL cannot launch it directly. Use the Python SDK or customer API from an
execution environment instead. Do not enter the Nodus API URL as an MCP URL.
See the [MCP reference](mcp.md) for the seven workload tools.

## Agent skills

Install Nodus's setup and workload guidance in agents that support skills:

```sh
npx skills add nodus-compute/Nodus-sdk-python
```

Requires Node.js. Choose the `setup` and `workloads` skills and your target
agent when prompted. See the [skills CLI](https://skills.sh/docs/cli).
Skills provide instructions, not the MCP connection. Complete setup above
separately. The [Nodus plugins](plugins.md) bundle both skills and MCP for
Claude Code, Codex and Cursor. Choose one MCP installation method per client.

The same skill files are readable without installation:

- [Setup skill](https://nodus-compute.ai/skills/setup/SKILL.md)
- [Workload skill](https://nodus-compute.ai/skills/workloads/SKILL.md)

## Let your agent help

Paste this into an agent that can read URLs and configure local tools:

```text
Read https://nodus-compute.ai/connect.md and help me connect Nodus to this agent. Reuse any existing Nodus connection. Verify setup by listing my workloads. Do not start paid compute.
```

The agent can prepare configuration and explain the steps. You complete
browser sign-in. Preserve existing settings and use the client's supported
configuration interface.

## Documentation and custom agents

- [llms.txt](https://nodus-compute.ai/llms.txt) indexes the public documentation.
- [llms-full.txt](https://nodus-compute.ai/llms-full.txt) combines every guide
  and reference into one text document.
- [connect.md](https://nodus-compute.ai/connect.md) provides this setup guide
  as Markdown. Every documentation page also links to its Markdown source.
- [OpenAPI](../../openapi/openapi.yaml) defines the customer HTTP contract.
- [Python SDK](../reference/python/client.md) and [CLI](../reference/cli.md)
  support custom agents and automation with execution environments.
- [Agent sandboxes](agent-sandboxes.md) support interactive commands and
  streamed output through the SDK. The seven workload MCP tools do not
  expose sandbox operations.
