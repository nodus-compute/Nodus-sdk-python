"""Customers describe work. Duration belongs to Nodus."""

import inspect

import pytest

import nodus
from nodus._brief import build_payload
from nodus._workload_file import load_workload_file


@pytest.mark.parametrize("client", [nodus.Client, nodus.AsyncClient])
def test_duration_is_not_a_customer_parameter(client):
    assert "expected_runtime_hours" not in inspect.signature(client.run).parameters


@pytest.mark.parametrize("kwargs", [
    {"expected_runtime_hours": 0.01},
    {"requirements": {"expected_runtime_hours": 0.01}},
    {"requirements": {"EXPECTED_RUNTIME_HOURS": 0.01}},
    {"extra": {"requirements": {"expected_runtime_hours": 0.01}}},
    {"stages": [{"id": "train", "source": {"image": "python:3.12", "command": ["true"]},
                 "requirements": {"expected_runtime_hours": 0.01}}]},
])
def test_duration_cannot_be_submitted_through_another_surface(kwargs):
    with pytest.raises((TypeError, ValueError), match="Nodus.*runtime"):
        build_payload(**kwargs)


def test_duration_word_is_allowed_as_an_output_name():
    payload = build_payload(stages=[{
        "id": "train", "source": {"image": "python:3.12", "command": ["true"]},
        "outputs": {"expected_runtime_hours": "result.txt"},
    }])
    assert payload["stages"][0]["outputs"] == {"expected_runtime_hours": "result.txt"}


@pytest.mark.parametrize("section", ["", "[requirements]\n", "[[stages]]\nid = 'train'\n[stages.requirements]\n"])
def test_workload_file_rejects_duration_before_submission(tmp_path, section):
    path = tmp_path / "nodus.toml"
    path.write_text("image = 'python:3.12'\ncommand = ['true']\n" + section + "expected_runtime_hours = 1\n")
    with pytest.raises((TypeError, ValueError), match="Nodus.*runtime"):
        load_workload_file(path)
