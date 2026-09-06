# Installation and authentication

Requires Python 3.10 or newer. This guide requires SDK version 0.1.2 or newer.
PyPI 0.1.1 lacks device login and output download helpers. Install this checkout:

```bash
git clone https://github.com/nodus-compute/Nodus-sdk-python.git
cd Nodus-sdk-python
python -m pip install -e .
nodus --version
```

After version 0.1.2 appears on PyPI, you can instead install with
`python -m pip install --upgrade "nodus_compute>=0.1.2"`.

Ask your Nodus onboarding contact for account access and your deployment API
base URL. For API-key authentication, obtain a key through the same onboarding
process. There is no public signup endpoint documented here.

## Interactive login

```bash
nodus login --base-url https://YOUR_NODUS_API_HOST
```

Use the API base URL issued for your deployment, without `/v1`. There is no
built-in hosted endpoint. Approve the displayed code at the displayed verification
URL. Credentials are stored in `~/.nodus/config.toml`. Device authorization must
be enabled on that deployment. If it is unavailable, use the API-key method below.

## Headless login

```bash
nodus login --base-url https://YOUR_NODUS_API_HOST --no-browser
```

Open the printed verification URL on a device with a browser and enter the code.
The initiating terminal waits for approval and saves the credentials locally.

## API key: CI, servers, and notebooks

Inject these variables from your deployment's secret manager:

```bash
export NODUS_BASE_URL='https://YOUR_NODUS_API_HOST'
export NODUS_API_KEY='YOUR_API_KEY'
```

Then `nodus.Client()` and the CLI use them automatically. Avoid embedding actual
keys in source files, notebook output, or checked-in shell scripts.
For an explicitly configured client:

```python
import os
import nodus

with nodus.Client(
    api_key=os.environ["NODUS_API_KEY"],
    base_url=os.environ["NODUS_BASE_URL"],
) as client:
    print([workload.id for workload in client.list(limit=5)])
```

This lists workloads without submitting compute. It requires a valid account key.

## Configuration precedence

| Setting | First | Second | Third |
|---|---|---|---|
| API key | `Client(api_key=...)` | `NODUS_API_KEY` | Saved config |
| API URL | `Client(base_url=...)` / CLI `--base-url` | `NODUS_BASE_URL` | Saved config |

Each setting resolves independently. Environment variables can override a new
login, including with a different deployment. Keep the key and URL paired.
Missing configuration raises `ConfigurationError` before network access.
For ordinary CLI commands put global flags first:
`nodus --base-url https://YOUR_NODUS_API_HOST list`.

## Logout

```bash
nodus logout
```

Logout removes the saved key. It does not revoke the key at the server or unset
shell variables. Revoke the key in your deployment's console when retiring it.
