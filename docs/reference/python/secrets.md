# Tenant secrets

`client.secrets.put(name, value)` creates a new tenant secret version and returns
its ID, name, version and creation time. `client.secrets.list()` returns current
metadata without values. `client.secrets.delete(name)` retires the current
version. The same methods are available through `AsyncClient` with `await`.

Pass `secrets=["API_KEY"]` to `client.sandboxes.create()` to bind those names at
boot. Commands receive each value as an environment variable and as a file named
for the secret under `NODUS_SECRETS_DIR`. Names must be environment variable
names outside the reserved `NODUS_` prefix. A sandbox can select at most 32 names,
and each value can contain up to 4096 UTF-8 bytes without NUL characters.

Writing a new version leaves running sandbox bindings unchanged.
`sandbox.refresh_secrets()` binds current versions for subsequent commands.
Already running processes keep their environment. A wake binds current versions.
Retired versions are destroyed after seven days once no live generation remains
bound to them. A live generation keeps its bound versions for injection and output
redaction until it ends.

Stored and streamed command output replaces matching secret values with
`[redacted:NAME]`. Possible secret fragments at output frame boundaries are
conservatively redacted too, so a matching fragment of ordinary text can be
masked. Passing a retained secret value directly in an exec `env` is rejected.

Injected files reside in memory-backed storage outside the workspace and
recovery state. Customer code must keep secret values out of files it saves.

Use `policy={"secret_refs": ["API_KEY"]}` when creating a sandbox to pin the
current version at admission. A returned `sec_` ID can also be used. Commands
receive this value as `NODUS_SECRET_API_KEY`. Rotation, revocation, recovery, and
`refresh_secrets()` preserve this admission pin. Revocation prevents new
admissions from selecting the secret. Pinned ciphertext remains available until
the sandbox terminates, including while it is recovering or suspended.

The CLI reads exact UTF-8 input without stripping a trailing newline. Values are
never accepted as command arguments or printed in normal command output.

```bash
nodus secret set API_KEY --from-file /path/to/private-key
nodus secret ls
nodus secret rm API_KEY
```

You can also pipe the value on stdin to `nodus secret set API_KEY`.
