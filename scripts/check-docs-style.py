"""Reject semicolons and em dashes in maintained documentation and examples."""
from __future__ import annotations

import html
from html.parser import HTMLParser
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN = {chr(59): 'semicolon', chr(0x2014): 'em dash'}


class VisibleText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.hidden = 0
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in {'script', 'style'}:
            self.hidden += 1

    def handle_endtag(self, tag):
        if tag in {'script', 'style'}:
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def document_paths(root):
    paths = set(root.glob('*.md'))
    for folder in ('docs', 'wiki', 'examples', 'openapi', 'design'):
        paths.update((root / folder).rglob('*.md'))
    paths.update((root / 'examples').rglob('*.py'))
    for path in ('openapi/openapi.yaml', 'design/openapi.yaml'):
        if (root / path).exists():
            paths.add(root / path)
    for folder in ('apps/landing/src/docs', 'apps/landing/src/_includes/docs'):
        paths.update((root / folder).rglob('*.njk'))
    return sorted(paths)


def strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from strings(item)


def violations(path):
    text = path.read_text(encoding='utf-8')
    if path.suffix == '.yaml':
        # The public OpenAPI snapshot uses JSON syntax, including escaped Unicode.
        text = '\n'.join(strings(json.loads(text)))
    elif path.suffix == '.njk':
        parser = VisibleText()
        parser.feed(text)
        text = '\n'.join(parser.parts)
    else:
        text = html.unescape(text)
    return [(number, label) for number, line in enumerate(text.splitlines(), 1)
            for char, label in FORBIDDEN.items() if char in line]


def main(root=ROOT):
    paths = document_paths(root)
    failures = [(path.relative_to(root), line, label)
                for path in paths for line, label in violations(path)]
    for path, line, label in failures:
        print(f'{path}: text line {line}: forbidden {label}')
    if failures:
        return 1
    print(f'Documentation style passed across {len(paths)} files.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
