# Nodus Python SDK

You describe the work — what to run, how much memory it needs, what you are
willing to pay — and Nodus finds capacity, runs it, checkpoints it, and resumes
it if the machine is taken back. You never name a machine, an instance type, or
a vendor. This SDK is the Python front door to that: submit a brief, wait for
it, read what it cost.

The distribution is `nodus_compute`; the import is `nodus`.
<!-- import-name decision pending -->

## First run

```python
import nodus

with nodus.Client() as client:
    wl = client.run(command=["python", "train.py"], budget=20)
    done = client.wait(wl.id)
    print(done.status, done.cost_now_usd)
```

That needs two environment variables set first. See
[Getting Started](Getting-Started).

## The pages

| Page | What it answers |
|---|---|
| [Getting Started](Getting-Started) | Install, the two settings, your first run, and the free check that saves you from paying for a machine that never starts. |
| [The Brief](The-Brief) | Every `run()` parameter, what it defaults to, and what happens if you leave it out. |
| [Waiting and Reliability](Waiting-and-Reliability) | What `wait()` does and does not promise, streaming events, and how to make a resubmit safe. |
| [Costs](Costs) | `cost_now_usd` vs `spend_usd`, the meter, your budget vs your account cap, and the ledger. |
| [Errors](Errors) | Which failures clear on their own, which never do, and what to do about each. |
| [CLI](CLI) | The `nodus` command: every subcommand, the `--` rule, exit codes. |
| [Async](Async) | `AsyncClient`, and the guarantee that it is not a subset. |
| [FAQ](FAQ) | The awkward questions, answered honestly — including what is *not* in the SDK. |

## Where things live

- Package: `nodus_compute` on PyPI
- Import: `import nodus`
- Console (API keys, spend caps, quickstart): <https://nodus.run/console/>
- Source: <https://github.com/Nodus-compute/Nodus-sdk-python>

## Version

`nodus.__version__` reads the installed distribution's metadata. From a source
tree that was never installed it reads `0.0.0+source` — deliberately not
something that could be mistaken for a release.

---

Found a gap, a wrong claim, or something this wiki does not answer?
[File an issue](https://github.com/Nodus-compute/Nodus-sdk-python/issues).
