"""Every ``nodus ...`` line the README shows must parse with the real CLI.

Lines are split at the first bare ``--`` the same way ``cli.main`` splits them:
everything after it belongs to the customer's program.
"""

from __future__ import annotations

import pathlib
import shlex

import pytest

from nodus import cli

README = pathlib.Path(__file__).parents[1] / "README.md"


def _nodus_lines(text: str) -> list[str]:
    """Command lines starting ``nodus `` inside fenced code blocks."""
    lines: list[str] = []
    in_fence = False
    for raw in text.splitlines():
        stripped = raw.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            continue
        if not in_fence:
            continue
        if stripped.startswith("$ "):
            stripped = stripped[2:].strip()
        if stripped == "nodus" or stripped.startswith("nodus "):
            lines.append(stripped)
    return lines


def test_every_readme_nodus_line_parses():
    if not README.exists():
        pytest.skip("no README beside the tests; run from a source checkout")
    lines = _nodus_lines(README.read_text(encoding="utf-8"))
    assert lines, "no `nodus ...` lines found in README.md; extractor or README broken"

    failures: list[str] = []
    for command in lines:
        argv = shlex.split(command)[1:]
        if "--" in argv:
            argv = argv[: argv.index("--")]
        try:
            cli.build_parser().parse_args(argv)
        except SystemExit as exc:
            # argparse exits 0 for --version/--help and non-zero for a usage
            # error; only the latter is a lie in the README.
            if exc.code:
                failures.append(command)
    assert not failures, "README shows nodus lines the CLI rejects:\n  " + "\n  ".join(failures)
