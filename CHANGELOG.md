# Changelog

## Unreleased

- `nodus login`: approve a short code in the browser once and the SDK writes
  `~/.nodus/config.toml` itself — no key to copy and no address to look up.
  `--no-browser` prints the address instead of opening it; `nodus logout`
  deletes the stored key and says that revoking it is a separate act in the
  console.
- `Client()` and `AsyncClient()` read that file when nothing else supplies a
  setting. Precedence is resolved per setting, highest first: explicit
  argument, environment, then the file — so a stale login cannot outrank what
  CI injected.
- `ConfigurationError` now offers `nodus login` alongside the two exports.

## 0.1.1 — 2026-09-02

Documentation and packaging; no change to what the client sends or raises.

- Every example in the README is executed before it is written down, and
  `tests/test_readme_examples.py` parses every `nodus ...` line in the README
  with the real CLI parser. The two lines the CLI rejected are corrected:
  `nodus status` → `nodus get`, and `nodus run --command "..."` →
  `nodus run --budget 20 -- python train.py`.
- README: `event.message` (no such attribute) → `event.type` / `event.payload`;
  "only `command` is required" corrected — nothing is, the default image fills
  in; the log-lags-by-a-checkpoint caveat now sits next to `logs()`; the error
  table lists every exception class and whether it clears on its own; the
  LICENSE link is absolute so it resolves on the PyPI page.
- The suite passes against an installed wheel, and CI now installs the built
  wheel into a fresh venv and runs the tests against it from outside the source
  tree — the artifact customers download is the artifact tested.
- The paid end-to-end journey test cancels its workload in a `finally`, so a
  failed assertion cannot leave a live run billing.
- Supply chain: the PyPI publish action is pinned to a release commit SHA and
  the CI jobs run with `contents: read`.

## 0.1.0 — 2026-09-02

First public release.
