#!/usr/bin/env bash
# Print the public URL of each post file given as an argument, one per line.
# Mirrors `permalink: /posts/:title/` in _config.yml: the filename minus its date prefix.
set -euo pipefail
for f in "$@"; do
  name=$(basename "$f" .md)
  echo "https://philipptheserver.com/posts/${name:11}/"
done
