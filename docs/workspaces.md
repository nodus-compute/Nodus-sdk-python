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

`list()` collects every page from servers that support pagination. Use
`client.workspaces.iter(limit=100)` to process one page at a time, or
`rows, cursor = client.workspaces.list_page(limit=100)` to control pagination.
Pass the returned cursor to the next `list_page` call until it is empty.
The asynchronous client provides the same methods, including `async for`
over `client.workspaces.iter()`. Cursors are positions in the current listing,
so concurrent changes do not provide a fixed snapshot. Older servers that
return no cursor still provide their original single page.

Workspace responses are dictionaries. A server may include `expired_at`, a
nullable RFC3339 timestamp for expired saved contents, and `cleanup_pending`,
a boolean indicating that cleanup remains pending. Expiration keeps the
workspace name available. Older responses may omit both fields. Read them
with `workspace.get("expired_at")` and
`workspace.get("cleanup_pending", False)`. Timestamp strings are returned
unchanged. These fields do not specify when a future expiration will occur.

A workspace can select another dedicated top-level mount. System directories cannot be used. Workspace contents and application recovery state are separate.

Periodic saves preserve the latest useful archive. An empty folder does not replace an earlier useful archive. Hard spending and lifetime cutoffs preserve the last successful save. They cannot guarantee files written after that save. Files must fit the configured workspace capacity.

Storage billing is disabled unless the deployment has a configured price. The metadata reports `disabled_no_approved_storage_rate` or `metered_subject_to_account_limits`. Account and workload spending limits still apply. The asynchronous client exposes the same workspace methods.

## Saved project files

For a stopped interactive GPU workspace created in the console, use its returned
workspace ID to upload a project directory without starting compute:

```python
with nodus.Client() as client:
    transfer = client.workspaces.upload_files(
        workspace_id,
        "./project",
        idempotency_key="project-upload-1",
    )
    print(transfer["state"])
    print(client.workspaces.storage())
```

An upload can return `queued` or `verifying` while the server checks the files.
Those states do not mean that saved files have been published. Observe the
workspace in the console. After an interrupted upload, retry the unchanged
directory with the same key and `replace_revision`, if supplied. Replacing saved
files requires their observed revision. Symbolic links and special files are
refused, and archive preparation uses temporary disk space proportional to the
project size.

Use the saved revision reported by the workspace when downloading or deleting:

```python
with nodus.Client() as client:
    client.workspaces.export_files(
        workspace_id,
        "./saved-project.tar",
        storage_revision=saved_revision,
    )
    client.workspaces.delete_files(workspace_id, storage_revision=saved_revision)
```

Downloads verify every segment and the complete archive before publishing the
destination. They do not extract files or forward account credentials to the
download service. Existing destination files are preserved unless
`overwrite=True` is supplied. Deletion is sent once and remains guarded by the
observed revision. The asynchronous client supports the same four methods.
