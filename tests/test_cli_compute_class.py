"""CLI placement selection reaches the submission contract without a default."""
import json

import httpx
import pytest

import nodus
from nodus import cli


@pytest.mark.parametrize('compute_class', ['vm', 'accelerator', None])
def test_cli_compute_class_reaches_wire(compute_class, monkeypatch):
    sent = []
    def handler(req):
        sent.append(json.loads(req.content))
        return httpx.Response(202, json={'workload_id': 'wl_test', 'id': 'wl_test', 'status': 'accepted'})
    client = nodus.Client(api_key='nk_test', base_url='https://nodus.invalid')
    client._http = httpx.Client(base_url='https://nodus.invalid', transport=httpx.MockTransport(handler))
    monkeypatch.setattr(cli, 'Client', lambda **kwargs: client)
    args = ['run']
    if compute_class:
        args += ['--compute-class', compute_class]
    args += ['--', 'python', '-c', 'print(1)']
    assert cli.main(args) == 0
    assert sent[0]['requirements'].get('compute_class') == compute_class
