"""Typed advanced requests must retain the existing dictionary wire format."""

import json

from nodus._brief import build_payload
from nodus.requests import ContinuitySpec, Policy, Requirements, Source, StageInput, StageSpec
from nodus.types import ComputeClass, ContinuityMode


def test_typed_requirements_and_continuity_preserve_payload_and_precedence():
    requirements = Requirements(
        compute_class=ComputeClass.VM,
        dataset_bytes=1024,
        peak_memory_gb=8,
        notes="CPU preprocessing",
    )
    continuity = ContinuitySpec(mode=ContinuityMode.RESTARTABLE)
    policy = Policy(data_regions=["us-east"])
    payload = build_payload(
        command=["python", "prepare.py"],
        requirements=requirements,
        peak_memory_gb=16,
        continuity=continuity,
        policy=policy,
        budget=2,
    )
    wire = json.loads(json.dumps(payload))
    assert wire["requirements"] == {
        "compute_class": "vm",
        "dataset_bytes": 1024,
        "peak_memory_gb": 8,
        "notes": "CPU preprocessing",
    }
    assert wire["continuity"] == {"mode": "restartable", "resume_on_interruption": True}
    assert wire["policy"] == {"data_regions": ["us-east"]}
    assert "resume_on_interruption" not in continuity


def test_typed_stage_graph_serializes_like_existing_dictionary_graph():
    prepare = StageSpec(
        id="prepare",
        source=Source(image="python:3.11-slim", command=["python", "prepare.py"]),
        outputs={"dataset": "dataset.json"},
        requirements=Requirements(compute_class="vm"),
    )
    train = StageSpec(
        id="train",
        source=Source(image="python:3.11-slim", command=["python", "train.py"]),
        depends_on=["prepare"],
        inputs=[StageInput(name="dataset", from_stage="prepare", from_output="dataset")],
        continuity=ContinuitySpec(mode="checkpointed", resume_on_interruption=True),
        total_units=100,
        outputs={"model": "model.bin"},
    )
    payload = build_payload(stages=[prepare, train], budget=10)
    assert "source" not in payload
    assert json.loads(json.dumps(payload))["stages"] == [prepare, train]
    assert type(prepare) is dict
    assert "continuity" not in prepare
