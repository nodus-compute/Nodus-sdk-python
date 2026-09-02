"""Suite-wide wiring.

The suite also runs against an installed wheel, which ships no `pyproject.toml`;
tests that need the source tree are skipped there, not failed.
"""

from __future__ import annotations

import pathlib

import pytest

import nodus
from nodus import config as _config


@pytest.fixture(autouse=True)
def nodus_config(tmp_path, monkeypatch):
    """The config file every test reads and writes, never the operator's own.

    Autouse because the hazard is the tests that do not mention it: whether
    ``nodus.Client()`` raises now depends on a file in ``$HOME``, and a suite
    that reads one passes or fails by whose machine it ran on.
    """
    path = tmp_path / "home" / ".nodus" / "config.toml"
    path.parent.mkdir(parents=True)
    monkeypatch.setattr(_config, "config_path", lambda: path)
    return path


# Tests that read files which exist only in a source checkout, by name.
_SOURCE_TREE_ONLY = {
    "test_the_version_is_read_from_the_package_metadata_not_repeated",
}


def pytest_collection_modifyitems(config, items):
    if (pathlib.Path(nodus.__file__).parents[1] / "pyproject.toml").exists():
        return
    skip = pytest.mark.skip(
        reason="needs the source tree: pyproject.toml is not shipped in the wheel"
    )
    for item in items:
        if item.name in _SOURCE_TREE_ONLY:
            item.add_marker(skip)
