---
layout: post
title: "chmod -R and the setgid Bit: Why a Four-Digit Mode Is Not Enough"
subtitle: "A recursive chmod that looks correct can silently strip the bit that made group ownership propagate."
date: 2025-09-12 09:00:00 +0200
tags: [linux, docker, testing]
description: >-
  Fixing shared-volume permissions with chmod -R 775 looks like a correct fix
  and passes an immediate check, but it clears the setgid bit that made new
  files inherit the directory's group, so the fix quietly regresses days
  later. This covers how the octal digits actually work, why the fix has to
  treat files and directories differently, and a way to test it that a
  one-off manual check will not catch.
---

## The problem

Two containers write into the same bind-mounted directory as different UIDs, sharing a
GID so that either one can read what the other wrote:

```bash
sudo chown -R :shared_data /srv/data
sudo chmod -R 2775 /srv/data
```

`2775` sets read/write/execute for owner and group, read/execute for others, and the
leading `2` sets setgid on every directory it touches — meaning new files and
subdirectories created inside inherit the directory's group rather than the creating
process's primary group. This is exactly the mechanism that makes two containers with
different UIDs but the same GID able to share a directory at all: without it, a file a
second container writes gets that container's own primary group, and the first container
can no longer read it.

It works. Both containers write, both containers read what the other wrote, the
permissions look right in `ls -l`. Then, weeks later, a maintenance script runs:

```bash
sudo chmod -R 775 /srv/data
```

Nothing in this command looks dangerous. It is fixing an unrelated permission complaint —
maybe a file ended up `770` and something needed to read it — and `775` is a subset of
`2775` in every digit that is actually shown. But `chmod -R 775` is a four-character mode
with an *implicit* leading `0`. It does not merely fail to set setgid; it actively clears
it, on every directory the recursion touches, because a chmod's special-bits digit is
absolute, not additive. The directory tree still shows `775` in `ls -l`, still looks
correct, and from that point on every new file created inside inherits the *writer's* own
group instead of the directory's — which is invisible until the other container tries to
read a file the first one just wrote and gets `Permission denied`.

The bug is hard to notice specifically because the visible part of the mode — the three
familiar rwx triplets — never changed. Nothing in a superficial `ls -l /srv/data` comparison
before and after tells you setgid is gone; you have to know to check for the `s` in the
group execute position, and most permission audits do not.

## Working through it

### What each chmod digit actually means

A four-digit octal chmod is two things concatenated: a special-bits digit, then the usual
owner/group/other triplet. The special digit is a bitmask of its own: `4` is setuid, `2`
is setgid, `1` is sticky, and they combine by addition — `6` is setuid plus setgid, `2775`
is setgid plus the familiar `775`.

The part that catches people is that a three-digit mode is not "the special bits,
unspecified" — `chmod 775 file` is exactly equivalent to `chmod 0775 file`. The `0` is
implicit, and it is absolute: it does not mean "leave special bits as they are," it means
"set them to zero." There is no chmod numeric mode that means "whatever the special bits
currently are, keep them" — that is only expressible with the symbolic form, `chmod
g+s`, which is additive by design.

### Setgid on a directory versus setgid on a file

Setgid means something different depending on what it is set on, and this is where a
blanket `chmod -R 2775` earns a second look rather than blind trust. On a directory, it
does exactly what this article needs: new entries created inside inherit the directory's
group. On a *regular file*, setgid means something else entirely — historically, mandatory
file locking on some Unix variants, and on Linux specifically, if the file is also group
executable, the kernel runs it with the privileges of the file's group rather than the
invoking user's, which is a real, if usually inert, privilege escalation vector for any
file that later becomes executable by accident.

Recursing `chmod -R 2775` sets setgid on every file it touches too, not only directories.
For non-executable data files this bit is inert — nothing consults it — but it is not
nothing: a later `chmod +x` on one of those files, done for an unrelated reason, now
produces a group-executable file with setgid already set, and that combination is worth
never creating by accident. The correct recursive fix separates directories from files
explicitly:

```bash
sudo find /srv/data -type d -exec chmod 2775 {} +
sudo find /srv/data -type f -exec chmod 664 {} +
```

Directories get `2775` — setgid, so the inheritance mechanism keeps working. Files get
`664` — no execute bit at all for a pure data directory, which makes the setgid-on-an-
executable-file scenario impossible by construction, because nothing in the tree is
executable.

### Why `ls -l` alone is not a sufficient check

`ls -ld /srv/data` shows `drwxrwsr-x` when setgid is present — the `s` replacing the `x` in
the group triplet is the only visible sign, and it is easy to skim past when scanning a
long `ls -l` for the parts of the mode you were actually thinking about. The reliable
check is `stat`, which reports the mode numerically and is what an automated test should
assert against rather than parsing `ls` output:

```bash
stat -c '%a %n' /srv/data
# 2775 /srv/data
```

If a later maintenance action drops that to `775`, this is the line that changes, and it
is worth asserting on directly rather than trusting a human glance at directory listings
to catch it.

## The solution

A small script that fixes permissions correctly — setting setgid on directories only,
leaving files without an execute bit — plus a bats test that proves both the fix and the
regression it guards against.

```bash
#!/usr/bin/env bash
# fix-shared-permissions.sh
set -euo pipefail

