import pytest

from nodus._brief import build_payload


INVALID = [True, False, "4", None, [], {}, float("nan"), float("inf"), -1, 10**1000]


@pytest.mark.parametrize("field", ["peak_memory_gb", "disk_gb", "vcpus", "dataset_bytes"])
@pytest.mark.parametrize("value", INVALID, ids=lambda value: type(value).__name__)
@pytest.mark.parametrize("stage", [False, True])
def test_invalid_numeric_requirements_rejected(field, value, stage):
    requirement = {field: value}
    arguments = {"requirements": requirement}
    if stage:
        arguments = {"stages": [{"id": "main", "requirements": requirement}]}
    with pytest.raises(ValueError, match=field):
        build_payload(budget=1, **arguments)


@pytest.mark.parametrize("value", [0, -1, True, "24", float("inf"), float("nan")])
def test_flat_memory_rejects_invalid_values(value):
    with pytest.raises(ValueError, match="peak_memory_gb"):
        build_payload(peak_memory_gb=value, budget=1)


@pytest.mark.parametrize("stage", [False, True])
@pytest.mark.parametrize("field,value", [("peak_memory_gb", 0), ("dataset_bytes", 0.5)])
def test_numeric_field_specific_bounds(field, value, stage):
    arguments = {"requirements": {field: value}}
    if stage:
        arguments = {"stages": [{"id": "main", **arguments}]}
    with pytest.raises(ValueError, match=field):
        build_payload(budget=1, **arguments)


@pytest.mark.parametrize("stage", [False, True])
def test_valid_values_preserve_fractional_capacity_and_zero_inheritance(stage):
    requirements = {"peak_memory_gb": 24.5, "disk_gb": 64.5, "vcpus": 0.5, "dataset_bytes": 0}
    arguments = {"requirements": requirements}
    if stage:
        arguments = {"stages": [{"id": "main", **arguments}]}
    payload = build_payload(budget=1, **arguments)
    actual = payload["stages"][0]["requirements"] if stage else payload["requirements"]
    assert all(actual[key] == value for key, value in requirements.items())
    assert requirements == {"peak_memory_gb": 24.5, "disk_gb": 64.5, "vcpus": 0.5, "dataset_bytes": 0}
    zeros = build_payload(requirements={"disk_gb": 0, "vcpus": 0}, budget=1)
    assert zeros["requirements"]["disk_gb"] == zeros["requirements"]["vcpus"] == 0


@pytest.mark.parametrize("alias", ["RTX-4090", "RTX_4090", "nvidia RTX-4090", "NVIDIARTX4090", "RTX4090"])
@pytest.mark.parametrize("stage", [False, True])
def test_gpu_aliases_serialize_canonical_family(alias, stage):
    arguments = {"gpu": alias}
    if stage:
        arguments = {"stages": [{"id": "main", "requirements": {"gpu": alias}}]}
    payload = build_payload(budget=1, **arguments)
    actual = payload["stages"][0]["requirements"] if stage else payload["requirements"]
    assert actual["gpu"] == "RTX 4090"


@pytest.mark.parametrize("value", [True, False, -1, 0.5, "1", None, [], {}, float("inf"), float("nan")])
def test_stage_total_units_rejects_invalid_counts(value):
    with pytest.raises(ValueError, match="total_units"):
        build_payload(stages=[{"id": "main", "total_units": value}], budget=1)


@pytest.mark.parametrize("stage", [{"id": "main"}, {"id": "main", "total_units": 0}, {"id": "main", "total_units": 10}])
def test_stage_total_units_preserves_optional_nonnegative_counts(stage):
    payload = build_payload(stages=[stage], budget=1)
    assert payload["stages"][0] == stage
