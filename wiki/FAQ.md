# FAQ

### Why two environment variables? Every other SDK needs one.

Because there is no built-in API address, on purpose. `NODUS_API_KEY` says who
you are; `NODUS_BASE_URL` says which control plane. Any address the SDK could
default to is either a domain that does not resolve or an account you are not
on — and both turn a five-second setup mistake into a confusing network error
somewhere deep in your stack.

So with either one missing, `nodus.Client()` refuses **before it opens a
socket** and hands you the fix. The console's quickstart panel has both lines
with your values already in them; copy it. See
[Getting Started](Getting-Started).

### Why did my workload bill for a host that never started?

Almost always the image. The Nodus runner installs itself onto the rented host
by fetching a small artifact — with `curl`, then `wget`, then a stdlib
`python3`. An image carrying none of those three cannot start your work, but the
host is rented and billed regardless.

A bare `ubuntu:22.04` has none of them. Most ML images have at least `python3`.
The SDK warns for images it has measured, while the brief is still free, but it
stays silent about images nobody has measured rather than guessing — so silence
is not approval. The one-line local check and the measured table are in
[Getting Started](Getting-Started).

### My process died mid-wait. Can I pick it back up?

Yes. The workload id is durable and has nothing to do with the process that
submitted it:

```python
with nodus.Client() as client:
    done = client.wait("wl_00000001-…")
```

Lost the id? `client.list(status="active")` finds it. And note the corollary:
closing your client or killing your script does **not** stop the workload. Only
`cancel()` does.

If what died was the *submit* rather than the wait, the answer is an idempotency
key. Pass a stable `idempotency_key=` derived from the thing you are running,
and a resubmission replays the original instead of creating a second paid
workload. Without one, every `run()` gets a fresh UUID that covers that call's
own retries and nothing beyond it. See
[Waiting and Reliability](Waiting-and-Reliability).

### Why does asking for generation 0 fail?

Because generations start at 1, and a caller that computed `0` has a bug.

`logs(generation=0)` sends `generation=0` and lets the server reject it. It used
to be read as "no filter given" and silently returned the latest generation —
which meant a loop with an off-by-one quietly showed you the wrong attempt's
log and everything looked fine. A 400 you can see beats a wrong answer you
cannot.

### Why is `spend_usd` zero on a run I know is costing me money?

Because a charge is booked when a lease closes, and yours has not closed yet.
`spend_usd` is settled charges only. Use `cost_now_usd` — settled plus what is
accruing — which is what the console and the CLI show. Full explanation in
[Costs](Costs).

### I mistyped a parameter and got a `TypeError`, not a `NodusError`.

Correct, and deliberate. `budget_usd=400` is not `budget=400`, and the API
ignores fields it does not model — so a forwarded typo is *accepted*, answers
202, and submits a run with no cost ceiling at all. Refusing it locally is the
only way you find out. It is a `TypeError` because it is a bug in your code
rather than an answer from the API. `extra={…}` is the deliberate way to send a
field the SDK does not know. See [The Brief](The-Brief).

### Is `logs()` a live tail?

No. The log is collected as a committed artifact, and the control plane
recomputes its digest before agreeing the run produced it — so it lags the
process by a checkpoint, and raises `NotFoundError` until the first manifest
carries one. What you get back is evidence rather than a tail. For live
progress, use `stream_events()`.

### Is there a `nodus.run(...)` one-liner?

No, and there will not be one. It existed, and it closed its client before
returning the handle, so every follow-up call on what it handed back failed. One
idiom: build a client, submit on it, wait on it.

### What is *not* in the SDK

Worth knowing before you design around it:

- **No file upload, no volumes, no data staging.** There is nothing that puts a
  local file on the machine. Bake data into your image, or have your program
  fetch it at startup.
- **No `inputs=`, no `env=`.** The control plane does not model input staging
  or per-run environment variables yet, so `run()` refuses both keywords with a
  `TypeError` naming them unsupported rather than sending fields the API would
  silently throw away. Configuration goes into your image or on the command
  line.
- **No download helper for outputs.** `artifacts()` lists *manifests* — a
  manifest names many objects — and `ManifestFile.uri` is a key in Nodus-held
  storage, not a URL you can fetch. The dedicated outputs endpoint is not
  modelled in this version.
- **No `verified` flag on an artifact.** A manifest is written only after the
  digests are recomputed, but the response carries no per-row verification
  state, and claiming one would assert a check the SDK never saw. Compare
  `ManifestFile.sha256` against bytes you fetched yourself if you need it.
- **No price book, no `quote`, no `sources`.** Removed: they resolved a path
  inside the monorepo, so they were dead the moment the package was installed.
  `nodus explain` survives, and reads the real routing from the control plane.
- **No live log streaming**, per above.

### What else is on the client that the pages do not cover?

```python
client.healthz()          # {'status': 'ok'} — cheap wiring check
client.readyz()
client.set_webhook(url, secret=…)   # be told about lifecycle events
client.get_webhook()
client.delete_webhook()
```

Webhook payload verification is server-side; a rejected signature comes back as
`SignatureError`. See [Errors](Errors).

### The API sent a status my SDK version has never heard of. Did it crash?

No. Unknown wire values coerce to a plain string rather than raising, so a
newer control plane cannot break an older client over a field it only meant to
display. An unknown status is also not terminal, so a `wait()` keeps waiting
rather than declaring victory.

### Why is the package `nodus_compute` but the import `nodus`?

The distribution name and the import name are simply different: `pip install
nodus_compute`, then `import nodus`.
<!-- import-name decision pending -->

---

Still stuck? [File an issue](https://github.com/Nodus-compute/Nodus-sdk-python/issues).
