import io

import pytest

from rich.console import Console

import nodus
from nodus import _terminal


@pytest.mark.parametrize('encoding', ['cp1252', 'utf-8'])
@pytest.mark.parametrize('width', [44, 80])
def test_progress_can_render_in_a_legacy_windows_encoding(monkeypatch, encoding, width):
    buffer = io.BytesIO()
    output = io.TextIOWrapper(buffer, encoding=encoding, write_through=True)
    console = Console(file=output, width=width, height=30, force_terminal=True, legacy_windows=False)
    monkeypatch.setattr(_terminal, 'console', lambda **kwargs: console)
    progress = _terminal.RunProgress('wl_test', True)
    workload = nodus.Workload(None)
    workload._absorb({'id': 'wl_test', 'status': 'running', 'spend_usd': 0.000312,
                      'stages': [{'id': 'train', 'metric_step': 3, 'metric_total_steps': 10}]})
    progress.workload = workload
    console.print(progress.render())
    text = buffer.getvalue().decode(encoding)
    assert 'Running' in text
    assert '$0.000312' in text
    assert all(len(_terminal.clean(line)) <= width for line in text.splitlines())
