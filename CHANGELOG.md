# Changelog

## 0.3.4

- Show the correct next command after creating a custom workload file.
- Distinguish empty filtered workload lists from an empty account history.
- Display reported GPU metadata instead of internal catalog placeholders.

## 0.3.3

- Validate resource quantities and stage progress counts before submission.
- Accept hyphenated and underscored GPU aliases and document every GPU choice.
- Explain GPU and optimization compatibility and container execution clearly.
- Explain required payment methods before the first workload.
- Preserve unavailable route cost and runtime estimates as `None`.
- Accept typed disk and CPU requirements and inherited stage preferences in workload files.
- Preserve the CLI recovery key when a submission response is lost or uncertain.
- Reject invalid budgets and numeric CLI flags with actionable errors.
- Show small nonzero costs without rounding them to a free run.
- Use a compatible progress spinner in legacy Windows terminal encodings.
- Validate deeply nested stage dependencies without exhausting the Python stack.
- Clarify backend compatibility, CLI ranges, and submission recovery in the docs.
- Verify installed release artifacts with one isolated command and after PyPI publication.

## 0.3.2

- Make README links usable from PyPI and align CLI documentation with SDK 0.3.x.
- Explain optional budgets, account limits, and pending cost accounting accurately.
- Clarify log pagination, status refresh, cancellation, and output download behavior.
- Remove an asset lookup entry that the hosted API does not serve.

## 0.3.1

- Show finished stage status when training metrics are unavailable.
- Label missing compute details without implying that a finished run is pending.
- Explain unavailable sign-in verification without referring to a missing run.

## 0.3.0

- Add optional optimization preferences and a strict GPU model requirement.
- Remove customer runtime estimates. Existing callers should remove that argument.
- Add Rich terminal tables, status summaries, live logs, and reported training progress.
- Reuse valid logins, show email, and add `login --force` for a fresh sign-in.
- Replace noisy budget warnings and raw CLI failures with concise guidance.
- Add `wait(progress=...)` and cursor-based `live_logs()` for sync and async clients.
- Support hard workload and account caps with the compatible backend release.


## 0.2.1

- Update the default hosted API endpoint.
- Move saved settings and explicit URLs for the retired hosted endpoint to the
  current endpoint. Custom deployment URLs keep their existing behavior.

## 0.2.0

- Simplify the CLI around workload files and short commands. Replace `get` with
  `status`, use `wait` to observe, and separate blocking `run` from `submit`.
- Add `init`, `download`, `upload`, and `assets`. Remove inline submission flags.
- Share validated TOML configuration between the CLI and sync or async Python.
- Add code and dataset uploads, GitHub and Hugging Face dataset imports, and URL imports.
- Add source assets, named inputs, and simple output declarations to Python submissions.
- Download all declared outputs with integrity checks and safe stage directories.
- Add personal and team workload listing with member attribution.
- Organize documentation from first workload to data, results, and advanced pipelines.


## 0.1.3

- Print workload statuses as readable values such as `completed`.
- Execute documentation examples and terminal commands in package CI.

- Connect to hosted Nodus with `nodus login` without an API URL.
- Show terminal activity while browser approval is pending.
- Keep custom endpoint overrides and saved configuration precedence.
- Lead the quickstart and workload guides with Python examples.


## 0.1.2

- Focus customer onboarding and examples on GPU workloads and clear installation steps.
- Cancel the current workload on Ctrl+C during synchronous waits and attached CLI
  observation. Show terminal activity and distinguish absent logs from missing work.

- Login-first README, canonical task guides and parameter references, plus
  executable documentation checks. Existing wiki paths link to the new guides.
- Sync and async `outputs()`, `download_output()`, and `routing()` methods.
  Downloads stream to a temporary file and verify SHA-256 and declared length
  before replacing the destination. Response-provided URLs are never followed.
- Optional typed request dictionaries and stage loss, rate, and step metrics.
- `nodus run --compute-class vm|accelerator` selects the compute category.
  Existing calls and defaults are unchanged.
- Public OpenAPI contract copy and a cross-repository synchronization command.

- `nodus login`: approve a short code in the browser once and the SDK writes
  `~/.nodus/config.toml` itself, no key to copy. Supply your deployment API address with `--base-url`.
  `--no-browser` prints the address instead of opening it. `nodus logout`
  removes the stored key, names the `key_id` to revoke, and says that revoking
  it is a separate act in the console. **Verified against a local test double.
  Not yet run against a deployed control plane.**
- The config file is proven writable *before* the exchange starts, because the
  console mints the key inside the call that releases it, a write that failed
  afterwards would leave a live key nobody had a copy of. If a write fails
  anyway, the key is printed once so it can be stored or revoked.
- A key the client could never send, one carrying a control character, a
  space, or non-ASCII, is refused at login rather than stored, and
  `save_credentials` refuses it for any caller: a stored key that cannot
  travel in a request header fails every later command, after only ever
  having been shown redacted. Control characters (C0 and C1 alike) are
  refused in every stored value. Non-ASCII text such as a tenant name is not.
  When that refusal meets a value already in the file, a foreign section's
  included, it names the file and the way out, not just the offending key.
- A wire string that arrives as the wrong JSON type reads as absent instead
  of crashing a listing: `nodus artifacts` no longer raises on a digest sent
  as a number, and `nodus explain` no longer raises on a non-numeric device
  memory. The sign-in page address is never opened if it carries any control
  character, the C1 range included.
- Both commands say so on stderr when `NODUS_API_KEY` (or `NODUS_BASE_URL`) is
  set: it outranks the file, so "signed in" and "logged out" would otherwise
  both be wrong.
- `Client()` and `AsyncClient()` read that file when nothing else supplies a
  setting. Precedence is resolved per setting, highest first: explicit
  argument, environment, then the file, so a stale login cannot outrank what
  CI injected.
- `ConfigurationError` now offers `nodus login` alongside the two exports.

## 0.1.1, 2026-09-02

Documentation and packaging. No change to what the client sends or raises.

- Every example in the README is executed before it is written down, and
  `tests/test_readme_examples.py` parses every `nodus ...` line in the README
  with the real CLI parser. The two lines the CLI rejected are corrected:
  `nodus status` → `nodus get`, and `nodus run --command "..."` →
  `nodus run --budget 20 -- python train.py`.
- README: `event.message` (no such attribute) → `event.type` / `event.payload`.
  "only `command` is required" corrected, nothing is, the default image fills
  in. The log-lags-by-a-checkpoint caveat now sits next to `logs()`. The error
  table lists every exception class and whether it clears on its own. The
  LICENSE link is absolute so it resolves on the PyPI page.
- The suite passes against an installed wheel, and CI now installs the built
  wheel into a fresh venv and runs the tests against it from outside the source
  tree, the artifact customers download is the artifact tested.
- The paid end-to-end journey test cancels its workload in a `finally`, so a
  failed assertion cannot leave a live run billing.
- Supply chain: the PyPI publish action is pinned to a release commit SHA and
  the CI jobs run with `contents: read`.

## 0.1.0, 2026-09-02

First public release.
