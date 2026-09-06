# Nodus SDK contributor rules

## Documentation style

Semicolons and em dashes are prohibited in documentation. This is a mandatory
rule for prose, headings, tables, API descriptions, and copyable examples.
Rewrite sentences and use multiline example commands. Do not hide prohibited
punctuation in HTML entities or escaped Unicode.

Run `python scripts/check-docs-style.py` before submitting documentation changes.
The check covers root Markdown, docs, wiki, examples, OpenAPI, design documents,
and rendered documentation template text. HTML markup and scripts must remain
valid, and their syntax is not customer-visible prose.

Keep internal architecture, API design deliberations, supplier details, and
planning in the private Nodus repository. The public SDK contains the package,
tests, customer documentation, examples, and essential contributor instructions.
