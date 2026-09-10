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
git tag v0.3.4
git push origin v0.3.4
```

Use the actual release version in the tag. The workflow verifies it matches
the package version. Approve the pending `pypi` deployment in GitHub Actions.

Creating and pushing the tag starts publishing automatically. The manual
Run workflow button only retries an existing tag. Entering a new version there
does not create its tag.

If checkout reports `couldn't find remote ref refs/tags/v0.3.4`, create and push
the tag from the tested release commit using the commands above. Do not move an
existing release tag.

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

Run the same isolated verification used by CI:

```bash
python scripts/verify-release.py --version 0.3.4 --report release-verification.json
```

For an unpublished wheel, replace `--version` with `--wheel PATH_TO_WHEEL`.
The command installs the artifact into a temporary environment and runs the
repository's tests against that installed package. It clears Nodus credentials,
uses an isolated home directory and local API fixtures, and never rents compute.
The JSON report records the artifact version, test counts and failure stage.
Run it from the matching release checkout, since newer tests may intentionally
detect defects in older packages.

After publication, a separate verification job installs the package from PyPI.
If that job fails, inspect its report and rerun the failed job after fixing the
cause. Do not rerun a successful upload or replace an existing PyPI version.
