"""Submit three paid experiments, limiting concurrent waits to two."""
import argparse
import asyncio
import nodus


async def run(args):
    semaphore = asyncio.Semaphore(2)
    async with nodus.AsyncClient() as client:
        async def experiment(index):
            async with semaphore:
                workload = await client.run(
                    image="python:3.11-slim", compute_class="vm",
                    command=["python", "-c", f"print(sum(i*i for i in range({index + 10})))"],
                    budget=args.budget_per_run, continuity="restartable",
                    idempotency_key=f"sweep-{args.run_id}-{index}",
                )
                print(index, workload.id, flush=True)
                done = await workload.wait(poll_seconds=5)
                return done.succeeded
        results = await asyncio.gather(*(experiment(i) for i in range(3)))
        return 0 if all(results) else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--budget-per-run", type=float, required=True)
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
