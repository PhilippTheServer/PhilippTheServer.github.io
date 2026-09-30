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

# The Medium import reminder links each new article by the URL post-urls.sh derives
# (issue #53). Every URL it produces must be a page this build actually has.
echo "==> post URLs resolve to built pages"
scripts/post-urls.sh _posts/*.md | while read -r url; do
  page="_site${url#https://philipptheserver.com}index.html"
  [ -f "$page" ] || { echo "post-urls.sh: $url has no page at $page" >&2; exit 1; }
done

# The pixel crew script's behaviour (issue #43). The ruby:3.3 image build-local.sh
# uses has no Node, so build-local.sh runs this on the host and sets SKIP_NODE_TESTS=1.
if [ "${SKIP_NODE_TESTS:-0}" = "1" ]; then
  echo "==> node tests: already run by build-local.sh"
else
  echo "==> node tests (pixel crew)"
  command -v node >/dev/null || { echo "node is required for the pixel crew tests" >&2; exit 1; }
  node --test scripts/pixel-crew.test.mjs
fi

echo "==> all verification passed"
