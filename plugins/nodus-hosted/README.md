# Nodus hosted plugin

Connect Nodus to your coding agent with browser sign-in. The plugin bundles
hosted MCP tools and setup and workload skills. No local Python is required.

Install `nodus-hosted@nodus` from the Nodus repository marketplace. See the
[plugin guide](https://nodus-compute.ai/docs/guides/plugins/) for client commands.
Complete browser authorization in your client, then ask it to list workloads.
Remove or disable an existing local Nodus connection before enabling this one.

Requests require an explicit spending limit. Validation and reading history
start no paid work. Result links expire after ten minutes. Treat them as secrets
and verify the returned SHA-256 after downloading.

Manage access in [Connected agents](https://console.nodus-compute.ai/console/?view=agents).
Public vendor directory listings have their own review process. Installing
from this repository does not require a public directory listing.
