# Install and sign in

Install with Python 3.10 or newer:

```bash
pip install nodus-compute
nodus login
```

Your browser opens the Nodus sign-in page. Sign in, check that the device code
matches your terminal, and approve. Close the tab once approved. Your terminal
updates automatically and saves the login for both the CLI and Python SDK.
There is no API URL or key to copy. Members and admins can sign in. Your CLI
session follows your current team permissions and does not create a team API key.

Existing users can upgrade with `pip install --upgrade nodus-compute`.
Personal CLI sessions require SDK 0.3.0 or newer and the matching server update.

## Use your login in Python

```python
import nodus

with nodus.Client() as client:
    for workload in client.list(limit=5):
        print(workload.id, workload.status)
```

This lists your workloads without starting paid compute.
Continue with [your first GPU workload](../../README.md#2-run-your-first-workload).

## Without a local browser

```bash
nodus login --no-browser
```

Open the printed link on another device. Approve the matching code there.
The original terminal saves the login automatically.

## API keys for automation

A team admin can create an API key in the console for CI or other automation.
Set `NODUS_API_KEY` through your secret manager. `nodus.Client()` reads it
automatically and connects to the hosted service. Never commit an API key.

## Custom deployments

Only private deployments and local development need a different API address:

```bash
nodus login --base-url https://YOUR_NODUS_API_HOST
```

Use the API origin without `/v1`. `NODUS_BASE_URL` and
`nodus.Client(base_url=...)` remain available.

| Setting | Resolution order |
|---|---|
| Credential | Explicit API key, then `NODUS_API_KEY`, then saved personal login |
| API URL | Explicit argument, then `NODUS_BASE_URL`, then saved login, then hosted default |

Each setting resolves independently. An environment variable overrides the saved
login, so keep custom deployment credentials and addresses paired.
Credentials are stored in `~/.nodus/config.toml`.

## Sign out

```bash
nodus logout
```

This revokes your personal CLI session and removes the saved login. If Nodus
cannot be reached, the login is kept so you can retry signing out. Team API keys
and environment variables are managed separately.

## Returning to Nodus

`nodus login` checks and reuses your saved sign-in. You only need the browser
when your credentials expire or you run `nodus login --force` to sign in again.
A connection problem keeps your existing credentials intact. Switching accounts
with `--force` keeps your old session until the new sign-in succeeds. Older CLI
API-key profiles are replaced through browser sign-in, without revoking team keys.
