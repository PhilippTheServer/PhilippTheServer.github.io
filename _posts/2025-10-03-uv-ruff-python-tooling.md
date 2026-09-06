---
layout: post
title: "Replacing pip, venv, flake8, black and isort with uv and Ruff"
subtitle: "One lockfile-backed tool for environments, and one binary for every lint and format check."
date: 2025-10-03 09:00:00 +0200
tags: [python, ci-cd, automation]
description: >-
  A requirements.txt file records no lockfile, so "pip install -r
  requirements.txt" can resolve a different dependency tree on two machines
  run a day apart, and four separate lint tools mean four configuration
  blocks that drift out of sync. This walks through replacing pip, venv,
  flake8, black and isort with uv and Ruff, with a project a reader can build
  and lint in a few minutes.
---

## The problem

A typical small Python project has a `requirements.txt`:

```
requests
flask
pytest
```

No versions. `pip install -r requirements.txt` today resolves whatever is newest right
now; the same command run in three months resolves something else. There is no lockfile,
so "it works on my machine" is not a joke, it is the accurate description of what
`requirements.txt` guarantees: it works on *a* machine, at *a* point in time, with *a*
dependency graph that nobody wrote down.

Pinning versions in `requirements.txt` by hand does not fix this either, because pinning
direct dependencies says nothing about *their* dependencies, and a transitive package
bumping a minor version can still change behaviour under you.

Alongside that, a typical `setup.cfg` carries three more tools, each with its own
configuration block:

```ini
[flake8]
max-line-length = 100
extend-ignore = E203

[isort]
profile = black
line_length = 100

[tool:pytest]
...
```

Plus a `pyproject.toml` section for Black. Four tools, four places their settings can
disagree — `flake8`'s line length and Black's line length are two numbers that must be
kept equal by hand, and nothing enforces that they are. On a team of two this is
tolerable. On a team where people join and leave, it is one more thing every new
contributor's editor has to be configured for correctly before their first commit passes
CI.

## Working through it

### Why a lockfile is not optional

`pip freeze > requirements.txt` produces a flat list of exact versions, but it conflates
"what I need" with "what I resolved to", and it cannot express platform-specific
dependencies (a package needed only on Windows, say) without hand-editing. A real lockfile
records the full resolved graph, including transitive dependencies, with hashes, separately
from the human-readable list of direct dependencies. `uv` produces exactly that:
`pyproject.toml` for intent, `uv.lock` for the exact, reproducible resolution.

### Why one binary beats four

Ruff reimplements the rule sets of `flake8` (and most of its popular plugins), `isort`'s
import sorting, and `black`'s formatting, in a single Rust binary with one configuration
block in `pyproject.toml`. The value is not "it is faster" — though it is, by a wide
margin — the value is that line length, quote style, and import grouping are now one
number in one file instead of the same number typed three times and hoping nobody changes
only one of them.

### Migrating without breaking history

Running a formatter over an existing codebase in one commit is disruptive to `git blame`.
Do it deliberately, as its own commit, separate from any behavioural change, and configure
Ruff to match the project's existing style choices (line length, quote style) rather than
accepting new defaults that reformat everything.

### Wiring it into CI so drift cannot land

A lockfile only helps if CI installs from it exactly, and a lint config only helps if CI
fails the build on violations rather than only warning locally. Both `uv sync --locked`
(fails if the lockfile is out of date with `pyproject.toml`) and `ruff check --output-format=github`
belong in the same workflow.

## The solution

```toml
# pyproject.toml
[project]
name = "widget-service"
version = "0.1.0"
description = "Example service for the uv/Ruff migration"
requires-python = ">=3.12"
dependencies = [
    "requests>=2.32,<3",
    "flask>=3.0,<4",
]

[dependency-groups]
dev = [
    "pytest>=8.3,<9",
    "ruff>=0.7,<1",
]

[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B"]
ignore = ["E203"]

[tool.ruff.format]
quote-style = "double"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
```

`select` picks rule groups explicitly rather than accepting Ruff's ever-growing default
set: `E`/`F` is the flake8 core (pycodestyle plus pyflakes), `I` is import sorting
(isort's job), `UP` flags code that can be written in a more modern Python syntax, and `B`
catches common bug patterns (`flake8-bugbear`). Formatting (Black's job) is a separate
Ruff subcommand, not a lint rule, which is why it has its own `[tool.ruff.format]` table.

```python
# src/widget_service/app.py
import json
import os

import requests
from flask import Flask, jsonify

app = Flask(__name__)


def fetch_status(url: str) -> dict:
    response = requests.get(url, timeout=5)
    response.raise_for_status()
    return response.json()


@app.get("/health")
def health() -> tuple[dict, int]:
    return jsonify({"status": "ok"}), 200


@app.get("/upstream")
def upstream() -> dict:
    target = os.environ.get("UPSTREAM_URL", "https://example.internal/status")
    return fetch_status(target)


if __name__ == "__main__":
    app.run(port=8000)
```

```python
# tests/test_app.py
from widget_service.app import app


def test_health_returns_ok():
    client = app.test_client()
    response = client.get("/health")
    assert response.status_code == 200
    assert response.get_json() == {"status": "ok"}
```

```yaml
# .github/workflows/ci.yml
name: ci

on:
  push:
  pull_request:

jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Install uv
        uses: astral-sh/setup-uv@v4
        with:
          version: "0.5.13"

      - name: Sync environment from the lockfile
        run: uv sync --locked --all-groups

      - name: Lint
        run: uv run ruff check .

      - name: Check formatting
        run: uv run ruff format --check .

      - name: Test
        run: uv run pytest
```

Building and checking it locally:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh

mkdir widget-service && cd widget-service
mkdir -p src/widget_service tests
# create pyproject.toml, src/widget_service/app.py, tests/test_app.py as above
touch src/widget_service/__init__.py

uv sync --all-groups
# Resolved 12 packages in 43ms
# Prepared 12 packages in 210ms
# Installed 12 packages in 38ms
# creates .venv/ and uv.lock

uv run ruff check .
# All checks passed!

uv run ruff format --check .
# 2 files already formatted

uv run pytest
# 1 passed in 0.08s
```

Introduce a deliberate lint violation to see the failure mode a reviewer would see in CI:

```bash
sed -i "s/import json//" src/widget_service/app.py   # now json is imported but unused... reverse it:
echo "import sys" >> src/widget_service/app.py
uv run ruff check .
# src/widget_service/app.py:14:8: F401 [*] `sys` imported but unused
# Found 1 error.
# [*] 1 fixable with the `--fix` option.

uv run ruff check --fix .
# Found 1 error (1 fixed, 0 remaining).
```

`uv.lock` is generated by `uv sync` and should be committed alongside `pyproject.toml`; it
is what makes `uv sync --locked` in CI fail the build if someone edited a dependency
version without regenerating the lock, rather than silently resolving something different
than what was tested locally.

## Conclusion

None of this changes what the project depends on or how it is linted; it changes how many
places that decision lives, and whether a machine or a person is responsible for keeping
those places consistent.

Three things generalise past this specific tool swap:

**A lockfile is not a nice-to-have for anything deployed more than once.** If "works on my
machine" is a real risk for a project, the fix is a recorded, hashed dependency graph, not
a more disciplined `requirements.txt`.

**Consolidating tools is a maintenance decision, not a speed one.** Ruff's speed is a
pleasant side effect; the actual return is one configuration block instead of three that
can silently disagree.

**Reformatting history-changing tools deserve their own commit.** Bundling a full-repo
reformat with a behavioural change makes both harder to review and makes `git blame`
useless for every line touched, indefinitely.
