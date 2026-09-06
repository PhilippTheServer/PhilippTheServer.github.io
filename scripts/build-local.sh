#!/usr/bin/env bash
# Build and verify in a container, since there is no local Ruby.
# The gem bundle lives in a named volume so repeated runs do not reinstall it.
set -euo pipefail
cd "$(dirname "$0")/.."
docker volume create ptsbundle >/dev/null
exec docker run --rm \
  -v "$PWD":/w -w /w \
  -v ptsbundle:/bundle \
  -u "$(id -u):$(id -g)" \
  -e HOME=/tmp -e BUNDLE_PATH=/bundle \
  ruby:3.3 \
  sh -c 'bundle install --quiet && '"${1:-./scripts/verify.sh}"
