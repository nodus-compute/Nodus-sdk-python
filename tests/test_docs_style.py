"""Enforce the documentation punctuation rule in source checkouts."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / 'scripts/check-docs-style.py'
if CHECKER.exists():
    spec = importlib.util.spec_from_file_location('docs_style', CHECKER)
    style = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(style)


@unittest.skipUnless(CHECKER.exists(), 'style checks require a source checkout')
class DocumentationStyleTests(unittest.TestCase):
    def test_repository_documentation(self):
        self.assertEqual(style.main(), 0)

    def test_rejects_prose_and_code_examples(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'guide.md'
            for content in ('First' + chr(59) + ' second',
                            'First ' + chr(0x2014) + ' second',
                            '```python\nprint(1)' + chr(59) + ' print(2)\n```',
                            'First &mdash' + chr(59) + ' second'):
                path.write_text(content, encoding='utf-8')
                self.assertTrue(style.violations(path), content)

    def test_rejects_escaped_openapi_descriptions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'openapi.yaml'
            path.write_text(json.dumps({'description': 'First ' + chr(0x2014) + ' second'}), encoding='utf-8')
            self.assertTrue(style.violations(path))

    def test_checks_rendered_html_without_breaking_script_syntax(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'index.njk'
            path.write_text('<script>const count = 1' + chr(59) + '</script><p>A &ge' + chr(59) + ' B</p>', encoding='utf-8')
            self.assertFalse(style.violations(path))
            path.write_text('<p>A &mdash' + chr(59) + ' B</p>', encoding='utf-8')
            self.assertTrue(style.violations(path))

    def test_checks_python_documentation_without_changing_runtime_strings(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'client.py'
            punctuation = chr(59)
            path.write_text('value = "wire' + punctuation + 'value"', encoding='utf-8')
            self.assertFalse(style.violations(path))
            path.write_text('def run():\n    """First' + punctuation + ' second"""\n    pass\n', encoding='utf-8')
            self.assertTrue(style.violations(path))
            path.write_text('parser.add_argument("--flag", help="First' + punctuation + ' second")', encoding='utf-8')
            self.assertTrue(style.violations(path))

    def test_discovers_new_nested_guides(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / 'docs/new-topic/guide.md'
            path.parent.mkdir(parents=True)
            path.write_text('Clear documentation.', encoding='utf-8')
            self.assertIn(path, style.document_paths(root))


if __name__ == '__main__':
    unittest.main()
