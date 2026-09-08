import importlib.util
from pathlib import Path


def test_release_verifier_cannot_inherit_live_test_credentials(monkeypatch, tmp_path):
    script = Path(__file__).resolve().parents[1] / 'scripts/verify-release.py'
    spec = importlib.util.spec_from_file_location('release_verifier', script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setenv('NODUS_E2E_BASE_URL', 'https://do-not-contact.invalid')
    monkeypatch.setenv('NODUS_E2E_API_KEY', 'sentinel-not-a-secret')
    monkeypatch.setenv('NODUS_API_KEY', 'sentinel-not-a-secret')
    monkeypatch.setenv('PYTHONPATH', '/not-the-release')
    env = module.isolated_environment(tmp_path)
    assert not any(key.upper().startswith('NODUS_') for key in env)
    assert 'PYTHONPATH' not in env
    assert env['HOME'] == str(tmp_path)
    assert env['USERPROFILE'] == str(tmp_path)
