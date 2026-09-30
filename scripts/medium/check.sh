#!/usr/bin/env bash
# Lint and test the Medium tooling (#68). CI runs this after the site build, so the test
# that models every built article has pages to read.
set -euo pipefail
cd "$(dirname "$0")"
export PYTHONDONTWRITEBYTECODE=1
uvx 'ruff>=0.16.9' check .
uvx 'ruff>=0.16.9' format --check .
uv run --no-project --with 'pytest>=9.1.1' --with 'beautifulsoup4>=4.15' pytest -q -p no:cacheprovider
