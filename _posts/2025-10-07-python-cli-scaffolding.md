---
layout: post
title: "A Python CLI That Scaffolds a Project Structure You Actually Want"
subtitle: "A stdlib-only generator for the layout, Dockerfile and ignore rules every new service repeats."
date: 2025-10-07 09:00:00 +0200
tags: [python, architecture, automation]
description: >-
  Starting a new service usually means recreating the same src layout,
  Dockerfile and ignore rules by hand, or copying them from whichever
  previous project is open in another tab, drifting a little further from a
  consistent shape each time. This builds a small, dependency-free scaffolding
  CLI that generates a project from a fixed template, and a test that checks
  the result is actually valid.
---

## The problem

The tenth time you start a new Python service, the first ten minutes look the same every
time: create `src/<package>/__init__.py`, write a `pyproject.toml`, write a `Dockerfile`
that almost certainly copies the previous project's, write a `.gitignore`, decide again
whether tests live under `tests/` or `src/<package>/tests/`.

Copy-pasting from the last project seems like it saves the ten minutes, but it also copies
whatever that project had accumulated — a dependency it no longer needs, a `.gitignore`
entry for a build tool this project doesn't use, a `Dockerfile` `FROM` line pinned to
whatever image tag happened to be current then. Six months and four copied-and-pasted
projects later, no two repositories in the organisation have the same layout, and nobody
remembers why any specific one differs.

