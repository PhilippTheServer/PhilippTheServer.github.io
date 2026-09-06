---
layout: post
title: "Object Storage as the Durability Boundary for a Container Registry"
subtitle: "Moving image blobs off a PVC so a reclaim policy or a bad prune can't take them with it."
date: 2026-04-24 09:00:00 +0200
tags: [kubernetes, storage, docker]
description: >-
  A self-hosted registry backed by a single PersistentVolumeClaim keeps exactly
  one copy of every image layer, and routine maintenance is enough to delete
  it. This walks through why the fix is moving the durability guarantee to
  object storage rather than the volume, and gives a complete, runnable
  example that proves the registry container itself has become disposable.
---

## The problem

A self-hosted Docker Registry (the `distribution` project) with the default `filesystem`
storage driver writes every layer blob, manifest and tag to a directory on disk. Back that
directory with a single PersistentVolumeClaim, which is the obvious and common thing to do,
and you have exactly one copy of every image you have ever pushed.

```yaml
# Minimal, and this is the problem, not an example to copy.
apiVersion: apps/v1
kind: Deployment
metadata:
  name: registry
spec:
  replicas: 1
  template:
    spec:
      containers:
        - name: registry
          image: registry:2.8.3
          volumeMounts:
            - name: data
              mountPath: /var/lib/registry
      volumes:
        - name: data
          persistentVolumeClaim:
            claimName: registry-data
```

Nothing here is a mistake by itself. It is what everyone writes the first time, and it
works for months. Then one of three entirely ordinary things happens:

1. Someone runs `helm uninstall registry` to move the chart to a new namespace, or bumps
   a chart version that renames the PVC, and the old claim gets garbage-collected along
   with it.
2. The StorageClass backing the claim has `reclaimPolicy: Delete`, which is the default
   for most dynamic provisioners, and a PV that outlives its claim for any reason is
   reclaimed straight into deletion.
3. Someone runs the registry's own `bin/registry garbage-collect` — the documented way to
   reclaim space from untagged blobs — without `--dry-run` first, against a config that
   does not match what actually got pushed, and it deletes blobs a manifest still points
   at.

None of these are exotic operator errors. They are the routine maintenance operations —
a chart upgrade, a storage class default, a disk-space cleanup — that happen to intersect
with the one place all your image data lives. The failure is also quiet: nothing tells you
a layer is gone until a pull for an old tag returns `MANIFEST_UNKNOWN` or a 404 on a blob,
usually during an incident when you least want to discover it.

## Working through it

### Move the durability guarantee below the registry, not inside it

The registry's filesystem driver treats its backing store as a plain directory tree. A
PVC gives it one. Object storage — S3, or an S3-compatible service such as MinIO or a
Ceph RGW endpoint — gives it something that is already replicated or erasure-coded by a
system whose entire job is not losing objects, and whose lifecycle is independent of the
registry's.

That independence is the actual fix. Deleting the registry's Deployment, its PVC (if it
still has one for anything), or its whole Helm release no longer touches a single blob,
because the blobs were never inside the thing you just deleted. The failure modes in the
previous section — an uninstall, a reclaim policy, a rename — all operate on Kubernetes
objects. Object storage is not a Kubernetes object.

### A stateless registry is a smaller thing to reason about

Once the registry's driver points at S3, the registry pod itself stops holding anything
that matters. You can run it with `replicas: 3` behind a Service, kill any one of them,
let the scheduler move them, or uninstall and reinstall the whole chart, and every one of
those operations is now a compute change, not a data change. This is the same shift you
would make for any application: state that used to live next to the process moves to a
system built to keep it, and the process becomes replaceable. It is worth doing even on a
single-replica registry, because "replaceable" is the property that makes an uninstall
safe, not just the property that enables scaling.

### Object storage is a second guard, not a fix for garbage-collection

It is tempting to read the previous two points as "moving to S3 solves registry data
loss." It solves the infrastructure half of it. It does not solve the application half.

`garbage-collect --delete-untagged` walks the registry's own manifest and blob metadata
and deletes what it decides is unreferenced. If that decision is wrong — a bug, a race
with a concurrent push, a config that does not match the storage actually in use — it
will delete blobs from S3 exactly as readily as it would from a local disk. S3 versioning
and, where the backend supports it, object-lock/WORM retention are a genuine second layer
of defence here: a versioned bucket keeps the previous version of an object after a
delete, so an over-eager GC run is recoverable rather than final. But that is a separate
control you have to turn on deliberately, not a side effect of using S3 as the driver. Say
plainly what changed and what did not: object storage removes the "someone deleted the
PVC" failure mode almost entirely; it leaves the "the application deleted its own data on
purpose, incorrectly" failure mode exactly where it was, and only mitigates it if you also
enable versioning and treat GC runs with the caution they deserve.

