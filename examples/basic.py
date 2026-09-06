"""Submit a paid self-contained VM workload after configuring Nodus credentials."""
import argparse
import nodus


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--budget", type=float, required=True)
    args = parser.parse_args()
    with nodus.Client() as client:
        workload = client.run(
            image="python:3.11-slim",
            command=["python", "-c", "print('Hello from Nodus')"],
            compute_class="vm", budget=args.budget, continuity="restartable",
        )
        print(workload.id, flush=True)
        done = workload.wait()
        print(done.status, done.cost_now_usd)
        return 0 if done.succeeded else 1


if __name__ == "__main__":
    raise SystemExit(main())
