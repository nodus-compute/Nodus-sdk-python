---
name: writing-timeless-comments
description: Use when writing or editing any code comment or docstring — especially after fixing a bug, removing code, or changing behavior, when deciding what note to leave behind — and when reviewing a diff's comments before commit.
---

# Writing Timeless Comments

## Overview

A comment documents the code that exists, not the story of how it got that way. Core principle: **every comment must read true and complete against today's tree alone** — a reader with no git history and no memory of the old code understands it fully. The moment a comment needs a "before" to make sense, the content is real but it is in the wrong place.

## Where history goes instead

The story of a change — the old behavior, the bug, the measurement, what was removed — is valuable and gets kept, in the artifacts built for history: the **commit message** (always), a **postmortem** (an incident with a lesson), a **design doc** (a decision among alternatives). Moving it there is the whole discipline; deleting it is not required and hiding it in a comment is not allowed.

## The recipe

A comment states, in present tense, the invariant or constraint the code alone cannot show: why this bound, what this branch guarantees, what breaks if you change it. After fixing an infinite retry of HTTP 400:

```python
# 4xx is permanent: the request itself is invalid, so retrying it
# unchanged can never succeed.
```

…and the clause that wants to follow it — "(Previously this was classified as transient, which retried bad requests forever; ~14 min burned per request)" — goes in the commit message, measurement included, verbatim.

## Concise — one to two lines

A comment is 1–2 lines, and two is the limit, not the target — when a draft reaches a third line, cut a clause, drop what the code already shows, or split the fact across the two sites it describes. Compress by naming the invariant, not narrating it:

```go
// Retry once on stale: a concurrent settlement can invalidate the snapshot
// between the locks; re-read it, never reuse it.
```

Doc-level comments (a docstring, a package/type doc) may take a couple more lines. A comment reaching 5 lines is a design note in the wrong place — move the paragraph to the design doc or the type's doc comment and leave the 1–2 line rule at the site.

## Self-test before keeping any comment

Read it as someone who can see only today's tree:
1. Does every thing it names exist in this tree?
2. Is there a before/after contrast anywhere in it?
3. Would it still be fully true if the git history vanished?
4. Is it within 2 lines?

Any failure → rewrite as the present-tense invariant, compress, or delete.

## Red flags — the changelog tells

"previously", "used to", "was", "no longer", "now" (contrasting with a past), "removed", "renamed", "fixed the bug where", "after the refactor", "caught by …", a date tied to an incident in this codebase. A third line on a code comment. A tombstone at a deletion site ("X used to be called here") is the same defect — an absence needs no guard. When one of these appears in a draft comment, that sentence is the part that belongs in the commit message.

## What this rule does NOT ban

- Dated citations of **external** facts — a vendor's price list, an RFC, a measured hardware limit. Those read true forever.
- Commit messages that tell the full story with numbers. That is exactly where it belongs — a terse commit message plus a changelog comment is the combination this rule exists to prevent.
