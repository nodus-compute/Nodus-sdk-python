from pathlib import Path
import re
import pytest

from nodus._brief import GPU_FAMILIES, OPTIMIZATIONS


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('filename,column', [('requirements.md', 2), ('index.md', 3)])
def test_resource_parameter_rows_define_every_accepted_choice(filename, column):
    document = (ROOT / 'docs/reference/parameters' / filename).read_text(encoding='utf-8')
    for parameter, choices in [('gpu', GPU_FAMILIES), ('optimization', OPTIMIZATIONS)]:
        row = next(line for line in document.splitlines() if line.startswith(f'| `{parameter}` |'))
        documented = set(re.findall(r'`"([^"]+)"`', row.split('|')[column]))
        assert documented == set(choices), f'{parameter} choices must be complete at the parameter row'
