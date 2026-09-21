"""Workspace metadata, creation, and explicit connection recovery."""
from __future__ import annotations

import re
import os
from pathlib import Path
import math
from typing import Any, TYPE_CHECKING

from .errors import NodusError, ValidationError

if TYPE_CHECKING:
    from . import AsyncWorkload, Workload


_RESEARCH_WORKSPACE_ID = re.compile(r"[A-Za-z0-9_-]{1,256}", re.ASCII)


def _path(workspace_id: str) -> str:
    if type(workspace_id) is not str or _RESEARCH_WORKSPACE_ID.fullmatch(workspace_id) is None:
        raise ValidationError("Use a workspace ID returned by Nodus")
    return f"/v1/research-workspaces/{workspace_id}"


def _key(value: str) -> str:
    from . import _valid_idempotency_key
    return _valid_idempotency_key(value)


def _positive(value: Any, field: str, minimum: float = 0) -> None:
    if type(value) not in {int, float} or (isinstance(value, float) and not math.isfinite(value)) or value <= 0 or value < minimum:
        raise ValidationError(f"{field} must be a finite positive number")


def _resources(gpu: str | None, gpu_count: int | None, gpu_memory_gb: float | None) -> dict[str, Any]:
    body: dict[str, Any] = {}
    if gpu is not None:
        if type(gpu) is not str or not 1 <= len(gpu) <= 64 or any(c in gpu for c in "\r\n\x00"):
            raise ValidationError("gpu must be an available GPU model name")
        body["gpu"] = gpu
    if gpu_count is not None:
        if type(gpu_count) is not int or gpu_count not in {1, 2, 4, 8}:
            raise ValidationError("gpu_count must be 1, 2, 4 or 8 GPUs on one machine")
        body["gpu_count"] = gpu_count
    if gpu_memory_gb is not None:
        _positive(gpu_memory_gb, "gpu_memory_gb")
        if gpu_memory_gb > 1024:
            raise ValidationError("gpu_memory_gb must not exceed 1024 GB per GPU")
        body["gpu_memory_gb"] = gpu_memory_gb
    return body


def _configuration(name: str, environment: str, editor: str, gpu: str, gpu_count: int,
                   gpu_memory_gb: float, budget_usd: float, max_hours: int, size_gb: float,
                   repository: str | None, ref: str | None, ssh_authorized_key: str | None) -> dict[str, Any]:
    if type(name) is not str or not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", name) or name in {".", ".."}:
        raise ValidationError("Use a workspace name with letters, digits, dots, underscores or dashes")
    if type(environment) is not str or environment != "pytorch-cuda" or type(editor) is not str or editor not in {"vscode", "jupyter", "ssh"}:
        raise ValidationError("Choose pytorch-cuda with vscode, jupyter or ssh")
    if gpu is None or gpu_count is None or gpu_memory_gb is None:
        raise ValidationError("Choose a GPU model, exact count and memory per GPU")
    _positive(budget_usd, "budget_usd", 0.000001)
    _positive(size_gb, "size_gb", 1024 / 1073741824)
    if type(max_hours) is not int or not 1 <= max_hours <= 168:
        raise ValidationError("max_hours must be from 1 to 168 whole hours")
    body = {"name": name, "environment": environment, "editor": editor,
            "budget_usd": budget_usd, "max_hours": max_hours, "size_gb": size_gb,
            **_resources(gpu, gpu_count, gpu_memory_gb)}
    for field, value in (("repository", repository), ("ref", ref), ("ssh_authorized_key", ssh_authorized_key)):
        if value is not None:
            if type(value) is not str:
                raise ValidationError(f"{field} must be a string")
            body[field] = value
    if editor == "ssh" and not ssh_authorized_key:
        raise ValidationError("SSH-only workspaces require an SSH public key")
    return body


def _submission(command: str, budget_usd: float, gpu: str | None, gpu_count: int | None,
                gpu_memory_gb: float | None) -> dict[str, Any]:
    if type(command) is not str or not command.strip() or len(command.encode("utf-8")) > 8192 or "\x00" in command:
        raise ValidationError("command must contain 1 to 8192 UTF-8 bytes without a NUL character")
    _positive(budget_usd, "budget_usd", 0.000001)
    return {"command": command, "budget_usd": budget_usd, **_resources(gpu, gpu_count, gpu_memory_gb)}


def _connection(tool: str) -> dict[str, str]:
    if type(tool) is not str or tool not in {"editor", "notebook", "ssh"}:
        raise ValidationError("tool must be editor, notebook or ssh")
    return {"tool": tool}


