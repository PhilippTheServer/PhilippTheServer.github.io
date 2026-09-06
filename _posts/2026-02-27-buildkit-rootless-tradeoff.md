---
layout: post
title: "Rootless BuildKit: What fuse-overlayfs Costs on a Cold Cache"
subtitle: "Avoiding a privileged builder trades container capability for filesystem speed"
date: 2026-02-27 09:00:00 +0200
tags: [docker, security, performance]
description: >-
  The standard BuildKit container image needs privileged mode to use the kernel's
  overlayfs snapshotter, which is unwelcome on a cluster that flags privileged
  workloads. Rootless BuildKit avoids that by running its snapshotter in userspace via
  fuse-overlayfs instead, and this article measures, reproducibly, what that substitution
  costs on a build with a cold cache.
---

## The problem

A standard BuildKit builder container needs `--privileged`:

```bash
docker buildx create --name native-builder --driver docker-container --use
```

This works, and it is exactly the kind of workload a cluster's admission policy is
designed to catch. `--privileged` grants every device, every capability, and disables
most of the isolation a container runtime provides. On infrastructure that runs a
Pod Security Admission "restricted" profile, or an equivalent OPA/Kyverno policy, a
privileged builder either gets rejected outright or needs a policy carve-out that a
security review will (rightly) ask hard questions about.

The reason BuildKit wants this is its default snapshotter: it manages image layers using
the kernel's `overlayfs`, which needs `CAP_SYS_ADMIN` to mount. Rootless BuildKit exists
to remove that requirement, and the trade it makes is the interesting part: it replaces
the kernel's overlay filesystem with `fuse-overlayfs`, an implementation of the same
layering logic that runs entirely in userspace via FUSE, requiring nothing more
privileged than access to `/dev/fuse`.

It works, unprivileged, on the same cluster that rejected the privileged builder. What it
costs is not visible until you build something with real filesystem churn — every file
operation during a layer diff now round-trips through a userspace FUSE daemon instead of
being handled inside the kernel, and on a cold cache, where a build is materialising many
new files rather than reusing cached layers, that overhead is where you actually feel it.
An article recommending rootless BuildKit that does not say this is not being honest
about what you are choosing.

## Working through it

### Why the snapshotter needs privilege at all

Overlayfs layers a writable directory on top of read-only ones by intercepting mounts at
the kernel level — mounting is a privileged operation, which is why the native
snapshotter needs `CAP_SYS_ADMIN` (in practice, `--privileged` is the common way of
getting there for a container). Rootless BuildKit avoids the mount syscall entirely:
`fuse-overlayfs` implements the same union semantics as a FUSE filesystem, which any
unprivileged process with `/dev/fuse` access can mount, because FUSE is designed
precisely to let unprivileged code implement filesystems.

### Where the userspace cost actually lands

FUSE means every read, write, `stat` and directory listing that touches the overlay
crosses from kernel space to a userspace daemon and back, instead of being resolved
directly by the kernel's own overlay driver. For a build whose layers are already cached
— most of a typical incremental build — this barely matters, because there is little new
file activity for the overlay to mediate. For a build creating many new files from
scratch — installing a package set, unpacking an archive, a build stage with no warm
cache to reuse — every one of those file operations pays the FUSE round-trip, and it adds
up in direct proportion to how many files the build actually touches.

This is why "rootless is slower" is not a useful sentence on its own: it depends
entirely on how much new filesystem work a given build does. A build that mostly reuses
cached layers will barely notice. A cold, file-heavy build will notice a lot.

### Measuring it instead of asserting it

The only honest way to state this cost is to reproduce it, because the size of the
effect depends on the build, the host kernel, and the storage backend underneath it. The
example below builds an image that creates several thousand small files — deliberately
file-count-heavy rather than network-heavy, so the timing reflects filesystem behaviour
and not network variance — under both snapshotters, cold and warm, so the difference (or
its absence, on your hardware) is something you observe rather than something this
article tells you to believe.

## The solution

A Dockerfile with real filesystem churn, and the two builder configurations to run it
under.

```dockerfile
# Dockerfile.manyfiles
FROM alpine:3.20
RUN mkdir -p /many-files && \
    i=0; while [ "$i" -lt 8000 ]; do \
      echo "file number $i" > "/many-files/file-$i.txt"; \
      i=$((i + 1)); \
    done
```

```bash
# native-builder.sh — the default, privileged snapshotter
docker buildx create --name native-builder --driver docker-container --use
docker buildx build --no-cache -t manyfiles:native -f Dockerfile.manyfiles . \
  --progress plain
```

```bash
# rootless-builder.sh — fuse-overlayfs, no privileged container
docker buildx create --name rootless-builder --driver docker-container \
  --driver-opt image=moby/buildkit:v0.13.2-rootless \
  --buildkitd-flags '--oci-worker-snapshotter=fuse-overlayfs' \
  --use
docker buildx build --no-cache -t manyfiles:rootless -f Dockerfile.manyfiles . \
  --progress plain
```

Run each twice: the first run is the cold-cache case (`--no-cache`, as above); the second
run, without `--no-cache`, is the warm case where nothing changed and BuildKit should
skip re-executing the `RUN` step entirely. Time both:

```bash
docker buildx use native-builder
time docker buildx build --no-cache -t manyfiles:native -f Dockerfile.manyfiles .
time docker buildx build -t manyfiles:native -f Dockerfile.manyfiles .

docker buildx use rootless-builder
time docker buildx build --no-cache -t manyfiles:rootless -f Dockerfile.manyfiles .
time docker buildx build -t manyfiles:rootless -f Dockerfile.manyfiles .
```

What to expect: the two warm-cache runs (no `--no-cache`) should be close to identical
between builders, both near-instant, because the cached layer is reused without touching
the snapshotter's file-handling path at all. The two cold-cache runs are where the
`fuse-overlayfs` round-trip shows up — expect the rootless builder's cold run to take
noticeably longer than the native one on the same file count, with the gap widening if
you raise the loop count in the Dockerfile and shrinking if you lower it. Run it on your
own hardware and record your own numbers rather than trusting a number from someone
else's kernel and disk.

```bash
docker buildx rm native-builder rootless-builder
```

## Conclusion

**A capability you remove has to go somewhere.** Rootless BuildKit does not make the
overlay filesystem's work free; it moves it from the kernel to a userspace FUSE daemon,
and userspace mediation of every file operation is never the fast path.

**The size of a trade-off depends on the workload, so measure the workload you actually
have.** A rootless builder is close to free for a cache-hit-heavy incremental build and
measurably slower for a build that manifests many new files. Neither statement
generalises to "rootless BuildKit is slow" or "rootless BuildKit is fine" — both are true
depending on which build you run.

**Choose the constraint you are actually under, honestly.** If a cluster policy rejects
privileged workloads outright, the FUSE overhead is the cost of being allowed to run at
all, and it is worth paying. If nothing enforces that policy, defaulting to rootless
anyway trades real build time for a security property nothing is currently requiring.
