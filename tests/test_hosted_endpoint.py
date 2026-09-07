import pytest

import nodus


@pytest.mark.parametrize("source", ["explicit", "environment", "saved"])
def test_retired_endpoint_moves_without_losing_credentials(monkeypatch, source):
    retired = "https://nodus-api-74it.onrender.com"
    monkeypatch.delenv("NODUS_BASE_URL", raising=False)
    monkeypatch.delenv("NODUS_API_KEY", raising=False)
    monkeypatch.setattr(nodus, "read_credentials", lambda: ("nk_test_key", retired if source == "saved" else ""))
    if source == "environment":
        monkeypatch.setenv("NODUS_BASE_URL", retired)
    explicit = retired if source == "explicit" else None
    key, url = nodus._resolve(None, explicit)
    assert key == "nk_test_key"
    assert url == nodus.DEFAULT_BASE_URL
    assert nodus._resolve_base_url(explicit) == nodus.DEFAULT_BASE_URL


def test_custom_endpoint_is_preserved():
    assert nodus._current_hosted_url("https://api.example.com") == "https://api.example.com"
