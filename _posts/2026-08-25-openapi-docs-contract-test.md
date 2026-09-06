---
layout: post
title: "Keeping Documentation Honest with an OpenAPI Snapshot Diff"
subtitle: "Diffing a live OpenAPI schema against a committed snapshot in CI."
date: 2026-08-25 09:00:00 +0200
tags: [documentation, testing, ci-cd]
description: >-
  Hand-written API documentation and the code behind it drift apart
  silently, because nothing runs the docs to notice. This article generates
  the real OpenAPI schema from a FastAPI app, commits a normalised
  snapshot, and fails CI the moment the two disagree, with the full app,
  test and workflow.
---

## The problem

API documentation is usually prose, written by hand, describing endpoints, fields and
types as they were at the time someone wrote the page. The code behind it keeps moving: a
field gets renamed, an endpoint is dropped in a refactor, a new required parameter is
added. None of that fails a build, because the documentation is not code the build runs —
it's text that looks fine sitting next to code that also looks fine, and the two only turn
out to disagree when a reader tries the documented request against the real API and gets a
response that doesn't match what the page said to expect.

This is easy to miss for exactly that reason: there is no moment where the mismatch
announces itself. A renamed field, a deleted route, a parameter that quietly became
required — each one is a normal, reviewed code change with no obligation attached to update
a separate markdown file three directories away, and no check that would fail if nobody
did.

## Working through it

### Get the schema from the code, not from memory

A framework like FastAPI can already produce the real OpenAPI schema on request, generated
directly from the same route definitions and Pydantic models the server runs on. That
schema is ground truth in a way a hand-maintained description never can be — it cannot
drift from the code, because it is derived from it.

### Commit a snapshot, and compare against it in CI

Rather than comparing "the docs" to "the code" in the abstract, compare the previously
committed schema to today's generated one. Fetch the live schema during the test run,
normalise it, and diff it against a snapshot file checked into the repository. Any
difference the current change didn't deliberately produce fails the build.

### Make the diff meaningful, not noisy

A raw JSON diff over an OpenAPI document is noisy — key ordering and internal `$ref`
churn can differ without anything about the API actually changing. Sorting keys and
stripping fields that vary for reasons unrelated to the API's shape, such as the schema's
own version string, keeps the comparison focused on what a caller would actually notice.

### Require regenerating the snapshot as a conscious step

If a mismatch caused the snapshot to update itself automatically, the check would stop
catching anything — every drift would simply become the new baseline. Failing the build
and requiring a person to run a regeneration script, then review and commit the result,
keeps a human decision in the loop at the one point where it matters.

## The solution

```python
# app.py
from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="Widget API", version="1.0.0")


class Widget(BaseModel):
    id: int
    name: str
    weight_grams: int


_WIDGETS = {1: Widget(id=1, name="bolt", weight_grams=12)}


@app.get("/widgets/{widget_id}", response_model=Widget)
def get_widget(widget_id: int) -> Widget:
    return _WIDGETS[widget_id]


@app.post("/widgets", response_model=Widget)
def create_widget(widget: Widget) -> Widget:
    _WIDGETS[widget.id] = widget
    return widget
```

```python
# scripts/dump_schema.py
"""Regenerate the committed OpenAPI snapshot from the running code.

Run this deliberately after a real API change, review the diff it
produces in the committed file, and commit both together.
"""
import json
from pathlib import Path

from app import app

SNAPSHOT_PATH = Path(__file__).parent.parent / "openapi.snapshot.json"


def normalize(schema: dict) -> dict:
    """Strip fields that change without the API's shape changing."""
    schema = dict(schema)
    schema.pop("info", None)  # a version bump alone shouldn't fail the diff
    return schema


def main() -> None:
    schema = normalize(app.openapi())
    SNAPSHOT_PATH.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n")
    print(f"wrote {SNAPSHOT_PATH}")


if __name__ == "__main__":
    main()
```

```python
# tests/test_openapi_contract.py
import json
from pathlib import Path

from app import app
from scripts.dump_schema import normalize

SNAPSHOT_PATH = Path(__file__).parent.parent / "openapi.snapshot.json"


def test_the_live_schema_matches_the_committed_snapshot():
    committed = json.loads(SNAPSHOT_PATH.read_text())
    live = normalize(app.openapi())

    assert live == committed, (
        "the API's live schema no longer matches openapi.snapshot.json.\n"
        "If this change is intentional, run:\n"
        "    python scripts/dump_schema.py\n"
        "review the diff, and commit the updated snapshot with your change."
    )
```

```yaml
# .github/workflows/openapi-contract.yml
name: openapi-contract
on: [pull_request]

jobs:
  contract:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install fastapi==0.115.0 pydantic==2.9.2 pytest==8.3.3
      - run: pytest tests/test_openapi_contract.py -q
```

Set up the initial snapshot and confirm the test passes against it:

```bash
pip install fastapi==0.115.0 pydantic==2.9.2 pytest==8.3.3

python scripts/dump_schema.py
```

```
wrote /path/to/project/openapi.snapshot.json
```

```bash
pytest tests/test_openapi_contract.py -q
```

```
1 passed in 0.18s
```

Now simulate the kind of drift that would otherwise pass unnoticed — rename `weight_grams`
to `weight` in `app.py` — and run the same test again:

```bash
pytest tests/test_openapi_contract.py -q
```

```
FAILED tests/test_openapi_contract.py::test_the_live_schema_matches_the_committed_snapshot
AssertionError: the API's live schema no longer matches openapi.snapshot.json.
If this change is intentional, run:
    python scripts/dump_schema.py
review the diff, and commit the updated snapshot with your change.
```

## Conclusion

Documentation drifts because nothing ever runs it; giving the schema an executable form
turns that drift into a failing test instead of a discovery made by whoever tries the
documented request next.

Diff the thing the framework already generates from the route definitions themselves,
rather than maintaining a second, parallel description of the same API by hand.

Letting the check silently regenerate the snapshot on a mismatch defeats its purpose — the
failure has to reach a person, and the regeneration has to be a command they run and a diff
they read, not something that happens for them.

This only proves the schema's shape hasn't drifted; a route can be schema-correct and still
sit next to prose documentation that describes it wrong in some other way. It catches
structural drift, not narrative drift — a smaller class of error than "the docs are wrong,"
but one that costs nothing extra to catch once the schema exists at all.
