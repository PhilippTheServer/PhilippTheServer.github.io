#!/usr/bin/env bash
# Build and verify in a container, since there is no local Ruby.
# The gem bundle lives in a named volume so repeated runs do not reinstall it.
# The ruby:3.3 image has no Node, so the pixel crew tests run here on the host first,
# and the Medium tooling checks run on the host after the build.
set -euo pipefail
cd "$(dirname "$0")/.."
node --test scripts/pixel-crew.test.mjs
docker volume create ptsbundle >/dev/null
docker run --rm \
  -v "$PWD":/w -w /w \
  -v ptsbundle:/bundle \
  -u "$(id -u):$(id -g)" \
  -e HOME=/tmp -e BUNDLE_PATH=/bundle -e SKIP_NODE_TESTS=1 \
  ruby:3.3 \
  sh -c 'bundle install --quiet && '"${1:-./scripts/verify.sh}"

# The Medium tooling needs uv, which the ruby:3.3 image lacks, and the built site (#68).
if [ $# -eq 0 ]; then scripts/medium/check.sh; fi
