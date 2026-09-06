# Maintaining and publishing these docs

`README.md` is the onboarding entrypoint. `docs/` is the canonical narrative and
parameter reference. `examples/` contains complete runnable programs. Legacy
`wiki/` paths contain links so old bookmarks still resolve without duplicate
instructions.

When an API or SDK behavior changes, update its reference page, affected guide,
example, and contract checks in the same change. Keep examples version-aligned;
mark additions unavailable in older package releases. Never publish a claimed
production hostname, supported integration, or recovery guarantee without
verifying it against the deployment and implementation.

For a hosted documentation site, render these Markdown files from Git rather
than copying them into a second editable wiki. Generate the HTTP reference from
the control plane's release-matched OpenAPI contract. Keep internal architecture
and supplier details out of the public SDK docs. A docs-site deployment and
hostname can be chosen independently of the API endpoint.

Useful release checks: relative links, Python syntax, CLI parsing, first-run
examples against a fake transport, SDK/server payload parity, and OpenAPI route
coverage. Hosted publication should add redirects from replaced pages and an
agent-readable index pointing to the same source. The repository already provides
[an agent entrypoint](../index.md#for-coding-agents).
