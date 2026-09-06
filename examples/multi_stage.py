"""Produce and consume declared outputs on paid VM workloads."""
import argparse
from pathlib import Path
import nodus


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--submission-id", required=True)
    parser.add_argument("--budget", type=float, required=True)
    args = parser.parse_args()
    stages = [
        nodus.StageSpec(
            id="prepare",
            source=nodus.Source(
                image="python:3.11-slim",
                command=["python", "-c", "from pathlib import Path\nPath('numbers.json').write_text('[1, 2, 3]')"],
            ),
            outputs={"numbers": "numbers.json"},
        ),
        nodus.StageSpec(
            id="summarize", depends_on=["prepare"],
            inputs=[nodus.StageInput(name="numbers", from_stage="prepare", from_output="numbers")],
            source=nodus.Source(
                image="python:3.11-slim",
                command=["python", "-c", "import json, os\nfrom pathlib import Path\nvalues=json.loads(Path(os.environ['NODUS_INPUT_numbers']).read_text())\nPath('result.json').write_text(json.dumps({'sum': sum(values)}))"],
            ),
            outputs={"result": "result.json"},
        ),
    ]
    with nodus.Client() as client:
        workload = client.run(
            stages=stages, compute_class="vm", continuity="restartable",
            budget=args.budget, idempotency_key=f"pipeline-{args.submission_id}",
        )
        print(workload.id, flush=True)
        if not workload.wait(poll_seconds=5).succeeded:
            return 1
        Path("results").mkdir(exist_ok=True)
        print(workload.download_output("result", "results/result.json", stage="summarize"))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
