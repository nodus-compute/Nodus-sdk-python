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
git tag v0.1.2
git push origin v0.1.2
```

Use the actual release version in the tag. The workflow verifies it matches
the package version. Approve the pending `pypi` deployment in GitHub Actions.

## Retry after a workflow fix

An old run keeps its original workflow. To publish an existing tag using the
updated workflow, open Actions, select publish, and click Run workflow.
Select the main branch and enter the existing release tag.

The job checks out that tag and publishes its package using the current
workflow. No tag needs to be moved or recreated. Environment approval still applies.

PyPI versions cannot be overwritten. Confirm a failed upload did not publish
the version before retrying. Keep customer installation docs synchronized
with the version available on PyPI.
