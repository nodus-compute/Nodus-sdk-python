import pytest
from nodus._brief import build_payload


@pytest.mark.parametrize("count", [1, 2, 4, 8])
def test_exact_gpu_count_preserves_distributed_command(count):
    command = ["torchrun", "--standalone", f"--nproc_per_node={count}", "train.py"]
    payload = build_payload(command=command, gpu="H100", gpu_count=count, peak_memory_gb=80)
    assert payload["requirements"]["gpu_count"] == count
    assert payload["requirements"]["gpu"] == "H100"
    assert payload["requirements"]["peak_memory_gb"] == 80
    assert payload["source"]["command"] == command


@pytest.mark.parametrize("count", [0, -1, 3, 16, True, 8.0, "8"])
def test_invalid_gpu_count_is_rejected_before_submission(count):
    with pytest.raises(ValueError, match="gpu_count"):
        build_payload(command=["python", "train.py"], gpu_count=count)


def test_omitted_count_and_unverified_topology():
    assert "gpu_count" not in build_payload(command=["python", "train.py"])["requirements"]
    with pytest.raises(ValueError, match="topology"):
        build_payload(command=["python", "train.py"], gpu_count=8, gpu_interconnect="nvlink")
