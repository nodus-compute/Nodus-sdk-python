"""Verify a built or published SDK in an isolated environment using local fixtures."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]


def isolated_environment(home: Path) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items()
           if not key.upper().startswith(('NODUS_', 'PYTHONPATH', 'PYTHONHOME'))}
    env.update(HOME=str(home), USERPROFILE=str(home), PYTHONNOUSERSITE='1',
               PIP_DISABLE_PIP_VERSION_CHECK='1')
    return env


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--wheel', type=Path)
    source.add_argument('--version', help='published PyPI version to verify')
    parser.add_argument('--report', type=Path, help='save the verification result as JSON')
    args = parser.parse_args()
    target = str(args.wheel.resolve()) if args.wheel else f'nodus-compute=={args.version.removeprefix("v")}'
    report = {'artifact': target, 'status': 'failed', 'stage': 'setup'}
    result = 1
    try:
        with tempfile.TemporaryDirectory(prefix='nodus-verification-') as directory:
            work = Path(directory)
            home = work / 'home'
            home.mkdir()
            env = isolated_environment(home)
            environment = work / 'venv'
            subprocess.run([sys.executable, '-m', 'venv', str(environment)], check=True, env=env)
            python = environment / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
            report['stage'] = 'install'
            for attempt in range(6 if args.version else 1):
                installed = subprocess.run([str(python), '-m', 'pip', 'install', '--no-cache-dir',
                                            '--index-url', 'https://pypi.org/simple', target,
                                            'pytest', 'pytest-asyncio'], env=env)
                if installed.returncode == 0:
                    break
                if not args.version or attempt == 5:
                    raise subprocess.CalledProcessError(installed.returncode, installed.args)
                print('Waiting for the published package to appear in the index.', flush=True)
                time.sleep(10)
            suite = work / 'suite'
            suite.mkdir()
            for name in ('tests', 'docs', 'examples', 'openapi', 'scripts', 'README.md',
                         'LICENSE', 'RELEASING.md', 'CHANGELOG.md', 'pyproject.toml'):
                source_path = ROOT / name
                if source_path.is_dir():
                    shutil.copytree(source_path, suite / name,
                                    ignore=shutil.ignore_patterns('__pycache__', '.pytest_cache'))
                else:
                    shutil.copy2(source_path, suite / name)
            report['stage'] = 'tests'
            metadata = subprocess.check_output([str(python), '-c',
                'import json, nodus\nprint(json.dumps({"version": nodus.__version__, "module": nodus.__file__}))'],
                cwd=suite, env=env, text=True)
            report.update(json.loads(metadata))
            Path(report['module']).resolve().relative_to(environment.resolve())
            junit = work / 'results.xml'
            tested = subprocess.run([str(python), '-m', 'pytest', '-q', 'tests',
                                     f'--junitxml={junit}'], cwd=suite, env=env)
            if junit.exists():
                suites = ET.parse(junit).getroot().findall('testsuite')
                report['counts'] = {name: sum(int(item.get(name, '0')) for item in suites)
                                    for name in ('tests', 'failures', 'errors', 'skipped')}
            result = tested.returncode
            report['status'] = 'passed' if result == 0 else 'failed'
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        report['error'] = str(exc)
    finally:
        rendered = json.dumps(report, indent=2)
        print(rendered)
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(rendered + '\n', encoding='utf-8')
    return result


if __name__ == '__main__':
    raise SystemExit(main())
