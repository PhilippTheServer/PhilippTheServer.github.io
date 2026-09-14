---
layout: post
title: "fsGroupChangePolicy: OnRootMismatch and the setgid Invariant That Makes It Sound"
subtitle: "A pod-level fsGroup makes kubelet recursively chown the entire volume on every mount. On a volume with 1.4 million files that is fifteen minutes per restart — unless the root already matches."
date: 2026-09-11 09:00:00 +0200
tags: [kubernetes, storage, performance, reliability]
description: >-
  A BuildKit pod with a persistent build cache sat in ContainerCreating for
  over fifteen minutes on every restart, because a pod-level fsGroup made
  kubelet re-chown 1.4 million cache files on every start. The fix is a single
  field — fsGroupChangePolicy: OnRootMismatch — and the reason it works is a
  detail about setgid directories that most write-ups of fsGroup omit.
---

## The problem

A stateful pod with a large persistent volume started taking fifteen minutes to
leave `ContainerCreating`. Not failing — just waiting. The container image was
small and local. The node had capacity. The pod's own logs never appeared,
because the container never ran; the delay was entirely in the volume being
made ready.

The volume held a build cache: a directory tree with 1.4 million files, grown
over months of container image builds, on a network-backed block volume. The
pod's security context set `fsGroup: 1000`, which is the standard,
recommended way to make a shared volume writable by a non-root process inside
the container. Nothing in that sentence sounds like it should cost fifteen
minutes.

It does, and the reason is in what `fsGroup` actually makes the kubelet do,
and in when it decides it has to do it.

## Working through it

### What fsGroup actually does at mount time

When a pod's security context carries `fsGroup`, the kubelet makes the volume
usable by that group before the container starts. The default behaviour — and
this is the part that is easy to believe is cheap — is a **recursive chown**:
every file and directory under the volume root is walked, and each one whose
group does not match `fsGroup` is `chown`ed to it. The kubelet does this
because the container's process will run as a user in that group, and a file
owned by a different group may not be writable by it.

On a volume with a handful of files, that walk is invisible. On a volume with
1.4 million files on a network-backed block device, it is a 1.4-million-file
metadata operation, executed on every pod start, including restarts of a pod
whose volume was not touched by anything in the meantime. The files did not
change. Their ownership was already correct. The kubelet does not know that,
and under the default policy it does not check in a cheap way — it walks.

The symptom this produces is specifically a pod that is *stuck*, not *broken*:
`kubectl describe pod` shows the pod in `ContainerCreating`, the events show
the volume being attached and mounted, and then nothing, for the duration of
the walk. There is no error to grep for, because from the kubelet's point of
view the operation is succeeding; it is just slow.

### The policy that skips the walk

The security context has a second field that most deployments never set:
`fsGroupChangePolicy`. Its two values define when the recursive chown runs:

- `Always` (the default): chown the volume root and walk the entire tree on
  every mount, unconditionally.
- `OnRootMismatch`: chown the volume root, and walk the tree **only if the
  root directory's group does not already match** `fsGroup`.

With `OnRootMismatch`, a volume whose root is already group `1000` costs one
`chown`-check on the root and nothing else, on every subsequent mount. The
1.4-million-file walk happens at most once — the first time the volume is
mounted after the policy exists — and never again.

The fix for the stuck pod is one line in the pod's security context:

```yaml
spec:
  securityContext:
    runAsUser: 1000
    runAsGroup: 1000
    fsGroup: 1000
    fsGroupChangePolicy: OnRootMismatch
```

### Why OnRootMismatch is safe here, and the setgid detail that makes it true

The policy is only as good as the assumption behind it: that if the root
directory has the right group, everything under it will too. That is not
guaranteed by `fsGroup` alone. It is guaranteed by the **setgid bit on the
root directory**, which the kubelet sets as part of applying `fsGroup`: a
directory with setgid propagates its group ownership to every file and
subdirectory created inside it, by any process.

So the invariant the policy relies on is: *the volume root is group 1000 and
setgid, therefore every file created under it by the workload is group 1000,
therefore a root check is a complete check.* The one-time cost of the initial
walk is what establishes the root's state; setgid then maintains the invariant
for the lifetime of the volume, and `OnRootMismatch` is what lets the kubelet
trust it.

The failure mode of getting this wrong is quiet in the direction you would not
expect: if something writes a file into the volume outside the setgid
propagation — a host-side tool, a different pod with a different `fsGroup`, a
restore from backup that does not preserve group — the root still matches, the
walk is still skipped, and the file lands with the wrong group. The container
then fails to write it, and the error looks like an application bug. This is
the price of the policy, and it is worth paying only when the volume has a
single writer, which is the case for a build cache owned by one stateful pod.

### Keeping the property honest with a test

A one-line manifest fix is easy to lose: someone removes the field while
tidying the security context, or copies the pod spec into a new one without
it, and the fifteen-minute start comes back with no announcement. The property
worth pinning is not "the field is present" in the abstract but the pairing
that makes it meaningful: **if the pod sets `fsGroup`, it must also set
`fsGroupChangePolicy: OnRootMismatch`**, so the two can never drift apart.

```python
# tests/test_buildkit_volume_ownership.py
from pathlib import Path

import yaml

BUILDKIT = Path(__file__).resolve().parents[1] / "apps" / "builder" / "buildkit.yaml"


def pod_security_context() -> dict:
    for doc in yaml.safe_load_all(BUILDKIT.read_text()):
        if doc and doc.get("kind") == "StatefulSet":
            return doc["spec"]["template"]["spec"].get("securityContext", {})
    raise AssertionError("builder StatefulSet not found")


def test_fsgroup_skips_the_recursive_chown():
    ctx = pod_security_context()
    if "fsGroup" in ctx:
        assert ctx.get("fsGroupChangePolicy") == "OnRootMismatch"
```

The test reads the manifest that ships, not a copy of it, so the thing it
responds to is editing the deployment. It is deliberately a pairing assertion
rather than a presence assertion: a security context with `fsGroup` and no
policy is exactly the shape that costs fifteen minutes, and that is the shape
the test must reject.

## The solution

The state of the fix:

1. The pod's security context carries `fsGroup: 1000` and
   `fsGroupChangePolicy: OnRootMismatch`, so the recursive walk runs at most
   once per volume and every later mount is a single root check.
2. The volume root is group 1000 and setgid, which is what makes the root
   check a complete check for everything created under it.
3. A test asserts the pairing on the shipped manifest, so removing the policy
   without removing the `fsGroup` fails the build rather than the next pod
   start.

The general shape of the problem is common enough to name: a per-mount
operation whose cost is proportional to the size of the volume, applied
unconditionally, to a volume whose contents are stable across mounts. The
answer is almost always the same three parts — a policy that makes the
operation conditional on a cheap check, an invariant (setgid, here) that makes
the cheap check sound, and a test that keeps the two from drifting apart.
Fifteen minutes on every restart is the price of omitting any one of them.

## Conclusion

`fsGroup` is the right tool for making a shared volume writable by a
non-root container, and its default behaviour is a recursive chown of the
entire volume on every mount. On a large, stable volume that turns every pod
restart into a metadata operation as big as the volume itself, and the pod
sits in `ContainerCreating` doing it, with no error anywhere.

`fsGroupChangePolicy: OnRootMismatch` makes the walk conditional on the root
directory's group, and the setgid bit the kubelet sets on that root is what
makes the condition sound: everything created under the root inherits its
group, so checking the root is checking the tree.

The field is one line, the safety argument is one paragraph, and the test is
one assertion on the shipped manifest. The fifteen minutes were the cost of
none of the three.
