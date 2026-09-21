"""Submit a workload file, observe completion and verify its output files."""
from __future__ import annotations

import hashlib
import math
import os
from pathlib import Path
import re
import sys

from nodus import Client, NodusError
from nodus._outputs import output_destinations
from nodus._workload_file import load_workload_file


def output(name: str, value: str) -> None:
    if "\n" in value or "\r" in value:
        raise ValueError("Action output must be a single line")
    target = os.environ.get("GITHUB_OUTPUT")
    if target:
        with open(target, "a", encoding="utf-8") as stream:
            stream.write(f"{name}={value}\n")


def main() -> int:
    api_key = os.environ.get("NODUS_API_KEY", "")
    if not api_key.strip():
        raise ValueError("Provide the action's api-key input from a GitHub Actions secret")
    api_url = os.environ.get("NODUS_BASE_URL", "")
    if not api_url.strip():
        raise ValueError("Provide the action's api-url input instead of a saved account address")
    filename = os.environ.get("NODUS_WORKLOAD_FILE", "nodus.toml")
    settings = load_workload_file(filename)
    budget = settings.get("budget")
    if isinstance(budget, bool) or not isinstance(budget, (int, float)) or not math.isfinite(budget) or budget <= 0:
        raise ValueError("Set a positive budget in your workload file before running the action")
    selected = os.environ.get("NODUS_IDEMPOTENCY_KEY") or settings.get("idempotency_key")
    if os.environ.get("NODUS_IDEMPOTENCY_KEY") and settings.get("idempotency_key") not in (None, selected):
        raise ValueError("The action and workload file specify different idempotency keys")
    if not selected:
        fields = [os.environ.get(name, "") for name in ("GITHUB_REPOSITORY", "GITHUB_RUN_ID", "GITHUB_JOB")]
        if not all(fields):
            raise ValueError("Outside GitHub Actions, provide NODUS_IDEMPOTENCY_KEY")
        # Attempts of the same GitHub run retain identity even when the request changes.
        selected = "gha-" + hashlib.sha256("\n".join([*fields, filename]).encode()).hexdigest()
    settings["idempotency_key"] = selected
    timeout = float(os.environ.get("NODUS_WAIT_TIMEOUT", "3600"))
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("wait-timeout must be a positive number of seconds")
    destination = Path(os.environ.get("NODUS_OUTPUT_DIRECTORY", "nodus-results"))
    if any(part.is_symlink() for part in (destination, *destination.parents)):
        raise ValueError("The output directory must not contain symbolic links")
    with Client(api_key=api_key, base_url=api_url) as client:
        workload = client.run(**settings)
        if not re.fullmatch(r"[A-Za-z0-9_-]+", workload.id):
            raise ValueError("Nodus returned an invalid workload ID")
        output("workload-id", workload.id)
        print("Nodus workload " + workload.id, flush=True)
        workload.wait(timeout_seconds=timeout, progress=False)
        output("status", workload.status.value if hasattr(workload.status, "value") else str(workload.status))
        if not workload.succeeded:
            print("Workload did not complete successfully. Inspect its status and logs in Nodus.", file=sys.stderr)
            return 1
        available = workload.outputs()
        declared = [(None, name) for name in settings.get("outputs", {})]
        for stage in settings.get("stages", []):
            declared.extend((stage["id"], name) for name in stage.get("outputs", {}))
        if any(not any(item.name == name and (stage is None or item.stage_id == stage) for item in available)
               for stage, name in declared):
            raise ValueError("A declared output is missing. Results are incomplete")
        files = []
        for item, path in output_destinations(destination, available):
            path.parent.mkdir(parents=True, exist_ok=True)
            files.append(workload.download_output(item.name, path, stage=item.stage_id, overwrite=False))
        output("output-directory", str(destination.resolve()))
        output("output-count", str(len(files)))
        print(f"Completed with {len(files)} verified outputs.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (NodusError, ValueError, OSError) as exc:
        print("Nodus action failed (" + type(exc).__name__ + "). Check the workload file and Nodus console.", file=sys.stderr)
        print("Retry this same GitHub run to reuse its submission key. A waiting timeout does not cancel running compute.", file=sys.stderr)
        raise SystemExit(1)