target_dir="${1:?usage: fix-shared-permissions.sh <dir> <group>}"
group="${2:?usage: fix-shared-permissions.sh <dir> <group>}"

chown -R ":${group}" "${target_dir}"
find "${target_dir}" -type d -exec chmod 2775 {} +
find "${target_dir}" -type f -exec chmod 664 {} +
```

```bash
#!/usr/bin/env bats
# test/fix-shared-permissions.bats

setup() {
  export TEST_DIR
  TEST_DIR="$(mktemp -d)"
  mkdir -p "${TEST_DIR}/nested"
  touch "${TEST_DIR}/file.txt" "${TEST_DIR}/nested/other.txt"
}

teardown() {
  rm -rf "${TEST_DIR}"
}

@test "directories get setgid after the fix" {
  bash fix-shared-permissions.sh "${TEST_DIR}" "$(id -gn)"
  mode="$(stat -c '%a' "${TEST_DIR}")"
  [ "${mode}" = "2775" ]
}

@test "nested directories also get setgid" {
  bash fix-shared-permissions.sh "${TEST_DIR}" "$(id -gn)"
  mode="$(stat -c '%a' "${TEST_DIR}/nested")"
  [ "${mode}" = "2775" ]
}

@test "files do not carry setgid or an execute bit" {
  bash fix-shared-permissions.sh "${TEST_DIR}" "$(id -gn)"
  mode="$(stat -c '%a' "${TEST_DIR}/file.txt")"
  [ "${mode}" = "664" ]
}

@test "a subsequent chmod -R 775 would strip setgid -- this is the regression to avoid" {
  bash fix-shared-permissions.sh "${TEST_DIR}" "$(id -gn)"
  chmod -R 775 "${TEST_DIR}"
  mode="$(stat -c '%a' "${TEST_DIR}")"
  # This assertion documents the failure mode: 775, not 2775.
  [ "${mode}" = "775" ]
}
```

The last test does not assert desired behaviour — it pins down the exact regression, so
that anyone reading the test suite sees, in executable form, precisely what `chmod -R 775`
does to a tree the earlier tests just fixed. It is a guard against re-introducing the bug
via a "harmless" maintenance chmod, documented as a failing expectation on purpose.

### Verifying it

```bash
chmod +x fix-shared-permissions.sh
bats test/fix-shared-permissions.bats
```

Correct output is all four tests passing, including the one documenting the regression:

```text
 ✓ directories get setgid after the fix
 ✓ nested directories also get setgid
 ✓ files do not carry setgid or an execute bit
 ✓ a subsequent chmod -R 775 would strip setgid -- this is the regression to avoid

4 tests, 0 failures
```

## Conclusion

**A numeric chmod mode is absolute, not a diff.** Every digit you do not intend to change
still has to be specified as the value you want it to end up at — there is no numeric
"leave alone," and a shorter mode string does not mean "the rest is untouched," it means
the rest becomes zero.

**Special bits and the ordinary permission triplet are orthogonal, and blanket recursion
conflates them.** Directories and files play different roles under setgid, and a fix that
treats a whole tree with one chmod mode is choosing a bit's meaning for two different
kinds of entities at once, whether or not that was the intent.

**`ls -l` is for humans skimming, not for asserting.** A permission fix that matters is
worth a `stat`-based check in a test file, checked into the same place as the fix, because
the failure mode here is specifically that the visible parts of the output stay
reassuring while the part that mattered silently reverts.