## The solution

A complete lab you can run on a laptop with `docker compose up`. It runs MinIO as the S3
backend and a registry configured with the `s3` storage driver, then proves durability
lives in MinIO by destroying the registry container entirely and pulling the image back
out.

```yaml
# docker-compose.yml
services:
  minio:
    image: minio/minio:RELEASE.2023-09-04T19-57-37Z
    command: server /data --console-address ":9001"
    environment:
      MINIO_ROOT_USER: minioadmin
      MINIO_ROOT_PASSWORD: minioadmin
    ports:
      - "9000:9000"
      - "9001:9001"
    volumes:
      - minio-data:/data

  create-bucket:
    image: minio/mc:RELEASE.2023-09-07T22-48-55Z
    depends_on:
      - minio
    entrypoint: >
      /bin/sh -c "
      until mc alias set local http://minio:9000 minioadmin minioadmin; do sleep 1; done;
      mc mb --ignore-existing local/registry-data;
      "

  registry:
    image: registry:2.8.3
    depends_on:
      - create-bucket
    ports:
      - "5000:5000"
    environment:
      REGISTRY_STORAGE: s3
      REGISTRY_STORAGE_S3_ACCESSKEY: minioadmin
      REGISTRY_STORAGE_S3_SECRETKEY: minioadmin
      REGISTRY_STORAGE_S3_REGION: us-east-1
      REGISTRY_STORAGE_S3_REGIONENDPOINT: http://minio:9000
      REGISTRY_STORAGE_S3_BUCKET: registry-data
      REGISTRY_STORAGE_S3_SECURE: "false"
      REGISTRY_STORAGE_S3_FORCEPATHSTYLE: "true"

volumes:
  minio-data:
```

Note there is no volume mounted on `registry` at all — the whole point is that its
container filesystem is disposable.

Bring it up and push an image:

```bash
docker compose up -d
docker pull alpine:3.19
docker tag alpine:3.19 localhost:5000/alpine:3.19
docker push localhost:5000/alpine:3.19
```

Expected output from the push ends with something like:

```
3.19: digest: sha256:<...> size: 528
```

Now confirm it pulls back, remove the tag locally so the next pull is real, and then
destroy the registry container — not just stop it, remove it, so any writable layer it
had is gone:

```bash
docker rmi localhost:5000/alpine:3.19
docker compose rm -f -s registry
docker compose up -d registry
docker pull localhost:5000/alpine:3.19
```

The pull succeeds:

```
3.19: Pulling from alpine
Digest: sha256:<same digest as before>
Status: Downloaded newer image for localhost:5000/alpine:3.19
```

The registry container that just handled this pull is not the one that handled the push —
it is a fresh container with an empty filesystem. Nothing was recovered from it, because
nothing needed to be. Every blob and manifest it just served came from `minio-data`, the
one volume in this stack that was never touched. You can make the point harder still by
also running `docker compose down` (without `-v`) between the push and the pull: the whole
`registry` and `create-bucket` containers disappear and are recreated, and the image is
still there.

## Conclusion

**Durability belongs to whichever layer is actually built to replicate data, not to
whichever layer happens to be easiest to attach a PVC to.** A PVC is a convenient place to
put data. It is not, by itself, a promise that the data survives anything other than the
pod restarting.

**Making a component stateless is a durability strategy, not just a scaling one.** The
registry in this example did not need three replicas to benefit from the S3 driver — it
needed to survive a chart reinstall without anyone thinking about it, and statelessness is
what gets you that for free.

**Object storage is not a substitute for careful application-level deletion.** It removes
the infrastructure-level failure modes — a reclaim policy, an uninstalled release, a
renamed claim — almost entirely. It does nothing for a `garbage-collect` run that was
wrong about what it was collecting, unless you have also turned on bucket versioning and
treat that command with the same caution as `rm -rf`.

**The pattern generalises past registries.** Any service that is currently storing blobs
on a PVC because that is what was reached for first — artifact stores, build caches,
anything that is really "files plus a small index" — is a candidate for the same move, and
the test is the same one used here: destroy the compute, keep the object store, and see if
a pull still works.
