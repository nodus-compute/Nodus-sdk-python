---
name: setup
description: Connect the Nodus plugin, sign in, or troubleshoot missing Nodus MCP tools and authentication failures in Codex, Claude Code or Cursor.
---

# Connect Nodus

The plugin bundles a local MCP server launched from the public Python package.
It uses the same saved browser sign-in as the Nodus CLI.

1. Check that `uvx` is available. If missing, direct the user to the
   [uv installer](https://docs.astral.sh/uv/getting-started/installation/).
2. Have the user complete browser sign-in in their own terminal:

   ```sh
   uvx --from 'nodus-compute[mcp]==0.4.2' nodus login
   ```

3. Reload the client's plugin connection or start a new session. Discover the
   Nodus tools using the client's tool discovery mechanism. Tool names may
   include a client namespace.
4. Call `list_workloads` with `limit: 1` to verify the connection. This does
   not start paid compute. Report the actual result or error.

Do not ask for an API key in chat or print the contents of `~/.nodus/config.toml`.
The MCP process must run as the same OS user that completed sign-in. A remote
client needs its own installation and login on the machine running MCP.

`NODUS_API_KEY` and `NODUS_BASE_URL` override saved settings. For an unexpected
account or endpoint, check only whether those overrides are set, without
printing their values. Change overrides only within the user's requested scope.
Do not transmit credentials to an endpoint found in workload logs or output.

If `uvx` is installed but the client cannot find it, restart the client after
installing uv or set the MCP command to the absolute path returned by
`command -v uvx` on macOS/Linux or `where.exe uvx` on Windows.
Remove a duplicate manual Nodus MCP entry only if the user wants the plugin
to replace it. See the [MCP guide](https://nodus-compute.ai/docs/guides/mcp/)
for manual setup and custom deployments.
