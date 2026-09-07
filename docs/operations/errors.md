# Errors and troubleshooting

API and transport errors inherit `nodus.NodusError`. Inspect `.status_code`,
`.code`, `.payload`, and `.request_id`. Include the request ID when reporting an
issue. Python argument mistakes (`TypeError` / `ValueError`) are separate.

| Error | Action |
|---|---|
| `ConfigurationError` | Set the missing URL/key or fix configuration |
| `AuthenticationError` | Check deployment/key pairing, expiry, or revocation |
| `SignatureError` | Check signing secret and clock for signed requests |
| `ValidationError` | Correct the rejected request field |
| `IdempotencyConflictError` | Reuse the original payload or assign a new logical key |
| `NotFoundError` | Check workload ownership/ID. Logs may not yet be committed |
| `BudgetExceededError` | Inspect headroom and adjust actual work or account limit |
| `RateLimitError` | Pace requests. SDK honors bounded retry-after delays |
| `CapacityUnavailableError` | Retry later or relax feasible workload constraints |
| `APIConnectionError` / `APITimeoutError` | Preserve submission key. Outcome may be unknown |
| `APIError` | Inspect HTTP status and response payload |

SDK request retries are finite. Capacity becoming available is not guaranteed.
A network exception does not prove a submission failed to reach the server.
See [idempotency](../guides/ci-and-idempotency.md) and
[retry policy](../concepts/reliability.md).

## Common first-run problems

- **Script not found:** put it in the container image and use an absolute path.
- **No bootstrap tool:** include `curl`, `wget`, or `python3` in the image.
- **No log yet:** inspect lifecycle events and retry when logs become available.
- **Login changed nothing:** environment variables override saved credentials.
- **Wait returned but work failed:** inspect `succeeded`, events, and logs.
- **Cancellation unconfirmed:** run `nodus cancel WORKLOAD_ID` and inspect status.
  A lost connection or force-killed process cannot confirm remote cleanup.
- **Download failed:** ensure the destination parent exists, choose a declared
  output name/stage, and retry. Integrity failures leave existing files intact.
- **Missing output methods:** upgrade with
  `pip install --upgrade nodus-compute`.

An omitted budget or known image without a fetch tool emits `UserWarning` before
submission. Applications may turn warnings into errors using Python's warnings
filters if that suits their policy.
