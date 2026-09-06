# Public API contract

[openapi.yaml](openapi.yaml) describes the customer workload and webhook HTTP API.
The Python SDK reference is in [the client reference](../docs/reference/python/client.md).

The authoritative contract lives at `design/openapi.yaml` in the Nodus server
repository. This byte-identical public copy lets users, agents, and documentation
renderers consume it without access to server implementation code.

Update the server contract first, then run from this repository:

```bash
python scripts/sync_openapi.py ../nodus
python scripts/sync_openapi.py ../nodus --check
```

Submit coordinated server and SDK pull requests for a contract change. Run the
server contract validator and the SDK tests before merging. Keep this copy and the
server source in sync; do not edit generated API reference pages independently.

This contract describes current behavior. An OpenAPI schema is not evidence that
the deployed server enforces every constraint. Compatibility limitations belong
in the schema descriptions and guides until the implementation changes.
