---
layout: post
title: "fsGroup Recursive chown Hangs on Large Volumes"
subtitle: "Why a stateful pod's second restart is slower than its first, and what to do about it."
date: 2026-04-28 09:00:00 +0200
tags: [kubernetes, storage, ceph, performance]
description: >-
  Setting fsGroup on a pod makes kubelet walk the entire volume at every
  mount, not just the first one, and on a volume with millions of files that
  walk can take hours. Here is why the second mount is the one that hurts,
  and how to stop paying for it on every restart.
---

## The problem

A pod that sets `securityContext.fsGroup` asks Kubernetes to make every file on its
volumes readable and writable by a particular group, regardless of what a container
image's `UID`/`GID` happen to be. It is a small, reasonable-looking line:

```yaml
securityContext:
  fsGroup: 2000
```

It also means that, at mount time, kubelet recursively walks the volume, `chown`ing
every file to that group and setting the setgid bit on every directory so new files
inherit it too. On a fresh, empty volume in a dev cluster this is instant, so it is
easy to ship without ever noticing what it costs.

The cost shows up later, and not where most people expect. It is not really the first
mount that hurts — there is no way to avoid walking the tree once. It is every mount
after that. A pod restart, a reschedule to another node, a rolling update: each one
re-mounts the same volume, and by default kubelet repeats the *entire* recursive walk
every single time, even though the ownership is already correct from the last mount. On
a volume with a few thousand files that is a rounding error. On a volume with a few
million small files — a mail spool, a cache, an artefact store — that walk can take
minutes to hours, and it happens exactly when you most want the pod back: a crash loop,
a drained node, an incident already in progress.

It is a hard problem to catch in testing, because the trigger is data volume, not code.
A developer's throwaway PVC with a handful of files behaves identically whether the
`fsGroupChangePolicy` is set or not. The difference only appears once a volume has been
in production long enough to accumulate the file count that makes the walk expensive,
at which point it looks like a sudden regression in pod startup time with no matching
change in the manifest.

## Working through it

### What kubelet is actually doing

For any volume plugin or CSI driver that supports `fsGroup`, kubelet's mount path calls
into a function that walks the volume tree and applies group ownership and the setgid
bit to every file and directory it finds (`SetVolumeOwnership`, if you want to go and
read the source). This is a plain recursive `chown`/`chmod`, one syscall pair per
filesystem entry, done synchronously before the container starts. There is no way to
make a single walk of ten million files fast; the walk is doing real, necessary work
the first time.

### Why the default punishes every restart, not just the first mount

Until Kubernetes 1.20, that walk ran unconditionally on every mount, with no way to
skip it. Since 1.20, `fsGroupChangePolicy` controls this, and it has two values:

- `Always` — the default when the field is omitted. Every mount gets the full
  recursive walk, unconditionally, regardless of whether the ownership is already
  correct.
- `OnRootMismatch` — kubelet checks only the top-level directory of the volume: its
  owning group and whether the setgid bit is already set as `fsGroup` would set it. If
  the root already matches, kubelet assumes the rest of the tree matches too, and skips
  the walk entirely.

The saving is real but the assumption behind it is worth stating plainly: `
OnRootMismatch` checks the root, not the tree. If something writes a file into the
volume out of band — a sidecar running as a different user, a restore from a backup
taken with different ownership, a manual `kubectl cp` — with the wrong group, and the
root directory's own ownership still matches, kubelet will not notice or correct it.
You are trading a rare, silent correctness gap for a large, repeated, and much more
visible time cost. For the overwhelming majority of stateful workloads, where the only
writer to the volume is the pod itself, that trade is worth taking. It is worth stating
rather than assuming, though, because it is exactly the kind of trade-off that looks
free until the one time it is not.

### The other lever: does kubelet need to do this at all

`fsGroupChangePolicy` only matters if kubelet is doing the chown in the first place.
Some CSI drivers declare `fsGroupPolicy: None` on their `CSIDriver` object, which tells
kubelet to skip fsGroup handling for that driver entirely — usually because the backend
already manages permissions itself and a client-side recursive chown would be redundant
or even actively wrong. Whether this applies to you depends entirely on your driver and
its configured mode; for a CSI driver backing a networked filesystem, such as Ceph via
ceph-csi, this varies by driver version and access mode, so check rather than assume:

```bash
kubectl get csidriver <driver-name> -o yaml
```

Look at `spec.fsGroupPolicy`. If it already reads `None`, changing
`fsGroupChangePolicy` on your pods will do nothing, because kubelet was never chowning
that volume in the first place — the fix, if you have a real ownership problem, lies in
the driver's own configuration, not the pod spec. Check this before reaching for
`OnRootMismatch`, since it tells you whether the whole mechanism is even in play.

### It is file count, not volume size

