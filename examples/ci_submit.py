"""Submit paid work with a stable ID retained across CI retries."""
import argparse
import nodus


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--submission-id", required=True)
    parser.add_argument("--budget", type=float, required=True)
    args = parser.parse_args()
    with nodus.Client() as client:
        workload = client.run(
            image="python:3.11-slim", compute_class="vm",
            command=["python", "-c", "assert sum(range(10)) == 45; print('passed')"],
            budget=args.budget, continuity="restartable",
            idempotency_key=f"ci-{args.submission_id}",
        )
        print(workload.id, flush=True)
        return 0 if workload.wait(poll_seconds=5).succeeded else 1


if __name__ == "__main__":
    raise SystemExit(main())
