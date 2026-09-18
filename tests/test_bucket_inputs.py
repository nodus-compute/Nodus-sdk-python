import copy
import pytest
from nodus._brief import build_payload
from nodus._workload_file import load_workload_file

BUCKET = dict(uri='s3://training-bucket/corpus.jsonl', region='us-east-1', bytes=3,
              sha256='ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad', credential_source='team_webhook')


def test_bucket_wire_preserves_exact_region_budget_and_descriptor():
    inputs = [dict(name='corpus', bucket=dict(BUCKET))]
    result = build_payload(command=['python', 'train.py'], inputs=inputs, data_regions=['us-east-1'], budget=2)
    assert result['inputs'] == inputs
    assert result['policy']['data_regions'] == ['us-east-1']
    assert result['outcome']['max_cost_usd'] == 2


@pytest.mark.parametrize('change', [
    {'uri': 's3://training-bucket/data?token=secret'}, {'uri': 's3://user:secret@training-bucket/data'},
    {'uri': 'https://training-bucket/data'}, {'uri': 's3://training-bucket/a/../b'},
    {'credential_source': 'environment'}, {'secret_access_key': 'never-store'}, {'bytes': True},
    {'sha256': 'invalid'}, {'region': 'us-east-1.attacker'},
])
def test_bucket_rejects_credentials_and_ambiguous_objects(change):
    bucket = dict(BUCKET, **change)
    with pytest.raises(ValueError):
        build_payload(command=['python', 'train.py'], inputs=[dict(name='corpus', bucket=bucket)], data_regions=['us-east-1'])


@pytest.mark.parametrize('regions', [None, ['us-west-2'], ['us-east-1', 'us-west-2']])
def test_bucket_requires_one_exact_region(regions):
    with pytest.raises(ValueError):
        build_payload(command=['python', 'train.py'], inputs=[dict(name='corpus', bucket=BUCKET)], data_regions=regions)


def test_workload_file_passes_bucket_descriptor_without_credentials(tmp_path):
    path = tmp_path / 'nodus.toml'
    path.write_text('command=["python", "train.py"]\nbudget=2\ndata_regions=["us-east-1"]\n'
                    '[[inputs]]\nname="corpus"\n[inputs.bucket]\n' + '\n'.join(f'{key}={value!r}' for key, value in BUCKET.items()))
    values = load_workload_file(path)
    assert values['inputs'] == [dict(name='corpus', bucket=BUCKET)]
    assert build_payload(**values)['outcome']['max_cost_usd'] == 2


def test_workload_file_empty_policy_preserves_top_level_bucket_region(tmp_path):
    path = tmp_path / 'nodus.toml'
    path.write_text('command=["python", "train.py"]\nbudget=2\ndata_regions=["us-east-1"]\npolicy={}\n'
                    '[[inputs]]\nname="corpus"\n[inputs.bucket]\n' + '\n'.join(f'{key}={value!r}' for key, value in BUCKET.items()))
    payload = build_payload(**load_workload_file(path))
    assert payload['policy'] == {'data_regions': ['us-east-1']}
    assert payload['inputs'] == [{'name': 'corpus', 'bucket': BUCKET}]
