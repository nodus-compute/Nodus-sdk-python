# Releasing

## One-time setup on PyPI

1. **Create the project owner account.** Register on PyPI under a company
   address, not a personal one — the account that first uploads owns the name,
   and moving it later is a support ticket.
2. **Turn on two-factor auth.** PyPI requires it for anyone who uploads.
3. **Add a Trusted Publisher** so no API token ever exists to leak. On PyPI:
   *Your projects → Publishing → Add a new pending publisher*
   - PyPI project name: `nodus_compute`
   - Owner: `nodus_compute`
   - Repository: `Nodus-sdk-python`
   - Workflow: `publish.yml`
   - Environment: `pypi`
4. **Create the `pypi` environment** in the GitHub repo
   (*Settings → Environments*). Adding required reviewers there makes a release
   a deliberate act rather than a tag anyone can push.

No API token is stored anywhere. PyPI verifies the workflow's identity directly.

## Cutting a release

```bash
# 1. version and changelog in the same commit
#    pyproject.toml -> version = "0.2.0"
#    CHANGELOG.md   -> a 0.2.0 section
git commit -am "Release 0.2.0"

# 2. the tag is the trigger
git tag v0.2.0
git push origin main --tags
```

The `publish` workflow builds, runs `twine check`, and uploads.

**A version is permanent.** PyPI will not let the same version be uploaded
twice, and a deleted release does not free its number. Test against TestPyPI
first if a release is at all uncertain.

## Before tagging

- `python -m pytest -q` passes
- `python -m build && python -m twine check dist/*` passes
- the README renders — `twine check` catches what PyPI would reject
- the version in `pyproject.toml` matches the tag
