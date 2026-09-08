import math

import pytest

import nodus
from nodus._brief import build_payload
from nodus._terminal import workload_rows


@pytest.mark.parametrize('budget', [True, False, 0, -1, math.nan, math.inf, -math.inf,
                                   pytest.param(10 ** 1000, id='huge'), [], {}, 'not-money'])
def test_invalid_budget_has_actionable_error(budget):
    with pytest.raises(ValueError, match='budget.*finite positive'):
        build_payload(command=['python', 'train.py'], budget=budget)


@pytest.mark.parametrize('amount,expected', [
    (0, '$0.00'), (1.25, '$1.25'), (0.000312, '$0.000312'),
    (0.001, '$0.001'), (0.0000001, '<$0.000001'),
])
def test_small_observed_cost_does_not_look_free(amount, expected):
    workload = nodus.Workload(None)
    workload._absorb({'id': 'wl_test', 'status': 'completed', 'spend_usd': amount})
    assert dict(workload_rows(workload))['Cost'] == expected
