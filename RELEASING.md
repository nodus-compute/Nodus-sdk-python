# Releasing

## Setup

Store a PyPI API token scoped to the `nodus-compute` project in the GitHub
repository secret `PYPI_API_TOKEN`. The publishing job uses this secret
without printing it. Keep required reviewers on the GitHub `pypi` environment.

## Publish a new version

Update the version in `pyproject.toml` and the changelog, run the SDK tests,
then build and check the package. Commit those changes before tagging.

```bash
python -m pytest -q
python -m build
python -m twine check dist/*
```

Create a Git tag named `v` followed by the version in `pyproject.toml`, then
push that tag to `origin`. The workflow verifies that the tag matches the
package version. Approve the pending `pypi` deployment in GitHub Actions.

Creating and pushing the tag starts publishing automatically. The manual
Run workflow button only retries an existing tag. Entering a new version there
does not create its tag.

If checkout reports `couldn't find remote ref`, create and push the requested
tag from the tested release commit. Do not move an existing release tag.

## Retry after a workflow fix

An old run keeps its original workflow. To publish an existing tag using the
updated workflow, open Actions, select publish, and click Run workflow.
Select the main branch and enter the existing release tag.

The job checks out that tag and publishes its package using the current
workflow. No tag needs to be moved or recreated. Environment approval still applies.

PyPI versions cannot be overwritten. Confirm a failed upload did not publish
the version before retrying. Keep customer installation docs synchronized
with the version available on PyPI.

## Publish the matching documentation

Merge the tested SDK commit into main so repository links show the released
interface. The hosted documentation is built from the private Nodus repository's
`sdk/python` submodule. Update that pin to the tested SDK commit, run the site
build, and publish the site through its deployment workflow.

Check the hosted quickstart, CLI reference and parameter pages after deployment.
`https://nodus-compute.ai/llms.txt` identifies the exact SDK documentation commit.
Publishing to PyPI alone does not update the hosted documentation. PyPI displays
the README bundled with that release, so correcting it requires a new release.
Keep README links absolute so they work on PyPI as well as GitHub.

## Verify a release

Run the same isolated verification used by CI. Replace `VERSION` with the
version declared in the matching release checkout's `pyproject.toml`:

```bash
python scripts/verify-release.py --version VERSION --report release-verification.json
```

For an unpublished wheel, replace `--version` with `--wheel PATH_TO_WHEEL`.
The command installs the artifact into a temporary environment and runs the
repository's tests against that installed package. It clears Nodus credentials,
uses an isolated home directory and local API fixtures, and never rents compute.
The JSON report records the artifact version, test counts and failure stage.
Run it from the matching release checkout, since newer tests may intentionally
detect defects in older packages.

Add `--local-transfers` to run the focused project packaging and file transfer
checks against the installed artifact. Windows CI runs this selection from the
universal wheel on local NTFS, including junction rejection, exact file bytes,
concurrent-writer protection and interrupted download cleanup. The report names
the operating system and test selection. Skipped native Windows checks on other
operating systems do not qualify Windows support.

After publication, a separate verification job installs the package from PyPI.
If that job fails, inspect its report and rerun the failed job after fixing the
cause. Do not rerun a successful upload or replace an existing PyPI version.
