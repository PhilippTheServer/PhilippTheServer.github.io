#!/usr/bin/env bash
# Build the site and check that it still holds together.
# This is what CI runs on every push and pull request.
set -euo pipefail
cd "$(dirname "$0")/.."

if [ "${SKIP_BUILD:-0}" != "1" ]; then
  echo "==> jekyll build"
  bundle exec jekyll build --trace
fi

echo "==> html-proofer (internal links, images, markup)"
bundle exec htmlproofer _site \
  --disable-external \
  --allow-hash-href \
  --ignore-empty-alt \
  --no-enforce-https

echo "==> structural checks"
ruby scripts/check_site.rb _site

echo "==> all verification passed"
