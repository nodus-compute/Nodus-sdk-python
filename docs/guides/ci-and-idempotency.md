# CI and safe retries

Provide `NODUS_API_KEY` and `NODUS_BASE_URL` through your CI secret manager.
Use a stable ID for one logical submission, preserved across job retries:

```bash
python examples/ci_submit.py --submission-id YOUR_PIPELINE_RUN_ID --budget 5
```

Each `run()` gets a fresh UUID unless `idempotency_key` is set. That UUID protects
only retries inside that call. Application retries and restarted CI jobs need
the same explicit key and exactly the same brief to avoid duplicate paid work.
A different payload under the same key raises `IdempotencyConflictError`.

`run()` returns an accepted handle; log its ID before waiting. `wait()` returns
on all terminal states. A CI job must inspect `done.succeeded`, as the example
does, to fail on a failed or cancelled workload.

A submission timeout or connection failure can leave the outcome unknown.
Retry with the original key. For automatically generated keys, transport errors
expose the submission key in `error.payload`; retain it if recovering manually.
Do not assume a network exception means the server created nothing.

Cancellation is a separate idempotent request: `client.cancel(workload_id)`.
Choose explicitly whether a CI timeout should cancel remote work or permit it
to finish. See [reliability](../concepts/reliability.md).
