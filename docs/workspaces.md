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

## Interactive GPU workspaces

These helpers are not released yet and require a deployment with interactive
workspaces enabled. Sign in with `nodus login`, then create a project with your
chosen development tool. `vscode` opens browser VS Code, `jupyter` opens
JupyterLab, and `ssh` runs without a browser tool. SSH-only mode requires
`ssh_authorized_key`. Either browser mode can also accept that public key.

```python
import nodus

with nodus.Client() as client:
    capabilities = client.workspaces.capabilities()
    decimal_policy = capabilities.get("storage_policy_version") in {
        "included-10gb-v1", "r2-standard-10gb-account-v1"
    }
    project_size = 10 if decimal_policy else capabilities["storage_limit_bytes"] / 1_073_741_824
    workspace = client.workspaces.create_interactive(
        "kernel-lab",
        environment="pytorch-cuda",
        editor="jupyter",
        gpu="H100",
        gpu_count=1,
        gpu_memory_gb=80,
        budget_usd=8,
        max_hours=2,
        size_gb=project_size,
    )
    session = client.workspaces.start(
        workspace["id"], idempotency_key="kernel-lab-session-1"
    )
    print(workspace["id"], session["state"])
```

Choose storage within `capabilities["storage_limit_bytes"]`. When
`capabilities.get("storage_policy_version")` is `included-10gb-v1` or
`r2-standard-10gb-account-v1`, use `size_gb=10` for the 10 GB project capacity. Other deployments use GiB
and their displayed storage limit. Creating the
configuration does not allocate compute. Start admits a session and can return
before its GPU or connection is ready. Exact counts of 1, 2, 4 or 8 refer to
one machine. Availability depends on the requested configuration.

Use `client.workspaces.get(workspace_id)` to read state, costs and connection
readiness. `client.workspaces.list_interactive()` lists interactive projects.
Once `connections["notebook"]` is true, call
`client.workspaces.connect(workspace_id, tool="notebook")` and open its `url`.
For browser VS Code use `tool="editor"`. SSH uses `tool="ssh"` and returns
connection instructions. The existing `create` and `list` methods retain their
named sandbox file-storage behavior.

Versioned storage policies require explicit deployment support. Under
`r2-standard-10gb-account-v1`, the first **10 decimal GB across your account**
is free. Additional saved-file payload costs **$0.015 per GB-month**, with
requests included. Charges are prorated continuously by saved bytes and time
using a fixed 30-day month. Compute is billed separately. The 10 GB capacity
of each workspace is an operational limit, separate from the shared allowance.
The older `included-10gb-v1` policy retains its existing allowance.

Read `client.workspaces.storage()` for `retained_bytes`, `included_bytes`,
`billable_bytes`, `rate_usd_gb_month`, lifetime `charged_usd`, `status` and
`as_of`. Values come from the server. Storage can continue after compute stops.
If `status` is `funding_required`, additional paid storage needs credits or
spending headroom. Saved files are preserved, and stopped-file export and
deletion remain available. Unfunded retention is not charged later.

## Upload, export and delete saved files

File helpers require version 3 storage. Stop compute and wait for saving and
active transfers to finish. Read `workspace = client.workspaces.get(workspace_id)`
and retain its `storage_revision` before replacing or deleting files.

Use `client.workspaces.upload_files(workspace_id, "./project",`
`idempotency_key="project-upload-1", replace_revision=workspace["storage_revision"])`
to upload a local folder. This prepares a bounded canonical archive on local
disk, uploads segments and queues server verification. It does not start compute.
The folder must fit the workspace limit. Symbolic links and special files are
refused by this uploader. Files must remain unchanged while the archive is built.

Retain the directory contents, key and replacement revision together. After an
uncertain response, repeat that same upload to resume acknowledged segments.
The SDK does not automatically retry mutations. Changed contents require a new
intent after inspecting the existing transfer. The returned state can be
`queued` or `verifying`. Read `client.workspaces.transfer(workspace_id, transfer_id)`
until `committed` confirms replacement. A failed verification leaves prior saved
files intact and reports `failure_code`. Use
`client.workspaces.abort_transfer(workspace_id, transfer_id)` to abandon an upload.

