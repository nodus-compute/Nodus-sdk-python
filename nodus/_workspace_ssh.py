"""Binary SSH transport through the authenticated workspace API."""
from __future__ import annotations

import os
import re
import sys
import threading
from urllib.parse import urlsplit, urlunsplit

import websocket

from . import Client, _is_loopback
from .errors import NodusError, ValidationError


_IDENTITY = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", re.ASCII)
_PROTOCOL = "nodus-workspace-ssh-v1"


def _address(base: str, workspace_id: str, session_id: str, generation: int) -> str:
    if any(type(value) is not str or _IDENTITY.fullmatch(value) is None for value in (workspace_id, session_id)):
        raise ValidationError("Use the workspace ID and session ID from fresh SSH connection instructions")
    if type(generation) is not int or generation < 1:
        raise ValidationError("Use a positive session generation from fresh SSH connection instructions")
    parsed = urlsplit(base)
    if any(ord(c) <= 32 for c in base) or not parsed.hostname or parsed.username or parsed.password or parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ValidationError("Workspace SSH requires a configured Nodus API origin")
    if parsed.scheme != "https" and not (parsed.scheme == "http" and _is_loopback(base)):
        raise ValidationError("Workspace SSH requires HTTPS or a local test deployment")
    return urlunsplit(("wss" if parsed.scheme == "https" else "ws", parsed.netloc,
                       f"/v1/research-workspaces/{workspace_id}/ssh-stream", "", ""))


def _relay(connection: websocket.WebSocket, input_fd: int, output_fd: int) -> None:
    stopped = threading.Event()
    failed = threading.Event()

    def upload() -> None:
        try:
            while not stopped.is_set():
                data = os.read(input_fd, 64 << 10)
                if not data:
                    break
                connection.send_binary(data)
        except (OSError, websocket.WebSocketException):
            if not stopped.is_set():
                failed.set()
        finally:
            stopped.set()
            connection.abort()

    worker = threading.Thread(target=upload, name="nodus-ssh-input", daemon=True)
    worker.start()
    try:
        while not stopped.is_set():
            try:
                opcode, data = connection.recv_data()
            except websocket.WebSocketTimeoutException:
                continue
            except (websocket.WebSocketConnectionClosedException, OSError):
                if stopped.is_set():
                    break
                raise NodusError("Workspace SSH connection closed. Request fresh connection instructions.") from None
            if opcode == websocket.ABNF.OPCODE_CLOSE:
                break
            if opcode != websocket.ABNF.OPCODE_BINARY or not isinstance(data, bytes):
                raise NodusError("Workspace SSH returned an invalid binary stream.")
            view = memoryview(data)
            while view:
                written = os.write(output_fd, view)
                if written <= 0:
                    raise NodusError("Workspace SSH output could not be delivered.")
                view = view[written:]
        if failed.is_set():
            raise NodusError("Workspace SSH input could not be forwarded.")
    finally:
        stopped.set()
        connection.abort()
        worker.join(timeout=0.2)


def ssh_proxy(workspace_id: str, *, session_id: str, generation: int, base_url: str | None = None) -> int:
    """Forward raw SSH bytes using the current Nodus sign-in and pinned session."""
    with Client(base_url=base_url) as client:
        address = _address(client.base_url, workspace_id, session_id, generation)
        headers = {"Authorization": client._http.headers["Authorization"],
                   "X-Nodus-Workspace-Session": session_id,
                   "X-Nodus-Workspace-Generation": str(generation)}
        connection = None
        try:
            connection = websocket.create_connection(
                address, header=headers, subprotocols=[_PROTOCOL],
                suppress_origin=True, redirect_limit=0, enable_multithread=True,
                timeout=client._http.timeout.connect,
            )
            connection.settimeout(1)
            input_fd, output_fd = sys.stdin.fileno(), sys.stdout.fileno()
            if os.name == "nt":
                import msvcrt
                msvcrt.setmode(input_fd, os.O_BINARY)
                msvcrt.setmode(output_fd, os.O_BINARY)
            _relay(connection, input_fd, output_fd)
            return 0
        except (websocket.WebSocketException, OSError) as error:
            raise NodusError("Workspace SSH connection failed. Check your sign-in and request fresh connection instructions.") from None
        finally:
            if connection is not None:
                connection.abort()
                connection.shutdown()
