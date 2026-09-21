# Devbox preview

Devbox is a named sandbox preset for development sessions. This addition requires
an SDK build that includes `nodus.Devbox` and a deployment that enables the
`devbox` profile. It is not a claim of generally available CPU execution.
An idle box suspends after its configured timeout. Reconnecting by name or
submitting a command wakes the same identity within its original lifetime.
Production qualification, including a 45 minute idle gap and package installation,
is still pending.

Authenticate to your configured preview deployment, then create a session:

```bash
nodus login
nodus devbox up scratch --image python:3.12 --budget 10
nodus devbox ls
```

The command prints a sandbox ID. Use that ID with `nodus sandbox exec` to run
commands, inspect their output, and continue using the same session. For example,
replace `sb_example` with the returned ID:

```bash
nodus sandbox exec sb_example python -m pip install requests
nodus devbox up scratch
nodus devbox rm scratch
```

`up` reattaches to an active devbox with the same name in your account. Omit the
image when reconnecting. Existing configuration and budget stay unchanged unless
you explicitly raise the budget. A name used by another sandbox profile is a
conflict. `rm` terminates the active devbox with that exact name and does not
create a replacement. `ls` walks all pages and includes terminated devboxes.

SDK 0.5.1 also accepts the returned ID with `devbox shell` and `devbox rm`.
Both commands verify that the target has the devbox profile. Sandbox commands
accept active exact names as well as IDs in this release.

```python
import nodus

box = nodus.Devbox(name="scratch", image="python:3.12", budget=10)
try:
    command = box.exec(["python", "-m", "pip", "install", "requests"])
    command.wait()
    if not command.succeeded:
        raise RuntimeError("Package installation failed")
finally:
    box.close()
```

Closing the handle releases the local connection. Call `box.terminate()` or
`nodus devbox rm scratch` to terminate the remote session. Using `with
nodus.Devbox(...)` inherits Sandbox's termination-on-exit behavior.

## Server defaults

The SDK sends `profile: devbox` to the existing sandbox endpoint. It does not
calculate a budget. Omitting `budget` uses the server's 10 USD devbox limit.
The preset supplies 2 vCPUs, 4 GB memory, 20 GB disk, a maximum lifetime of
7 days from creation, idle suspension after 30 minutes, and snapshot continuity
at 5 minute intervals. Explicit resource, budget, lifecycle, continuity, and
policy fields override these defaults. Recovery relies on saved files, not
arbitrary process memory. Only files in the selected checkpoint folder survive
suspension. Installed packages and other files outside that folder must be
recreated after wake. Persistent workspace storage is a separate feature.

The default egress hosts are `github.com`, `api.github.com`,
`objects.githubusercontent.com`, `pypi.org`, `files.pythonhosted.org`,
`registry.npmjs.org`, `registry-1.docker.io`, `ghcr.io`, and `huggingface.co`.
Package clients use the supplied HTTP and HTTPS proxy settings. Redirects to
other hosts require an explicit `policy.egress_allow` override. Repository,
registry, model downloads, and CDN redirects have not all been qualified live.
Set `policy={"network": "deny"}` to disable egress.

## Repository bootstrap preview

Connect the GitHub App to your account and grant it read access to the selected
repository. Use an image that contains the tools your project needs.

```bash
nodus devbox up api \
  --image your-image:qualified \
  --repo your-org/api \
  --ref main \
  --setup "make deps"
```

The command returns the sandbox ID while startup continues. The checkout lives in
`/workspace`. The setup command appears as an ordinary execution whose ID begins
with `ex_bootstrap_`. Inspect its output and completion before running commands
that depend on it. Setup failure leaves the box usable and adds a
`bootstrap_failed` warning to its API response and SDK `warnings` list.

The optional `--dotfiles your-org/dotfiles` clones a second connected repository
into `$HOME/.dotfiles` and runs its `install.sh` before setup. `--ref` accepts a
branch name or `refs/tags/name`. Repository URLs and embedded credentials are
rejected. Bootstrap requires `github.com` in the egress allowlist, which the
devbox preset includes.

```python
box = nodus.Devbox(
    name="api",
    image="your-image:qualified",
    bootstrap={"repo": "your-org/api", "ref": "main", "setup": "make deps"},
)
```

The installation credential is never supplied to customer commands or saved in
the checkout. Later authenticated Git operations require your own connection
method. Repository bootstrap does not turn the root filesystem into persistent
storage. Production checkout and setup qualification is pending.
