from pathlib import Path
import re

from nodus._brief import GPU_FAMILIES, OPTIMIZATIONS


ROOT = Path(__file__).resolve().parents[1]


def test_resource_parameter_rows_define_every_accepted_choice():
    document = (ROOT / 'docs/reference/parameters/requirements.md').read_text(encoding='utf-8')
    for parameter, choices in [('gpu', GPU_FAMILIES), ('optimization', OPTIMIZATIONS)]:
        row = next(line for line in document.splitlines() if line.startswith(f'| `{parameter}` |'))
        documented = set(re.findall(r'`"([^"]+)"`', row.split('|')[2]))
        assert documented == set(choices), f'{parameter} choices must be complete at the parameter row'