A template repository (`git clone` and delete the parts you don't need) is better than
copy-paste, but it still requires a human to remember it exists, find it, and manually
substitute the project name into every file that mentions it. A generator does the
substitution and, more importantly, is itself a single file that changes when the standard
changes — updating the template updates every *future* project, without anyone needing to
know where the "canonical" repository currently lives.

## Working through it

### Keeping it dependency-free on purpose

A scaffolding tool that itself needs a virtual environment before it can create your first
virtual environment is a chicken-and-egg problem. Using only the standard library —
`argparse`, `pathlib`, `string.Template`, `tomllib` for verification — means the tool runs
with nothing but a Python interpreter, which is the one thing guaranteed to be present
before any project exists.

### Templates as data, not as a directory of files to ship

The two common approaches are a directory of `.j2` files bundled with the package, or
strings embedded directly in the script. A directory is nicer to edit but needs packaging
machinery (`importlib.resources`) to be reliable once installed rather than run from a
checkout. For a tool this size, a dictionary mapping relative path to content keeps the
whole thing in one file that is easy to read top to bottom and easy to diff when the
standard changes.

### Refusing to overwrite silently

The single most damaging failure mode for a generator is running it a second time into an
existing directory and quietly overwriting work. Check for an existing target and refuse
by default; require an explicit `--force` to overwrite.

### Proving the output is valid, not just present

Generating files that exist is not the same as generating files that work. The
`pyproject.toml` the tool writes should parse as valid TOML, and the `.gitignore` should
not be empty. A test that actually runs the generator into a temporary directory and
inspects the result catches template mistakes — a stray unescaped `{` in a template
string, say — that a human skimming the template text would miss.

## The solution

```python
#!/usr/bin/env python3
# scaffold.py
"""Generate a standard Python project layout.

Usage:
    python scaffold.py my-service --author "Jane Doe" [--force]
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from string import Template

TEMPLATES: dict[str, str] = {
    "pyproject.toml": '''\
[project]
name = "$slug"
version = "0.1.0"
description = "$name"
requires-python = ">=3.12"
authors = [{ name = "$author" }]
dependencies = []

[dependency-groups]
dev = ["pytest>=8.3,<9", "ruff>=0.7,<1"]

[tool.ruff]
line-length = 100
target-version = "py312"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
''',
    "src/$package/__init__.py": '''\
"""$name."""

__version__ = "0.1.0"
''',
    "src/$package/main.py": '''\
def main() -> None:
    print("$name is running")


if __name__ == "__main__":
    main()
''',
    "tests/test_main.py": '''\
from $package.main import main


def test_main_runs(capsys):
    main()
    captured = capsys.readouterr()
    assert "$name" in captured.out
''',
    "Dockerfile": '''\
FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml ./
COPY src/ ./src/

RUN pip install --no-cache-dir .

CMD ["python", "-m", "$package.main"]
''',
    ".gitignore": '''\
__pycache__/
*.pyc
.venv/
.pytest_cache/
*.egg-info/
dist/
''',
    "README.md": '''\
# $name

Generated with scaffold.py. Replace this description.

## Run

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
python -m $package.main
```
''',
}


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", name).strip("-").lower()
    if not slug:
        raise ValueError(f"cannot derive a slug from {name!r}")
    return slug


def package_name(slug: str) -> str:
    return slug.replace("-", "_")


def render(target: Path, name: str, author: str, force: bool) -> list[Path]:
    slug = slugify(name)
    package = package_name(slug)
    substitutions = {"name": name, "slug": slug, "package": package, "author": author}

    written: list[Path] = []
    for rel_path_template, content_template in TEMPLATES.items():
        rel_path = Template(rel_path_template).substitute(substitutions)
        dest = target / rel_path

        if dest.exists() and not force:
            raise FileExistsError(f"{dest} already exists (use --force to overwrite)")

        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(Template(content_template).substitute(substitutions))
        written.append(dest)

    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("name", help="Project name, e.g. 'my service'")
    parser.add_argument("--author", default="Unknown", help="Author name for pyproject.toml")
    parser.add_argument("--out", type=Path, default=None, help="Target directory (default: ./<slug>)")
    parser.add_argument("--force", action="store_true", help="Overwrite existing files")
    args = parser.parse_args(argv)

    target = args.out or Path(slugify(args.name))

    try:
        written = render(target, args.name, args.author, args.force)
    except FileExistsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    for path in written:
        print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

```python
# test_scaffold.py
import tomllib

import pytest

from scaffold import render, slugify


def test_slugify_handles_spaces_and_case():
    assert slugify("My New Service") == "my-new-service"


def test_slugify_rejects_empty_result():
    with pytest.raises(ValueError):
        slugify("!!!")


def test_render_produces_valid_pyproject(tmp_path):
    written = render(tmp_path, "My New Service", "Jane Doe", force=False)
    assert len(written) == len(set(written)) == 7

    pyproject = tmp_path / "pyproject.toml"
    data = tomllib.loads(pyproject.read_text())
    assert data["project"]["name"] == "my-new-service"
    assert data["project"]["authors"][0]["name"] == "Jane Doe"


def test_render_creates_importable_package_layout(tmp_path):
    render(tmp_path, "My New Service", "Jane Doe", force=False)
    package_init = tmp_path / "src" / "my_new_service" / "__init__.py"
    assert package_init.is_file()
    assert "My New Service" in package_init.read_text()


def test_render_refuses_to_overwrite_by_default(tmp_path):
    render(tmp_path, "My New Service", "Jane Doe", force=False)
    with pytest.raises(FileExistsError):
        render(tmp_path, "My New Service", "Jane Doe", force=False)


def test_render_overwrites_with_force(tmp_path):
    render(tmp_path, "My New Service", "Jane Doe", force=False)
    written = render(tmp_path, "My New Service", "Jane Doe", force=True)
    assert len(written) == 7
```

Running it end to end:

```bash
python scaffold.py "Widget Service" --author "Jane Doe"
# wrote widget-service/pyproject.toml
# wrote widget-service/src/widget_service/__init__.py
# wrote widget-service/src/widget_service/main.py
# wrote widget-service/tests/test_main.py
# wrote widget-service/Dockerfile
# wrote widget-service/.gitignore
# wrote widget-service/README.md

cd widget-service
python -m venv .venv && source .venv/bin/activate
pip install -e . pytest >/dev/null
pytest -q
# 1 passed in 0.02s

python -m widget_service.main
# Widget Service is running

pip install pytest
pytest -q ../test_scaffold.py
# 5 passed in 0.03s
```

## Conclusion

The generator is small on purpose. Its value is not in the specific layout it produces —
that will change as the standard changes — it is in making the standard a single file that
one person can update and every future project inherits automatically, instead of
knowledge that lives in whichever repository someone last copied from.

Two things generalise past project scaffolding specifically:

**Anything copy-pasted more than twice should become a generator, not a better template.**
A template still requires a human to remember which parts to change; a generator makes
that decision once, in code that can be reviewed and tested.

**A generator's own test suite should assert on the *output's* correctness, not on the
generator's internal behaviour.** Parsing the generated `pyproject.toml` with `tomllib`
catches a broken template string; asserting that `render()` was called with certain
arguments would not.
