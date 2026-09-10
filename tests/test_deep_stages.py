import pytest

from nodus._workload_file import _stages


@pytest.mark.parametrize('cycle', [False, True])
def test_deep_stage_graph_does_not_exhaust_python_stack(cycle):
    stages = [
        {'id': f's{i}', 'source': {'command': ['true']},
         'depends_on': [f's{i + 1}'] if i < 1099 else (['s0'] if cycle else [])}
        for i in range(1100)
    ]
    if cycle:
        with pytest.raises(ValueError, match='cycle'):
            _stages(stages)
    else:
        _stages(stages)
