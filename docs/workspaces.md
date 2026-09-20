# Named workspaces

Create a workspace and attach it to a sandbox to preserve selected files between sandbox identities.

```python
with nodus.Client() as client:
    client.workspaces.create("research", size_gb=0.1)
    box = client.sandboxes.create(
        image="python:3.12",
        workspace={"name": "research", "mount": "/workspace"},
    )
    command = box.exec(["sh", "-c", "echo ready > /workspace/progress.txt"])
    command.wait()
    box.terminate()
```

One live sandbox can write a workspace. A conflict includes its current holder ID. Termination can return while the final save is pending. Observe the sandbox until it becomes terminated before attaching the same workspace to another sandbox. List workspace metadata with `client.workspaces.list()` and check `last_error` and `saved_at`.

Devboxes automatically attach a workspace with their name. A custom workspace can select another dedicated top-level mount. System directories cannot be used. Workspace contents and application recovery state are separate.

Periodic saves preserve the latest useful archive. An empty folder does not replace an earlier useful archive. Hard spending and lifetime cutoffs preserve the last successful save. They cannot guarantee files written after that save. Files must fit the configured workspace capacity.

Storage billing is disabled unless the deployment has a configured price. The metadata reports `disabled_no_approved_storage_rate` or `metered_subject_to_account_limits`. Account and workload spending limits still apply. The asynchronous client exposes the same workspace methods.

## Retry a research workspace connection

This helper is not released yet and requires deployment support for research workspace connection retries. Use the research workspace ID returned by its lifecycle, such as `ws_1234-abcd`, rather than a display name. Existing named workspace create and list methods are distinct from research lifecycle methods.

Retry a failed editor connection explicitly:

```python
result = client.workspaces.retry_connection("ws_1234-abcd", tool="editor")
assert result == {"status": "retry_scheduled"}
```

The asynchronous client mirrors the same call:

```python
import asyncio
import nodus

async def retry_notebook():
    async with nodus.AsyncClient() as client:
        result = await client.workspaces.retry_connection("ws_1234-abcd", tool="notebook")
        assert result == {"status": "retry_scheduled"}

asyncio.run(retry_notebook())
```

The tool must be exactly `editor` or `notebook`. This request does not start or stop compute and does not issue a browser grant. The SDK sends it once without an automatic retry. After a transport failure or unavailable response, observe the workspace before asking for another retry.
