---
layout: post
title: "From a Large Allowlist to Three Denials: Permissions for a Coding Agent"
subtitle: "Containing three blast-radius effects instead of enumerating every safe command."
date: 2026-08-11 09:00:00 +0200
tags: [agents, security, linux]
description: >-
  A command allowlist for an autonomous agent never converges, because the
  set of legitimate commands a real task needs is not enumerable in advance,
  and a name-based list is also easy to defeat. This article moves the
  boundary from command names to blast radius using kernel-level sandboxing,
  with a runnable script and its trade-offs.
---

## The problem

The obvious way to let a coding agent run shell commands is to give it a list of the ones
it's allowed to run: `ls`, `cat`, `grep`, `git status`, and so on, growing as real work
demands more of it.

It never stops growing. A day of ordinary work needs `jq` to inspect a JSON response,
`sed -n` to view part of a file, `find -exec` to batch-rename something, `awk` for a column
extraction — each one a small, legitimate need, and each one a pull request against the
allowlist before the agent can do the work a person asked for. The list is chasing a moving
target: the set of commands a competent worker might reasonably need is not something you
can enumerate ahead of time, because it's exactly as large as the set of tasks you might
ask for.

Worse, matching on the command name doesn't actually bound what a command can do. An
allowlist entry for `git` because `git status` and `git log` are safe also permits:

```bash
git config --system core.pager 'rm -rf ~; true'
```

which is still, syntactically, "running git." A regex over the first word of a command
line is a check on vocabulary, not on effect, and the two only coincide by accident for
commands that happen to be single-purpose.

## Working through it

### Enumerate the failure mode, not the command set

The actual risk from letting an agent run arbitrary commands is not "which binaries" —
it's a small number of effects: destroying data outside the working directory, sending
data somewhere it shouldn't go, escalating privilege, or leaving something running that
persists after the session ends. Almost every legitimate command an agent might want to run
does none of these things, regardless of its name.

### Move the boundary from command name to blast radius

If the boundary is enforced by the kernel instead of by inspecting the command string, the
question "is this command safe" stops being the thing you check. Namespaces, a read-only
root filesystem, a scoped writable mount, and dropped capabilities mean a command can be
allowed to run *at all* without needing to be individually vetted, because nothing it does
can exceed the boundary.

### Allow the shell, deny the effect

`bubblewrap` (`bwrap`) builds exactly this kind of sandbox from ordinary Linux namespaces,
without needing root or a full container runtime. Inside it, an agent can run an unmodified
shell, install whatever it likes within the sandbox, and invoke any binary present on the
host image — none of that needs a per-command decision, because none of it can write
outside the bound-mounted workspace, reach the network, or gain a capability it didn't
start with.

### What you give up

This does not stop an agent from making a bad decision that stays within bounds — deleting
every file in its own workspace, say, or spinning a CPU indefinitely. It needs unprivileged
user namespaces enabled in the kernel, which some hardened distributions disable by
default, and it costs a script per invocation instead of a shell alias. And any task that
genuinely needs network access — installing a package, cloning a dependency — requires a
deliberate, separate exception. That reopens a second, smaller list: not of commands, but
of egress destinations, which is a far more stable set than the list of binaries a real
task might need.

## The solution

```bash
#!/usr/bin/env bash
# run-sandboxed.sh — run a command with three denials enforced by the
# kernel, not by a list of permitted binaries.
#
# Usage: ./run-sandboxed.sh /path/to/workspace -- <command> [args...]
set -euo pipefail

workspace="$1"; shift
if [[ "$1" != "--" ]]; then
  echo "usage: $0 <workspace> -- <command> [args...]" >&2
  exit 2
fi
shift

exec bwrap \
  --ro-bind /usr /usr \
  --ro-bind /bin /bin \
  --ro-bind /lib /lib \
  --ro-bind-try /lib64 /lib64 \
  --ro-bind /etc/resolv.conf /etc/resolv.conf \
  --proc /proc \
  --dev /dev \
  --tmpfs /tmp \
  --bind "$workspace" /workspace \
  --chdir /workspace \
  --unshare-net \
  --unshare-pid \
  --die-with-parent \
  --cap-drop ALL \
  --new-session \
  "$@"
```

Install and try it on an ordinary Linux laptop:

```bash
sudo apt-get install -y bubblewrap
chmod +x run-sandboxed.sh

mkdir -p /tmp/demo-workspace
echo "hello" > /tmp/demo-workspace/file.txt

# A destructive command only affects the bind-mounted workspace; the rest
# of the filesystem is a read-only view, not the host's real one:
./run-sandboxed.sh /tmp/demo-workspace -- bash -c 'rm -rf /*; ls /'
```

```
bin  dev  etc  lib  lib64  proc  tmp  usr  workspace
```

```bash
# Network is denied by default, regardless of which tool tries to use it:
./run-sandboxed.sh /tmp/demo-workspace -- curl -sS https://example.com
```

```
curl: (6) Could not resolve host: example.com
```

```bash
# Ordinary work still runs, with no per-command approval needed:
./run-sandboxed.sh /tmp/demo-workspace -- bash -c 'echo world >> file.txt; cat file.txt'
```

```
hello
world
```

```bash
cat /tmp/demo-workspace/file.txt   # on the real host, unaffected by the rm -rf above
```

```
hello
world
```

When a task genuinely needs network access, that's an explicit, separate exception —
`--share-net` added deliberately for that invocation, not a standing permission — and it
deserves its own decision about where the traffic is allowed to go rather than simply
whether it's allowed at all.

## Conclusion

An allowlist of names is fighting a search space that grows every time someone does
legitimate new work; moving the boundary from names to effects stops that growth without
loosening what's actually being protected.

Kernel-level isolation makes broad permission affordable, because permission no longer
implies unlimited blast radius — the two used to be the same decision and now they aren't.

It isn't free: unprivileged namespaces have to be available on the host, and containment
doesn't stop an in-bounds mistake or resource abuse — it bounds where damage can happen,
not whether a bad decision gets made at all.

Once effects are contained, the list that remains — of egress destinations, in this case —
is small and changes rarely, unlike a list of commands, which is the actual sign that the
boundary has moved to the right place.
