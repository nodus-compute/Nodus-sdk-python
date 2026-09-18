"""Interactive terminal transport for a sandbox execution."""
from __future__ import annotations

import os
import select
import signal
import sys
import time
from typing import Any

from .errors import NodusError


def shell(box: Any, command: list[str] | None = None) -> int:
    """Run a terminal and restore local terminal settings on every exit path."""
    if os.name != "posix" or not sys.stdin.isatty() or not sys.stdout.isatty():
        raise ValueError("devbox shell requires an interactive POSIX terminal")
    import termios
    import tty

    input_fd, output_fd = sys.stdin.fileno(), sys.stdout.fileno()
    attributes = termios.tcgetattr(input_fd)
    size = os.get_terminal_size(output_fd)
    execution = box.exec(command or ["/bin/bash", "-l"], stdin=True, tty=True,
                         rows=size.lines, cols=size.columns, env={"TERM": os.environ.get("TERM", "xterm-256color")})
    resized = True
    stopped = False
    previous = {}

    def resize(_signum, _frame):
        nonlocal resized
        resized = True

    def stop(_signum, _frame):
        nonlocal stopped
        stopped = True

    done = False
    cursor = 0
    try:
        for number, handler in ((signal.SIGWINCH, resize), (signal.SIGHUP, stop), (signal.SIGTERM, stop)):
            previous[number] = signal.signal(number, handler)
        tty.setraw(input_fd)
        while not stopped:
            if resized:
                size = os.get_terminal_size(output_fd)
                execution.resize(size.lines, size.columns)
                resized = False
            readable, _, _ = select.select([input_fd], [], [], 0.05)
            if readable:
                data = os.read(input_fd, 4096)
                if not data or b"\x1d" in data:
                    break
                execution.write(data)
            previous_cursor = cursor
            page = execution.output(after=cursor, wait=False)
            for frame in page.frames:
                if frame.sequence > cursor:
                    view = memoryview(frame.data)
                    while view:
                        count = os.write(output_fd, view)
                        if count <= 0:
                            raise OSError("terminal output closed")
                        view = view[count:]
                    cursor = frame.sequence
            cursor = max(cursor, page.next_sequence)
            if page.done:
                done = True
                if page.complete:
                    execution.refresh()
                    return execution.exit_code if execution.exit_code is not None else 1
                if cursor <= previous_cursor:
                    raise NodusError(f"Sandbox execution final output is unavailable after sequence {cursor}. Retry output(after={cursor}).")
        return 130
    finally:
        try:
            termios.tcsetattr(input_fd, termios.TCSADRAIN, attributes)
        finally:
            for number, handler in previous.items():
                signal.signal(number, handler)
            if not done:
                execution.cancel()
