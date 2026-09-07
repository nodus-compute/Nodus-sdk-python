"""Small, dependency-free terminal presentation shared by human-facing commands."""
from __future__ import annotations

import os
import re
import sys
from typing import Any, TextIO

# Strip complete terminal sequences before removing remaining control bytes.
_ANSI = re.compile(r'\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b\[[0-?]*[ -/]*[@-~]')
_CONTROL = re.compile(r'[\x00-\x08\x0b-\x1f\x7f-\x9f\u202a-\u202e\u2066-\u2069]')
_LINE_CONTROL = re.compile(r'[\x00-\x1f\x7f-\x9f\u202a-\u202e\u2066-\u2069]')
_COLORS = {'bold': '1', 'cyan': '36', 'green': '32', 'yellow': '33', 'red': '31'}


def safe(value: Any, *, multiline: bool = False) -> str:
    text = _ANSI.sub('', str(value))
    return (_CONTROL if multiline else _LINE_CONTROL).sub('', text)


def accent(value: str, color: str = 'bold', *, stream: TextIO | None = None) -> str:
    stream = sys.stdout if stream is None else stream
    if 'NO_COLOR' in os.environ or os.environ.get('TERM') == 'dumb' or not stream.isatty():
        return value
    return f'\x1b[{_COLORS[color]}m{value}\x1b[0m'


def status_text(value: Any) -> str:
    text = safe(getattr(value, 'value', value))
    color = {'completed': 'green', 'failed': 'red', 'cancelled': 'yellow'}.get(text, 'cyan')
    return accent(text, color)


def table(headers: tuple[str, ...], rows: list[tuple[str, ...]]) -> str:
    """Render complete IDs and explicit columns without terminal control bytes."""
    clean = [tuple(safe(cell) for cell in row) for row in rows]
    widths = [max([len(header), *(len(row[i]) for row in clean)]) for i, header in enumerate(headers)]
    def line(row: tuple[str, ...]) -> str:
        return '  '.join(cell.ljust(width) for cell, width in zip(row, widths)).rstrip()
    return '\n'.join([accent(line(headers)), line(tuple('-' * width for width in widths)), *(line(row) for row in clean)])


def workload_summary(wl: Any) -> str:
    identifier = safe(wl.id)
    route = safe(wl.route.sku) if wl.route else 'Not reported'
    lines = [
        accent('Nodus / Workload'),
        f'  Workload  {identifier}',
        f'  Status    {status_text(wl.status)}',
        f'  Compute   {route}',
        f'  Cost      ${wl.cost_now_usd:.2f}',
    ]
    status = getattr(wl.status, 'value', wl.status)
    if status == 'failed':
        lines.extend(['', f'  View logs: nodus logs {identifier}', f'  View events: nodus events {identifier}'])
    elif status == 'completed':
        lines.extend(['', f'  View logs: nodus logs {identifier}', f'  Get results: nodus download {identifier}'])
    elif status == 'cancelled':
        lines.extend(['', '  Cancellation recorded. Resource cleanup may still be finishing.'])
    else:
        lines.extend(['', f'  Follow progress: nodus wait {identifier}'])
    return '\n'.join(lines)


def error_message(exc: Exception, *, command: str, workload_id: str = '') -> str:
    from .errors import (
        APIConnectionError, APITimeoutError, AuthenticationError, BudgetExceededError,
        NotFoundError, RateLimitError, SpendCheckUnavailableError,
    )
    identifier = safe(workload_id)
    status = getattr(exc, 'status_code', None)
    if isinstance(exc, BudgetExceededError):
        message = 'This run exceeds your spending limit.'
        if exc.headroom_usd is not None:
            message += f' ${exc.headroom_usd:.2f} remains available this month.'
        message += '\n  Review your limits in the Nodus console before submitting again.'
    elif isinstance(exc, AuthenticationError) and command == "login":
        message = safe(str(exc), multiline=True)
    elif isinstance(exc, AuthenticationError):
        message = 'Your sign-in could not be verified. Run nodus login to sign in again.'
    elif isinstance(exc, NotFoundError):
        message = 'Workload not found in this account. Run nodus list to check the ID.' if identifier else 'The requested resource was not found.'
    elif isinstance(exc, SpendCheckUnavailableError):
        message = 'We could not check your spending limit. Nothing was submitted. Try again shortly.'
    elif command == 'logs' and status is not None and status >= 500:
        message = f'Logs are temporarily unavailable. Try nodus logs {identifier} again shortly.'
    elif isinstance(exc, APITimeoutError) and command == "login":
        message = "Sign-in timed out. Run nodus login to try again."
    elif isinstance(exc, APITimeoutError) and command not in ("run", "submit", "wait"):
        message = "Nodus took too long to respond. Try again shortly."
    elif isinstance(exc, APITimeoutError):
        message = 'The wait timed out. Your workload may still be running.'
        if identifier:
            message += f'\n  Check it with nodus status {identifier}.'
        elif command in ('run', 'submit'):
            message += '\n  Check nodus list before submitting again.'
    elif isinstance(exc, APIConnectionError):
        message = 'Could not reach Nodus. Check your connection and try again.'
        if command in ('run', 'submit'):
            message += '\n  Check nodus list before submitting again.'
    elif isinstance(exc, RateLimitError):
        message = 'Nodus received too many requests. Wait a moment and try again.'
    elif status is not None and status >= 500:
        message = 'Nodus is temporarily unavailable. Try again shortly.'
        if command in ('run', 'submit'):
            message += '\n  Check nodus list before submitting again.'
    elif isinstance(exc, FileNotFoundError):
        message = f'File not found: {safe(exc.filename or str(exc))}.'
        if command in ('run', 'submit'):
            message += '\n  Run nodus init to create a workload file.'
    else:
        body = getattr(exc, 'body', None)
        detail = body.get('message') if isinstance(body, dict) else None
        message = safe(detail) if detail else safe(str(exc), multiline=True)
    request_id = getattr(exc, 'request_id', None)
    if request_id:
        message += f'\n  Support reference: {safe(request_id)}'
    return f'{accent("error:", "red", stream=sys.stderr)} {message}'