def _configuration_update(revision: str, configuration: dict[str, Any]) -> dict[str, Any]:
    if type(revision) is not str or re.fullmatch(r"[0-9a-f]{64}", revision) is None:
        raise ValidationError("Use configuration_revision from the current workspace")
    if not isinstance(configuration, dict) or not configuration:
        raise ValidationError("Send the complete configuration from the current workspace with your changes")
    return {"configuration_revision": revision, "configuration": dict(configuration)}


def _submitted(client: Any, result: Any, headers: dict[str, str], asynchronous: bool) -> Workload | AsyncWorkload:
    from . import AsyncWorkload, Workload, _valid_id, _was_replayed
    if not isinstance(result, dict) or not isinstance(result.get("id"), str) or not result["id"]:
        raise NodusError("Workspace submission returned no workload ID", body=result)
    _valid_id(result["id"])
    run = AsyncWorkload(client) if asynchronous else Workload(client)
    run._absorb(result)
    run.replayed = _was_replayed(headers)
    return run


def _retry_connection_path(workspace_id: str, tool: str) -> str:
    if type(workspace_id) is not str or _RESEARCH_WORKSPACE_ID.fullmatch(workspace_id) is None:
        raise ValidationError("Use a research workspace ID returned by Nodus")
    if type(tool) is not str or tool not in {"editor", "notebook"}:
        raise ValidationError("tool must be editor or notebook")
    return f"/v1/research-workspaces/{workspace_id}/connections/retry"


class Workspaces:
    def __init__(self, client: Any):
        self._client = client

    def create(self, name: str, *, size_gb: float) -> dict[str, Any]:
        """Create a named workspace within the server capacity limit."""
        return self._client._request("POST", "/v1/workspaces", json={"name": name, "size_gb": size_gb})

    def list(self) -> list[dict[str, Any]]:
        """Read owned workspace metadata and current writer identities."""
        return self._client._request("GET", "/v1/workspaces")["workspaces"]

    def retry_connection(self, workspace_id: str, *, tool: str) -> dict[str, Any]:
        """Schedule one explicit retry for a failed research-workspace connection."""
        path = _retry_connection_path(workspace_id, tool)
        return self._client._request("POST", path, json={"tool": tool}, max_retries=0)

    def capabilities(self) -> dict[str, Any]:
        """Read available workspace tools, storage limits and supported GPU counts."""
        return self._client._request("GET", "/v1/research-workspaces/capabilities")

    def storage(self) -> dict[str, Any]:
        """Read the account's shared saved-file allowance, usage and charges."""
        return self._client._request("GET", "/v1/research-workspaces/storage")

    def delete_files(self, workspace_id: str, *, storage_revision: int) -> dict[str, Any]:
        """Delete stopped saved files once, guarded by the observed revision."""
        from ._workspace_files import revision
        return self._client._request("DELETE", _path(workspace_id) + "/files",
                                           json={"storage_revision": revision(storage_revision)}, max_retries=0)

    def export_files(self, workspace_id: str, destination: str | os.PathLike[str], *,
                           storage_revision: int, overwrite: bool = False) -> Path:
        """Stream saved files to a verified local tar archive without extracting it."""
        from ._workspace_files import export_files
        return export_files(self._client, workspace_id, destination,
                                         storage_revision=storage_revision, overwrite=overwrite)

    def upload_files(self, workspace_id: str, directory: str | os.PathLike[str], *,
                           idempotency_key: str, replace_revision: int | None = None) -> dict[str, Any]:
        """Upload a stopped project once and return its pending verification status."""
        from ._workspace_files import upload_files
        return upload_files(self._client, workspace_id, directory,
                                         idempotency_key=idempotency_key, replace_revision=replace_revision)

    def transfer(self, workspace_id: str, transfer_id: str) -> dict[str, Any]:
        """Read uploaded segments and durable verification progress."""
        identifier = _path(transfer_id).rsplit("/", 1)[1]
        return self._client._request("GET", _path(workspace_id) + "/transfers/" + identifier)

    def abort_transfer(self, workspace_id: str, transfer_id: str) -> None:
        """Request abandoning an upload once without changing saved files."""
        identifier = _path(transfer_id).rsplit("/", 1)[1]
        self._client._request("DELETE", _path(workspace_id) + "/transfers/" + identifier, max_retries=0)

    def create_interactive(self, name: str, *, environment: str, editor: str, gpu: str,
                           gpu_count: int, gpu_memory_gb: float, budget_usd: float,
                           max_hours: int, size_gb: float, repository: str | None = None,
                           ref: str | None = None, ssh_authorized_key: str | None = None) -> dict[str, Any]:
        """Save an interactive workspace configuration without starting compute."""
        body = _configuration(name, environment, editor, gpu, gpu_count, gpu_memory_gb,
                              budget_usd, max_hours, size_gb, repository, ref, ssh_authorized_key)
        return self._client._request("POST", "/v1/research-workspaces", json=body, max_retries=0)

    def list_interactive(self) -> list[dict[str, Any]]:
        """Read the latest 100 interactive workspaces and their compute state."""
        return self._client._request("GET", "/v1/research-workspaces")["workspaces"]

    def get(self, workspace_id: str) -> dict[str, Any]:
        """Read an interactive workspace's state, costs and connection readiness."""
        return self._client._request("GET", _path(workspace_id))

    def update(self, workspace_id: str, *, configuration_revision: str,
               configuration: dict[str, Any]) -> dict[str, Any]:
        """Save next-session settings while compute and transfers are stopped."""
        return self._client._request("PATCH", _path(workspace_id),
                                     json=_configuration_update(configuration_revision, configuration), max_retries=0)

    def start(self, workspace_id: str, *, idempotency_key: str) -> dict[str, Any]:
        """Start compute once using a caller-retained retry key."""
        return self._client._request("POST", _path(workspace_id) + "/start", json={},
                                     idempotency_key=_key(idempotency_key), max_retries=0)

    def stop(self, workspace_id: str, *, session_id: str, idempotency_key: str) -> dict[str, Any]:
        """Request saving and stopping the specified session once."""
        _path(session_id)
        return self._client._request("POST", _path(workspace_id) + "/stop", json={"session_id": session_id},
                                     idempotency_key=_key(idempotency_key), max_retries=0)

    def connect(self, workspace_id: str, *, tool: str) -> dict[str, Any]:
        """Request a browser connection URL or SSH configuration once."""
        return self._client._request("POST", _path(workspace_id) + "/connections",
                                     json=_connection(tool), max_retries=0)

    def submit(self, workspace_id: str, *, command: str, budget_usd: float,
               idempotency_key: str, gpu: str | None = None, gpu_count: int | None = None,
               gpu_memory_gb: float | None = None) -> Workload:
        """Submit a project revision once, preserving the key for explicit retries."""
        body = _submission(command, budget_usd, gpu, gpu_count, gpu_memory_gb)
        headers: dict[str, str] = {}
        result = self._client._request("POST", _path(workspace_id) + "/workloads", json=body,
                                      idempotency_key=_key(idempotency_key), headers_out=headers, max_retries=0)
        return _submitted(self._client, result, headers, False)

    def workloads(self, workspace_id: str) -> list[dict[str, Any]]:
        """List up to 100 submitted workloads and their saved source revisions."""
        return self._client._request("GET", _path(workspace_id) + "/workloads")["workloads"]


