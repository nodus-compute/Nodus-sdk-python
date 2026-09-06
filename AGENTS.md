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

## Supported customer workflow

Customer documentation currently targets GPU workloads. Do not present VM or
CPU-only execution as a supported offering merely because a schema or adapter
accepts it. Keep existing wire values compatible.

Lead with authentication, submitting the customer command, observing progress,
and retrieving results. Keep scheduling and recovery internals out of the
quickstart. Never claim transparent application-state restoration without a
verified integration. Use consistent hyphenated workload labels such as
`LoRA-fine-tune` in examples. These labels are descriptions, not API enums.
