# Nodus Python SDK — working rules for Claude

This is a published client library. Every public name, default, and README
example is a promise to someone who cannot see this repo.

## Test-driven, strictly

Write the failing test first and watch it fail — red, then green, in that
order. A test that was never seen red proves nothing: before accepting a new
assertion, make it fail against the old code once. Fake a failure at the layer
it really happens (wire bytes, an HTTP status, a header), never by raising the
SDK's own exception type from a patched internal call.

## Definition of Done

A task is not done when the code is written. It is done when all three hold:

1. **Verified by execution.** The full test suite (and any other claimed
   command) actually ran *this session* and its output is reported. Anything
   not executed is reported as **written, not run** — it never counts as done,
   and never passes silently.
2. **Red-teamed.** Any diff touching money (cost and budget fields, spend
   arithmetic, idempotency of paid submissions) or security (credentials,
   URLs, header construction, input validation, what gets printed or logged)
   gets an adversarial review by the `red-team` agent
   (`.claude/agents/red-team.md`) before it is declared done. No exemption for
   a small diff, time pressure, or green tests.
3. **Critical findings closed** — fixed, or explicitly accepted by the founder
   in writing. Never silently parked, never summarised away.

## No invented numbers

The SDK never makes up a number on the caller's behalf: no default budget, no
default deadline, no default address, no money figure derived client-side when
the server did not send one. Where a value is missing, say so — `None`, a
warning, a refusal. A guessed number that is wrong about money is worse than
no number.

## Fixtures mirror the real wire

A test fixture is a claim about what the server actually sends. Take shapes
from the control plane's real handlers and responses — field names, which side
of debit/credit, value not pointer, null versus absent — never from what the
SDK would find convenient. A fixture invented to fit the code cannot
contradict it. When the real shape is unknown, finding it out comes before
writing the test.

## Comments are timeless and concise

A comment is 1–2 lines, present tense, and states only the invariant the code
cannot show. It must read true against today's tree alone: no narration, no
edit history, no "previously / now / no longer" — that story belongs in the
commit message. Docstrings on public API stay: first line says what it does,
a couple more lines at most. See the `writing-timeless-comments` skill; the
red-team flags violations.

## Release rules

- **A version is permanent.** PyPI will not accept the same version twice, and
  a deleted release does not free its number. The version bump and its
  CHANGELOG entry travel in the same commit; the tag is the trigger. See
  RELEASING.md.
- **README examples must execute.** Every command and code block a customer
  can copy is run against the real surface before it ships, and the README is
  updated in the same change that alters a flag, a default, or what a call
  costs. A documented flag the parser rejects is a defect.
- **Never document a capability the API does not have.** A parameter that
  lands on no server field is refused by the SDK, not accepted and thrown
  away.