Use `client.workspaces.export_files(workspace_id, "project.tar",`
`storage_revision=workspace["storage_revision"])` to stream an integrity-checked
archive to your computer. The destination must not exist unless you explicitly
pass `overwrite=True`. Failed downloads leave an existing destination intact.
The SDK verifies segment and complete-archive hashes and does not extract files.
Export links expire, so retry the export to refresh them. Exported revisions
remain available for 24 hours even if the current saved project is deleted.
The console streams downloads in Chrome or Edge. Other browsers can use this
SDK helper.

After keeping any export you need, call
`client.workspaces.delete_files(workspace_id, storage_revision=workspace["storage_revision"])`
to delete that saved revision. The workspace configuration stays available.
A changed revision is refused instead of deleting newer files. Deletion cannot
be undone through the workspace. Previously created exports expire separately.
The asynchronous client provides the same methods with `await`, including
archive preparation off the event loop.

## Submit your project as a workload

Save changes in your editor before submitting. A running workspace captures
its current files in `/workspace`. A stopped workspace uses its last successful
saved revision. The original curated environment image and GPU configuration
carry over. Packages installed outside the saved folder are not captured.
Project source must fit `capabilities["workload_source_limit_bytes"]`, which can
be smaller than the workspace's saved-file capacity.

Replace `ws_1234-abcd` with your workspace ID. Retain the command, budget and
retry key together until admission is confirmed:

```python
with nodus.Client() as client:
    try:
        workload = client.workspaces.submit(
            "ws_1234-abcd",
            command="python train.py",
            budget_usd=6,
            idempotency_key="kernel-lab-training-1",
        )
    except nodus.APIError as error:
        if error.code != "workspace_save_pending":
            raise
        print("Project saving is pending. Retry the same command, budget and key.")
    else:
        print(workload.id)
```

The SDK sends every interactive create, start, stop, connection and workload
submission once. It does not automatically retry a failed or pending request.
For `workspace_save_pending`, wait for the server's `Retry-After` interval and
repeat the same submission. After an uncertain reply, also retain the same key
and body. A definite `workspace_capture_failed` or `workspace_source_rejected` requires
correcting the project problem and a new submission key. A different intended run gets a new key.

An admitted workload is independent. Further edits do not change its captured
source, and stopping workspace compute does not cancel it. Both can incur
compute charges at the same time. The workload budget is separate from the
workspace session budget. Optional `gpu`, `gpu_count` and `gpu_memory_gb`
override only the submitted workload.

Use the returned workload's ordinary `wait`, `logs` and `outputs` methods.
Reconnect later with `client.get(workload_id)`.
`client.workspaces.workloads(workspace_id)` lists admitted runs and their
`source_revision`. The asynchronous client mirrors all these helpers with
`await` and returns `AsyncWorkload` on submission.

To stop workspace compute, retain the workspace record's `session["id"]` and a stable
retry key, then call `client.workspaces.stop(workspace_id,
session_id=session_id, idempotency_key=stop_key)`. Poll the workspace until its
state confirms compute stopped. Running programs and GPU memory are not saved.

## SSH from your computer

Use a Nodus CLI version with workspace SSH support and sign in with `nodus login`
on the computer running SSH or VS Code. Create the workspace with your SSH
public key, then copy the connection's `ssh_config` into your OpenSSH
configuration. Keep the matching private key on your computer.

The HTTPS connection instructions use `nodus workspaces ssh-proxy` as an
OpenSSH `ProxyCommand`. The proxy reads the existing Nodus sign-in and forwards
binary SSH traffic to the specified compute session. Credentials are absent
from the configuration and command line. Confirm the workspace host key on
first connection. If compute is replaced, request fresh connection instructions.
Disconnecting SSH leaves compute running. Use Stop to release it.
