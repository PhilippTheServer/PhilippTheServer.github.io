#!/usr/bin/env bash
# Serve the site locally on http://localhost:4000 with live reload.
# Runs Jekyll in Docker so no local Ruby installation is needed.
set -euo pipefail
cd "$(dirname "$0")/.."

exec docker run --rm -it \
  -v "$PWD":/srv/jekyll \
  -p 4000:4000 \
  jekyll/jekyll:4 \
  sh -c "bundle install && bundle exec jekyll serve --host 0.0.0.0 --livereload --force_polling"