The walk's cost is one syscall pair per filesystem entry. A 2 TB volume holding fifty
large files chowns almost instantly. A 10 GB volume holding five million small files
does not. Any capacity-planning intuition built around volume size will mislead you
here; the number that matters is file count, and it is worth actually knowing it for
your own volumes before deciding this problem does not apply to you.

## The solution

The full demonstration below runs on a laptop with `kind`. It seeds a volume with a
large number of files, then times pod startup twice under each `fsGroupChangePolicy`,
so the comparison that matters — the *second* mount of the same, already-correctly-owned
volume — is something you actually measure rather than take on faith.

```bash
kind create cluster --name fsgroup-demo
```

```yaml
# pvc.yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: fsgroup-data
spec:
  accessModes: ["ReadWriteOnce"]
  storageClassName: standard
  resources:
    requests:
      storage: 1Gi
```

```yaml
# seed-job.yaml
apiVersion: batch/v1
kind: Job
metadata:
  name: seed-files
spec:
  template:
    spec:
      restartPolicy: Never
      containers:
        - name: seed
          image: busybox:1.36
          command:
            - sh
            - -c
            - "seq 1 200000 | xargs -P4 -I{} touch /data/file-{} && echo done"
          volumeMounts:
            - name: data
              mountPath: /data
      volumes:
        - name: data
          persistentVolumeClaim:
            claimName: fsgroup-data
```

```yaml
# pod-always.yaml
apiVersion: v1
kind: Pod
metadata:
  name: fsgroup-always
spec:
  securityContext:
    fsGroup: 2000
    fsGroupChangePolicy: Always
  containers:
    - name: app
      image: busybox:1.36
      command: ["sleep", "3600"]
      volumeMounts:
        - name: data
          mountPath: /data
  volumes:
    - name: data
      persistentVolumeClaim:
        claimName: fsgroup-data
```

```yaml
# pod-onrootmismatch.yaml
apiVersion: v1
kind: Pod
metadata:
  name: fsgroup-onrootmismatch
spec:
  securityContext:
    fsGroup: 2000
    fsGroupChangePolicy: OnRootMismatch
  containers:
    - name: app
      image: busybox:1.36
      command: ["sleep", "3600"]
      volumeMounts:
        - name: data
          mountPath: /data
  volumes:
    - name: data
      persistentVolumeClaim:
        claimName: fsgroup-data
```

Seed the volume once, then bring each pod up twice, timing every start:

```bash
kubectl apply -f pvc.yaml
kubectl apply -f seed-job.yaml
kubectl wait --for=condition=Complete job/seed-files --timeout=120s

kubectl apply -f pod-always.yaml
time kubectl wait --for=condition=Ready pod/fsgroup-always --timeout=300s   # first mount: full walk
kubectl delete pod fsgroup-always
kubectl apply -f pod-always.yaml
time kubectl wait --for=condition=Ready pod/fsgroup-always --timeout=300s   # second mount: full walk again

kubectl apply -f pod-onrootmismatch.yaml
time kubectl wait --for=condition=Ready pod/fsgroup-onrootmismatch --timeout=300s   # first mount: full walk
kubectl delete pod fsgroup-onrootmismatch
kubectl apply -f pod-onrootmismatch.yaml
time kubectl wait --for=condition=Ready pod/fsgroup-onrootmismatch --timeout=300s   # second mount: root check only, walk skipped
```

The absolute numbers depend on your machine's disk and filesystem, and 200,000 files on
a laptop SSD will not reproduce an hours-long production hang — that requires the
millions-of-files scale this article opened with. What is reproducible, and is the
actual point, is the *shape* of the result: the `Always` pod takes roughly the same time
on both its first and second start, while the `OnRootMismatch` pod's second start is
markedly faster than its first, because the root directory's ownership already matches
and the walk is skipped entirely. Run it on your own data at production scale before
deciding the difference does or does not matter to you.

## Conclusion

**`OnRootMismatch` trades a rare correctness gap for a large, repeated time saving, and
for almost all stateful workloads that trade is worth making.** The failure mode it
accepts — an out-of-band write with the wrong ownership going unnoticed — is uncommon
precisely because most volumes are written to only by the pod that mounts them.

**Check whether your CSI driver needs kubelet's chown at all before reaching for a
change-policy fix.** A `CSIDriver` object with `fsGroupPolicy: None` means the whole
mechanism this article describes is already switched off for that driver, and the fix
for an ownership problem lies elsewhere.

**The cost is driven by file count, not volume size**, which cuts against most people's
intuition for capacity planning. Know your own file counts before assuming this does or
does not apply to you.

**Measure on your own data.** The relative difference between `Always` and
`OnRootMismatch` on a second mount is the whole demonstration; the absolute numbers on a
laptop with 200,000 files will not resemble the absolute numbers on a production volume
with five million, and only the latter tells you what you actually need to know.
