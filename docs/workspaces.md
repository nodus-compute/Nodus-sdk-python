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

A workspace can select another dedicated top-level mount. System directories cannot be used. Workspace contents and application recovery state are separate.

Periodic saves preserve the latest useful archive. An empty folder does not replace an earlier useful archive. Credit exhaustion and lifetime cutoffs preserve the last successful save. They cannot guarantee files written after that save. Files must fit the configured workspace capacity.

Storage billing is disabled unless the deployment has a configured price. The metadata reports `disabled_no_approved_storage_rate` or `metered_subject_to_account_limits`. Payment requirements and available credits still apply. The asynchronous client exposes the same workspace methods.
