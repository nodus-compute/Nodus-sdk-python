"""Human progress on stderr, independent of workload polling and billing."""
from __future__ import annotations

import asyncio
import json
import sys

import httpx
import threading
import time
from typing import Any

from .errors import NodusError, APITimeoutError
from .types import Event

from ._presentation import safe

_AUTO_LOG_LIMIT = 1024 * 1024


class Progress:
    """Report actual states and log changes, never guessed percentages.

    Automatic mode is enabled only on a terminal. Explicit True also emits
    plain transition lines to redirected stderr. False does no extra API work.
    """

    def __init__(self, workload_id: str, enabled: bool | None = None):
        if enabled is not None and not isinstance(enabled, bool):
            raise TypeError('progress must be True, False or None.')
        self.stream = sys.stderr
        self.tty = bool(getattr(self.stream, 'isatty', lambda: False)())
        self.enabled = self.tty if enabled is None else enabled
        self.workload_id = workload_id
        self.started = time.monotonic()
        self.label = 'Connecting'
        self.after = 0
        self.previous_logs = ''
        self.last_status = None
        self.last_stages: dict[str, tuple] = {}
        self.pending_log = ''
        self.logs_paused = False
        self.terminal = False
        self.notices: set[str] = set()
        self.lock = threading.Lock()
        self.stopped = threading.Event()
        self.thread: threading.Thread | None = None
        self.width = 0

    def __enter__(self):
        if self.enabled:
            self.line(f'Nodus | {safe(self.workload_id)}')
            if self.tty:
                self.thread = threading.Thread(target=self._heartbeat, name='nodus-progress', daemon=True)
                self.thread.start()
        return self

    def __exit__(self, *args):
        self.flush_logs()
        self.stopped.set()
        if self.thread:
            self.thread.join()
        if self.enabled and self.tty:
            with self.lock:
                self.stream.write('\r' + ' ' * self.width + '\r')
                self.stream.flush()

    def _heartbeat(self):
        while not self.stopped.is_set():
            with self.lock:
                value = f'  {self.label} | {int(time.monotonic() - self.started)}s elapsed'
                self.stream.write('\r' + value.ljust(self.width))
                self.width = len(value)
                self.stream.flush()
            if self.stopped.wait(1):
                break

    def line(self, value: str):
        if not self.enabled:
            return
        with self.lock:
            if self.tty:
                self.stream.write('\r' + ' ' * self.width + '\r')
            self.stream.write(safe(value, multiline=True) + '\n')
            self.stream.flush()

    def status(self, workload: Any):
        status = str(workload.status)
        self.label = safe(status).replace('_', ' ').capitalize()
        self.terminal = status in ('completed', 'failed', 'cancelled')
        if status != self.last_status:
            self.line(f'  {self.label} | {int(time.monotonic() - self.started)}s elapsed | ${workload.cost_now_usd:.2f}')
            self.last_status = status
        for stage in getattr(workload, 'stages', []):
            snapshot = (str(stage.status), stage.completed_units, stage.total_units)
            if self.last_stages.get(stage.id) != snapshot:
                self.last_stages[stage.id] = snapshot
                units = ''
                if stage.total_units > 0 and 0 <= stage.completed_units <= stage.total_units:
                    units = f' | {stage.completed_units}/{stage.total_units} units'
                self.line(f'  {safe(stage.id)} | {safe(stage.status)}{units}')
            if str(stage.status) == 'running' and not self.terminal:
                self.label = f'Running {safe(stage.id)}'

    def events(self, batch):
        for event in sorted(batch, key=lambda e: e.seq):
            if event.seq <= self.after:
                continue
            self.after = event.seq
            # Payloads may contain credentials or internal infrastructure data.
            self.line('  ' + event.type.replace('.', ' ').replace('_', ' ').capitalize())

    def flush_logs(self):
        if self.pending_log:
            self.line(self.pending_log)
            self.pending_log = ''

    def pause_logs(self):
        self.flush_logs()
        self.logs_paused = True
        self.previous_logs = ''
        self.line(f'  Automatic log preview limit reached. Read all logs: nodus logs {safe(self.workload_id)}')

    def logs(self, text: str):
        if self.logs_paused or text == self.previous_logs:
            return
        if len(text.encode('utf-8')) > _AUTO_LOG_LIMIT:
            self.pause_logs()
            return
        if text.startswith(self.previous_logs):
            delta = text[len(self.previous_logs):]
        else:
            # A new attempt or a rotated log starts a new snapshot.
            self.flush_logs()
            delta = text
        self.previous_logs = text
        self.pending_log += delta
        if '\n' in self.pending_log:
            complete, self.pending_log = self.pending_log.rsplit('\n', 1)
            self.line(complete)

    def log_error(self, error: NodusError):
        if error.status_code == 404:
            if 'no_logs' not in self.notices:
                self.line('  No logs were found for this run.' if self.terminal else '  No logs yet. Waiting for output.')
                self.notices.add('no_logs')
        elif 'larger than' in str(error) and 'bytes' in str(error):
            self.pause_logs()
        else:
            self.unavailable('Logs')

    def unavailable(self, kind: str):
        if kind not in self.notices:
            self.line(f'  {kind} temporarily unavailable.' + (f' Try again with nodus logs {safe(self.workload_id)}.' if self.terminal and kind == 'Logs' else '' if self.terminal else ' Continuing to watch this run.'))
            self.notices.add(kind)

    def retrying(self):
        self.label = 'Reconnecting'
        self.unavailable('Status updates')

    def _request_budget(self, deadline):
        end = time.monotonic() + 1.0
        if deadline is not None:
            end = min(end, deadline)
        remaining = end - time.monotonic()
        if remaining <= 0:
            raise APITimeoutError('Progress observation deadline reached.')
        # Reserve headroom for pool, connect, write and read phases. These
        # optional requests never use the workload transport's retry loop.
        return end, max(0.0001, remaining / 4)

    def _decode(self, client, response, chunks, path, kind):
        if response.status_code >= 400:
            response._content = b''.join(chunks)
            client._raise('GET', path, response)
        text = b''.join(chunks).decode('utf-8', 'replace')
        if kind == 'logs':
            return text
        try:
            body = json.loads(text)
            rows = body.get('events', []) if isinstance(body, dict) else []
            return [Event.from_dict(row) for row in rows if isinstance(row, dict)]
        except (ValueError, TypeError):
            raise NodusError('Event updates could not be read.') from None

    def _fetch(self, client, kind, deadline):
        end, timeout = self._request_budget(deadline)
        path = f'/v1/workloads/{self.workload_id}/{kind}'
        params = {'after': self.after} if kind == 'events' else None
        chunks, size = [], 0
        try:
            with client._http.stream('GET', path, params=params, timeout=timeout) as response:
                for chunk in response.iter_bytes():
                    if time.monotonic() >= end:
                        raise APITimeoutError('Progress observation deadline reached.')
                    size += len(chunk)
                    if size > _AUTO_LOG_LIMIT:
                        raise NodusError('Progress preview is larger than the automatic limit in bytes.')
                    chunks.append(chunk)
                return self._decode(client, response, chunks, path, kind)
        except httpx.HTTPError as exc:
            raise NodusError('Progress updates are temporarily unavailable.') from exc

    async def _fetch_async(self, client, kind, deadline):
        end, timeout = self._request_budget(deadline)
        path = f'/v1/workloads/{self.workload_id}/{kind}'
        params = {'after': self.after} if kind == 'events' else None
        async def read():
            chunks, size = [], 0
            async with client._http.stream('GET', path, params=params, timeout=timeout) as response:
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > _AUTO_LOG_LIMIT:
                        raise NodusError('Progress preview is larger than the automatic limit in bytes.')
                    chunks.append(chunk)
                return self._decode(client, response, chunks, path, kind)
        try:
            return await asyncio.wait_for(read(), max(0.0001, end - time.monotonic()))
        except (httpx.HTTPError, asyncio.TimeoutError) as exc:
            raise NodusError('Progress updates are temporarily unavailable.') from exc

    def _begin_update(self, workload):
        if not self.enabled:
            return False
        self.status(workload)
        if self.terminal:
            self.flush_logs()
            self.line(f'  Final output: nodus logs {safe(self.workload_id)}')
            return False
        return True

    def update(self, client, workload, *, deadline=None):
        if not self._begin_update(workload):
            return
        try:
            self.events(self._fetch(client, 'events', deadline))
        except NodusError:
            self.unavailable('Events')
        if not self.logs_paused and str(workload.status) in ('running', 'recovering'):
            try:
                self.logs(self._fetch(client, 'logs', deadline))
            except NodusError as exc:
                self.log_error(exc)

    async def update_async(self, client, workload, *, deadline=None):
        if not self._begin_update(workload):
            return
        try:
            self.events(await self._fetch_async(client, 'events', deadline))
        except NodusError:
            self.unavailable('Events')
        if not self.logs_paused and str(workload.status) in ('running', 'recovering'):
            try:
                self.logs(await self._fetch_async(client, 'logs', deadline))
            except NodusError as exc:
                self.log_error(exc)
