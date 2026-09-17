"""Process one immutable batch and produce a downloadable result file."""
import json
import sys
import time
from pathlib import Path


def process(items):
    import torch

    values = torch.tensor([item["value"] for item in items], device="cuda")
    started = time.monotonic()
    results = torch.sigmoid(values).tolist()
    elapsed_ms = (time.monotonic() - started) * 1000
    for item in items:
        print("nodus.unit_done " + json.dumps({"id": item["id"], "ms": elapsed_ms / len(items)}), flush=True)
    return [{"id": item["id"], "score": score} for item, score in zip(items, results)]


if __name__ == "__main__":
    Path("results.json").write_text(json.dumps(process(json.loads(sys.argv[1]))), encoding="utf-8")
