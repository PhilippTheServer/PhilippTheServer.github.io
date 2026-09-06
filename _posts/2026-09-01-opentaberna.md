---
layout: post
title: "Documentation as a Repository: Publishing a Wiki.js Site from Reviewed Markdown"
subtitle: "Docs that are not under version control are docs that will be wrong; the only question is when."
date: 2026-09-01 09:00:00 +0200
tags: [documentation, testing, ci-cd, architecture]
description: >-
  Documentation kept in a hosted wiki drifts silently, because nothing forces
  a reviewer to look at it when the code it describes changes. Treating docs
  as a repository, reviewed through the same pull requests as code, turns
  that silence into a diff someone has to look at. This covers what changes
  when documentation moves into git — as it does in the open-source OpenTaberna
  project — with a runnable Wiki.js setup and a CI check that fails a pull
  request when source changes without its docs.
---

## The problem

[OpenTaberna](https://github.com/OpenTaberna) is an open-source hospitality system — a
FastAPI backend, two TypeScript frontends, and a wiki. The wiki is the part worth writing
about, because of where it lives: [its own repository](https://github.com/OpenTaberna/wiki)
of Markdown files, published to a Wiki.js site, and moved through pull requests like
everything else.

That is not how most projects do it, and the difference is not cosmetic.

A page in a hosted wiki drifts silently. There is no diff, no blame, no review, and no
moment at which anyone is forced to look at the change that made it stale. The code moves
on, the page stays exactly as it was written, and both look fine independently — the code
because it works, the page because nothing marks it as wrong. The gap between them is
invisible until someone follows the page and it lies to them.

The instinct is to fix this with a rule: "update the docs when you change the behaviour."
That rule fails everywhere it is tried for the same reason — it lives in a policy document
nobody reads at the moment they are making the change, and there is nothing in the tooling
that would stop a pull request from merging without it. A rule enforced by memory is a rule
that erodes the first time someone is in a hurry, and someone is always in a hurry.

Working on something public raises the stakes on this in a specific way: there is no
colleague to ask, no shared context, no "obviously it needs the database running first."
Everything a person needs has to be written down, and the fastest way to discover whether
it actually is written down is that someone tries and fails. That is uncomfortable, and it
is the most useful review a project gets — every internal project I have worked on had
gaps that were invisible precisely because everyone had already been told the missing thing
in conversation. A public project has no conversations to lean on.

## Working through it

### Put the documentation under the same rules as the code

If the docs live in a repository, as Markdown, moved by pull requests, three things follow
automatically. **They can be wrong in a way that shows** — a stale page is a diff, with a
blame and an author, reviewed by the same person reviewing the code that made it stale.
**They can be required** — "the docs are updated in the same change" becomes a rule a
reviewer can actually enforce, because the change is sitting right there in front of them,
rather than a rule that depends on someone separately remembering to check a different
system. **They can be run locally** — the same container that serves the published site can
serve the working copy, so a page looks locally exactly as it will look once published,
which is the difference between people previewing their writing and people guessing.

### Decide the shape once, not per page

An API that returns a bare object here and a wrapped one there, or an error as a string in
one place and a structured object in another, is an API where every client writes its own
special case, and the special cases are where bugs live — invisible until a client hits the
one endpoint shaped differently. The same argument applies to documentation structure.
Deciding the envelope once — what every page in a given category has to cover, in what
order — and holding every page to it is unglamorous, and it is most of what makes a set of
docs pleasant to use. It also makes the documentation shorter, because the shape is
described once instead of re-derived per page.

The pages that resist this the most are the ones describing something that changes
independently of the documentation itself — a database schema, a configuration surface. A
page that promises to describe "the schema as it is actually built" is making an explicit
promise to be revisited when the schema changes, and writing that promise into the title is
what makes the obligation visible rather than assumed.

### Enforce it, don't just ask for it

A rule a reviewer has to remember is a rule that gets missed under deadline pressure,
exactly the conditions in which documentation debt actually accumulates. The reliable
version of "the docs are updated in the same change" is a check that runs on every pull
request and fails the build when source changes without a corresponding docs change. It
does not need to be clever — a rule that flags every source change for the reviewer to
confirm is documentation-relevant or not is enough, because the point is not to guess
correctly every time, it is to make silence impossible.

## The solution

### Publishing the site

Wiki.js is a reasonable choice for the published side, because it is ordinary software you
run yourself and it stores content that maps directly onto Markdown files:

```yaml
# docker-compose.yml
services:
  wiki-db:
    image: postgres:15-alpine
    environment:
      POSTGRES_DB: wiki
      POSTGRES_USER: wiki
      POSTGRES_PASSWORD: wiki-dev-only
    volumes:
      - wiki-db:/var/lib/postgresql/data

  wiki:
    image: ghcr.io/requarks/wiki:2
    depends_on: [wiki-db]
    environment:
      DB_TYPE: postgres
      DB_HOST: wiki-db
      DB_PORT: "5432"
      DB_USER: wiki
      DB_PASS: wiki-dev-only
      DB_NAME: wiki
    ports:
      - "3000:3000"

volumes:
  wiki-db:
```

```bash
docker compose up -d
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:3000/
# 200 — the setup wizard, on first run
```

The one manual step Wiki.js requires is the first-run wizard in a browser, to create the
admin account — after that, its Git storage module can be pointed at a repository of
Markdown files and it will sync pages from commits on the branch you choose, which is what
turns "publish the wiki" into "merge a pull request."

### Making "docs move with the code" a check, not a request

This is the part that actually holds the discipline in place. It needs no external service
and no dependency beyond git and bash:

```bash
#!/usr/bin/env bash
# check-docs-updated.sh
# Fails if this diff touches source but not docs/.
# Usage: check-docs-updated.sh <base-ref> <head-ref>
set -euo pipefail

BASE_REF="${1:-origin/main}"
HEAD_REF="${2:-HEAD}"

CHANGED=$(git diff --name-only "$BASE_REF" "$HEAD_REF")
TOUCHES_SOURCE=$(echo "$CHANGED" | grep -E '^(src|api)/' || true)
TOUCHES_DOCS=$(echo "$CHANGED" | grep -E '^docs/' || true)

if [[ -n "$TOUCHES_SOURCE" && -z "$TOUCHES_DOCS" ]]; then
  echo "This change touches source but not docs/:"
  echo "$TOUCHES_SOURCE"
  echo
  echo "If nothing here is documentation-relevant, say so in the PR description."
  exit 1
fi

echo "OK: docs/ moved with source, or nothing documentation-relevant changed."
```

{% raw %}
```yaml
# .github/workflows/docs-check.yml
name: docs-check
on: [pull_request]
jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - run: ./check-docs-updated.sh origin/${{ github.base_ref }} HEAD
```
{% endraw %}

And the test that proves the check actually does what it claims, built entirely from a
throwaway repository so it needs nothing beyond git:

```bash
#!/usr/bin/env bash
# test-check-docs-updated.sh
set -euo pipefail

WORKDIR=$(mktemp -d)
trap 'rm -rf "$WORKDIR"' EXIT
cd "$WORKDIR"

git init -q
git config user.email test@example.com
git config user.name test
mkdir -p src docs
echo "initial" > src/app.py
echo "initial" > docs/app.md
git add . && git commit -qm "baseline"

git checkout -qb change-source-only
echo "changed" > src/app.py
git commit -qam "touch source only"

cp "$OLDPWD/check-docs-updated.sh" .
chmod +x check-docs-updated.sh

if ./check-docs-updated.sh main HEAD; then
  echo "FAIL: expected the check to reject a source-only change"
  exit 1
fi
echo "PASS: source-only change was rejected"

echo "changed" > docs/app.md
git commit -qam "update docs too"

if ! ./check-docs-updated.sh main HEAD; then
  echo "FAIL: expected the check to accept source with matching docs"
  exit 1
fi
echo "PASS: source with matching docs was accepted"
```

```bash
chmod +x test-check-docs-updated.sh
./test-check-docs-updated.sh
# PASS: source-only change was rejected
# PASS: source with matching docs was accepted
```

## Conclusion

**A rule enforced by memory is not a rule, it is a hope.** "Update the docs in the same
change" only survives contact with a deadline if something other than a person's memory is
checking for it — a build that fails, not a policy that is trusted.

**Version control is what turns silent drift into a visible diff.** The mechanism is not
Markdown, and it is not any particular wiki engine — it is that a stale page becomes
something with a blame and an author, reviewed by the person who made it stale, instead of
a fact quietly going out of date in a database nobody diffs.

**Public accountability is a forcing function worth borrowing even without a public
project.** "No one to ask, nothing assumed" is a genuinely higher bar than most internal
documentation is held to, and holding internal docs to it — writing as if the reader has no
access to anyone's memory — catches gaps that a colleague would otherwise silently fill in
conversation.