class AsyncWorkspaces:
    def __init__(self, client: Any):
        self._client = client

    async def create(self, name: str, *, size_gb: float) -> dict[str, Any]:
        """Create a named workspace within the server capacity limit."""
        return await self._client._request("POST", "/v1/workspaces", json={"name": name, "size_gb": size_gb})

    async def list(self) -> list[dict[str, Any]]:
        """Read owned workspace metadata and current writer identities."""
        return (await self._client._request("GET", "/v1/workspaces"))["workspaces"]

    async def retry_connection(self, workspace_id: str, *, tool: str) -> dict[str, Any]:
        """Schedule one explicit retry for a failed research-workspace connection."""
        path = _retry_connection_path(workspace_id, tool)
        return await self._client._request("POST", path, json={"tool": tool}, max_retries=0)

    async def capabilities(self) -> dict[str, Any]:
        """Read available workspace tools, storage limits and supported GPU counts."""
        return await self._client._request("GET", "/v1/research-workspaces/capabilities")

    async def storage(self) -> dict[str, Any]:
        """Read the account's shared saved-file allowance, usage and charges."""
        return await self._client._request("GET", "/v1/research-workspaces/storage")

    async def delete_files(self, workspace_id: str, *, storage_revision: int) -> dict[str, Any]:
        """Delete stopped saved files once, guarded by the observed revision."""
        from ._workspace_files import revision
        return await self._client._request("DELETE", _path(workspace_id) + "/files",
                                           json={"storage_revision": revision(storage_revision)}, max_retries=0)

    async def export_files(self, workspace_id: str, destination: str | os.PathLike[str], *,
                           storage_revision: int, overwrite: bool = False) -> Path:
        """Stream saved files to a verified local tar archive without extracting it."""
        from ._workspace_files import export_files_async
        return await export_files_async(self._client, workspace_id, destination,
                                         storage_revision=storage_revision, overwrite=overwrite)

    async def upload_files(self, workspace_id: str, directory: str | os.PathLike[str], *,
                           idempotency_key: str, replace_revision: int | None = None) -> dict[str, Any]:
        """Upload a stopped project once and return its pending verification status."""
        from ._workspace_files import upload_files_async
        return await upload_files_async(self._client, workspace_id, directory,
                                         idempotency_key=idempotency_key, replace_revision=replace_revision)

    async def transfer(self, workspace_id: str, transfer_id: str) -> dict[str, Any]:
        """Read uploaded segments and durable verification progress."""
        identifier = _path(transfer_id).rsplit("/", 1)[1]
        return await self._client._request("GET", _path(workspace_id) + "/transfers/" + identifier)

    async def abort_transfer(self, workspace_id: str, transfer_id: str) -> None:
        """Request abandoning an upload once without changing saved files."""
        identifier = _path(transfer_id).rsplit("/", 1)[1]
        await self._client._request("DELETE", _path(workspace_id) + "/transfers/" + identifier, max_retries=0)

    async def create_interactive(self, name: str, *, environment: str, editor: str, gpu: str,
                                 gpu_count: int, gpu_memory_gb: float, budget_usd: float,
                                 max_hours: int, size_gb: float, repository: str | None = None,
                                 ref: str | None = None, ssh_authorized_key: str | None = None) -> dict[str, Any]:
        """Save an interactive workspace configuration without starting compute."""
        body = _configuration(name, environment, editor, gpu, gpu_count, gpu_memory_gb,
                              budget_usd, max_hours, size_gb, repository, ref, ssh_authorized_key)
        return await self._client._request("POST", "/v1/research-workspaces", json=body, max_retries=0)

    async def list_interactive(self) -> list[dict[str, Any]]:
        """Read the latest 100 interactive workspaces and their compute state."""
        return (await self._client._request("GET", "/v1/research-workspaces"))["workspaces"]

    async def get(self, workspace_id: str) -> dict[str, Any]:
        """Read an interactive workspace's state, costs and connection readiness."""
        return await self._client._request("GET", _path(workspace_id))

    async def update(self, workspace_id: str, *, configuration_revision: str,
                     configuration: dict[str, Any]) -> dict[str, Any]:
        """Save next-session settings while compute and transfers are stopped."""
        return await self._client._request("PATCH", _path(workspace_id),
                                           json=_configuration_update(configuration_revision, configuration), max_retries=0)

    async def start(self, workspace_id: str, *, idempotency_key: str) -> dict[str, Any]:
        """Start compute once using a caller-retained retry key."""
        return await self._client._request("POST", _path(workspace_id) + "/start", json={},
                                           idempotency_key=_key(idempotency_key), max_retries=0)

    async def stop(self, workspace_id: str, *, session_id: str, idempotency_key: str) -> dict[str, Any]:
        """Request saving and stopping the specified session once."""
        _path(session_id)
        return await self._client._request("POST", _path(workspace_id) + "/stop", json={"session_id": session_id},
                                           idempotency_key=_key(idempotency_key), max_retries=0)

    async def connect(self, workspace_id: str, *, tool: str) -> dict[str, Any]:
        """Request a browser connection URL or SSH configuration once."""
        return await self._client._request("POST", _path(workspace_id) + "/connections",
                                           json=_connection(tool), max_retries=0)

    async def submit(self, workspace_id: str, *, command: str, budget_usd: float,
                     idempotency_key: str, gpu: str | None = None, gpu_count: int | None = None,
                     gpu_memory_gb: float | None = None) -> AsyncWorkload:
        """Submit a project revision once, preserving the key for explicit retries."""
        body = _submission(command, budget_usd, gpu, gpu_count, gpu_memory_gb)
        headers: dict[str, str] = {}
        result = await self._client._request("POST", _path(workspace_id) + "/workloads", json=body,
                                            idempotency_key=_key(idempotency_key), headers_out=headers, max_retries=0)
        return _submitted(self._client, result, headers, True)

    async def workloads(self, workspace_id: str) -> list[dict[str, Any]]:
        """List up to 100 submitted workloads and their saved source revisions."""
        return (await self._client._request("GET", _path(workspace_id) + "/workloads"))["workloads"]
