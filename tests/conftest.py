"""Suite-wide wiring.

The suite has two homes: a source checkout, and an installed wheel with the
tests run from outside the tree (what CI's build job does, and what a customer
reproducing a bug against `pip install nodus_compute` does). A wheel does not
ship `pyproject.toml`, so checks that compare the source tree against package
metadata can only run from a checkout; they are skipped, not failed, when the
tree is absent.
"""

from __future__ import annotations

import pathlib

import pytest

import nodus

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
