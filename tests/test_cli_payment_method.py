import httpx
import pytest

import nodus
from nodus import cli


@pytest.mark.parametrize("command", ["submit", "run"])
def test_required_card_directs_user_to_billing(command, monkeypatch, tmp_path, capsys):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(402, json={
            "error": "payment_method_required",
            "message": "untrusted https://evil.invalid/collect-card",
        })

    client = nodus.Client(api_key="nk_test", base_url="https://nodus.invalid")
    client._http = httpx.Client(
        base_url="https://nodus.invalid", transport=httpx.MockTransport(handler)
    )
    monkeypatch.setattr(cli, "Client", lambda **kwargs: client)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "nodus.toml").write_text(
        'image = "training:v1"\ncommand = ["python", "train.py"]\nbudget = 5\n'
    )
    assert cli.main([command]) == 2
    assert len(requests) == 1
    output = capsys.readouterr().err
    assert "Add a payment method" in output
    assert "https://console.nodus-compute.ai/?view=billing" in output
    assert "starter credits" in output
    assert "spending limit" not in output
    assert "evil.invalid" not in output
