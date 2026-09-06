---
layout: post
title: "Ceph CSI StorageClasses: RBD for Block, CephFS for Shared Volumes"
subtitle: "One Ceph cluster, two provisioners, chosen by whether a volume needs one writer or many."
date: 2026-04-17 09:00:00 +0200
tags: [kubernetes, ceph, storage]
description: >-
  A database needs an exclusive block device and a multi-pod workload needs
  a volume several nodes can write to at once, and a single Ceph cluster
  can serve both, but only if each workload uses the right CSI provisioner.
  This works through why RBD and CephFS answer different access patterns
  and gives a complete, reproducible Rook-Ceph setup on a local kind
  cluster proving both, including the failure that shows up when they're
  swapped.
---

## The problem

A Deployment with three replicas, all meant to write into a shared uploads directory,
looks reasonable with an ordinary block-backed StorageClass:

```yaml
# Broken. Do not copy this.
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: shared-uploads
spec:
  accessModes: ["ReadWriteOnce"]
  storageClassName: rook-ceph-block
  resources:
    requests:
      storage: 1Gi
```

The first replica schedules and mounts the volume without complaint. The second and
third sit in `ContainerCreating` indefinitely:

```
Warning  FailedAttachVolume  pod/upload-writer-7d4-xk2p9
Multi-Attach error for volume "pvc-3a1f..." Volume is already exclusively
attached to one node and can't be attached to another
```

`ReadWriteOnce` is not a suggestion or a soft default — an RBD-backed volume is a block
device, and a block device can be attached to exactly one node's kernel at a time.
Nothing about asking Kubernetes nicely changes that; it's a property of how the storage
is implemented, not a policy Kubernetes is enforcing on top of storage that could
otherwise share.

The opposite mistake is quieter and more expensive to notice. A single-instance Postgres
StatefulSet given a `ReadWriteMany` CephFS-backed volume instead of a block device works
— CephFS is a real POSIX filesystem and Postgres runs on it without complaint — but every
write now goes through a network filesystem with its own metadata server, its own
caching semantics, and its own latency profile, none of which a relational database's
write-ahead log was designed around. Nothing crashes. It's simply slower and behaves
subtly differently under concurrent access than the exclusive local-feeling block device
the workload actually wants, and because nothing errors, this mismatch tends to survive
in production long after anyone remembers a choice was made.

## Working through it

### One Ceph cluster, two genuinely different storage engines

Ceph exposes more than one way to consume the same underlying cluster. RBD (RADOS Block
Device) presents a pool as raw block devices — each one is a single object a client
attaches exclusively, the same relationship a VM has with a virtual disk. CephFS is a
POSIX filesystem layered on top of the same object store, with its own metadata servers
(MDS) coordinating concurrent access from multiple clients at once. These aren't two
configurations of one thing — they're different data paths with different consistency
and concurrency models, and Kubernetes exposes each through its own CSI driver and
therefore its own `StorageClass`.

### RWO is the correct shape for a database, not just the only shape RBD offers

A single-primary database wants exactly one thing attached to exactly one node: nothing
else should ever be able to write to its data directory concurrently, by design. RBD's
`ReadWriteOnce` restriction isn't a limitation to work around for this workload — it's
the same guarantee the database already assumes about local disk, enforced one layer
lower, by the storage system itself rather than by application-level discipline. It also
gives the database near-block-device latency: no metadata server round trip on every
`fsync`, no shared-filesystem locking protocol between clients that don't exist here in
the first place.

### RWX is not available from RBD at any configuration

There's no flag that turns an RBD volume into something multiple nodes can mount
simultaneously — the access mode restriction is structural, not a default that a braver
setting relaxes. A workload that genuinely needs several pods, potentially on different
nodes, reading and writing the same files concurrently has to use a filesystem-shaped
volume. CephFS's `ReadWriteMany` support exists because its MDS layer is doing the actual
work of coordinating those concurrent clients — work that has no equivalent in a block
device's contract.

### Choosing per-volume, not per-cluster

Both provisioners can point at the same Ceph cluster; the choice is per-`PersistentVolumeClaim`,
made by the access pattern that specific workload actually needs. A cluster with both a
`rook-ceph-block` and a `rook-cephfs` `StorageClass` isn't offering redundant options —
it's offering the two shapes storage actually comes in for anything running on it.

### The honest costs of each

CephFS's flexibility isn't free: every metadata operation — creating a file, listing a
directory — is a round trip to an MDS daemon, and under-provisioned or unscaled MDS
capacity becomes a real bottleneck under concurrent load in a way RBD, with no shared
metadata layer to contend on, doesn't. RBD's exclusivity has its own cost the other
direction: rescheduling a pod to a different node means the volume has to be detached
from the old node and attached to the new one before the new pod can start, which is
measured in seconds to low tens of seconds — a real, visible delay on a StatefulSet
rollout or a node drain that a shared filesystem volume doesn't incur, since nothing has
to be exclusively released first.

## The solution

A minimal, single-node Rook-Ceph cluster on `kind`, enough to exercise both drivers.
This is a disposable test cluster, not a production topology — one OSD backed by a loop
device, replication factor 1. One caveat worth knowing before running this on a shared
machine: `losetup` creates a kernel-wide loop device, visible outside the container it
was created in as well, since containers on the same host share one kernel.

```bash
kind create cluster --name ceph-storage-demo

NODE=ceph-storage-demo-control-plane
docker exec "$NODE" bash -c '
  set -e
  modprobe rbd || true
  dd if=/dev/zero of=/ceph-osd-disk.img bs=1M count=8192
  losetup -f /ceph-osd-disk.img
'
LOOP_DEV=$(docker exec "$NODE" losetup -j /ceph-osd-disk.img | cut -d: -f1)
LOOP_NAME=$(basename "$LOOP_DEV")
echo "OSD backing device: $LOOP_NAME"
```

Install Rook, pinned:

```bash
ROOK_VERSION=v1.15.3
kubectl apply -f https://raw.githubusercontent.com/rook/rook/$ROOK_VERSION/deploy/examples/crds.yaml
kubectl apply -f https://raw.githubusercontent.com/rook/rook/$ROOK_VERSION/deploy/examples/common.yaml
kubectl apply -f https://raw.githubusercontent.com/rook/rook/$ROOK_VERSION/deploy/examples/operator.yaml
kubectl -n rook-ceph wait --for=condition=available --timeout=300s deployment/rook-ceph-operator
```

```yaml
# ceph-cluster.yaml — substitute $LOOP_NAME and the node name below
apiVersion: ceph.rook.io/v1
kind: CephCluster
metadata:
  name: rook-ceph
  namespace: rook-ceph
spec:
  cephVersion:
    image: quay.io/ceph/ceph:v18.2.4
  dataDirHostPath: /var/lib/rook
  mon:
    count: 1
    allowMultiplePerNode: true
  mgr:
    count: 1
  storage:
    useAllNodes: false
    useAllDevices: false
    nodes:
      - name: ceph-storage-demo-control-plane
        devices:
          - name: "REPLACE_WITH_LOOP_NAME"
```

```bash
sed "s/REPLACE_WITH_LOOP_NAME/$LOOP_NAME/" ceph-cluster.yaml | kubectl apply -f -
kubectl -n rook-ceph wait --for=jsonpath='{.status.phase}'=Ready cephcluster/rook-ceph --timeout=600s
```

The block pool and its StorageClass:

```yaml
# rbd-storageclass.yaml
apiVersion: ceph.rook.io/v1
kind: CephBlockPool
metadata:
  name: replicapool
  namespace: rook-ceph
spec:
  failureDomain: osd
  replicated:
    size: 1
---
apiVersion: storage.k8s.io/v1
kind: StorageClass
metadata:
  name: rook-ceph-block
provisioner: rook-ceph.rbd.csi.ceph.com
parameters:
  clusterID: rook-ceph
  pool: replicapool
  imageFormat: "2"
  imageFeatures: layering
  csi.storage.k8s.io/provisioner-secret-name: rook-csi-rbd-provisioner
  csi.storage.k8s.io/provisioner-secret-namespace: rook-ceph
  csi.storage.k8s.io/controller-expand-secret-name: rook-csi-rbd-provisioner
  csi.storage.k8s.io/controller-expand-secret-namespace: rook-ceph
  csi.storage.k8s.io/node-stage-secret-name: rook-csi-rbd-node
  csi.storage.k8s.io/node-stage-secret-namespace: rook-ceph
reclaimPolicy: Delete
allowVolumeExpansion: true
```

The filesystem and its StorageClass:

```yaml
# cephfs-storageclass.yaml
apiVersion: ceph.rook.io/v1
kind: CephFilesystem
metadata:
  name: sharedfs
  namespace: rook-ceph
spec:
  metadataPool:
    replicated:
      size: 1
  dataPools:
    - name: data0
      replicated:
        size: 1
  metadataServer:
    activeCount: 1
    activeStandby: false
---
apiVersion: storage.k8s.io/v1
kind: StorageClass
metadata:
  name: rook-cephfs
provisioner: rook-ceph.cephfs.csi.ceph.com
parameters:
  clusterID: rook-ceph
  fsName: sharedfs
  pool: sharedfs-data0
  csi.storage.k8s.io/provisioner-secret-name: rook-csi-cephfs-provisioner
  csi.storage.k8s.io/provisioner-secret-namespace: rook-ceph
  csi.storage.k8s.io/controller-expand-secret-name: rook-csi-cephfs-provisioner
  csi.storage.k8s.io/controller-expand-secret-namespace: rook-ceph
  csi.storage.k8s.io/node-stage-secret-name: rook-csi-cephfs-node
  csi.storage.k8s.io/node-stage-secret-namespace: rook-ceph
reclaimPolicy: Delete
```

```bash
kubectl apply -f rbd-storageclass.yaml
kubectl apply -f cephfs-storageclass.yaml
```

The database, on RBD:

```yaml
# postgres.yaml
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: postgres
spec:
  serviceName: postgres
  replicas: 1
  selector:
    matchLabels: { app: postgres }
  template:
    metadata:
      labels: { app: postgres }
    spec:
      containers:
        - name: postgres
          image: postgres:16
          env:
            - name: POSTGRES_PASSWORD
              value: app
          volumeMounts:
            - name: data
              mountPath: /var/lib/postgresql/data
  volumeClaimTemplates:
    - metadata:
        name: data
      spec:
        accessModes: ["ReadWriteOnce"]
        storageClassName: rook-ceph-block
        resources:
          requests:
            storage: 2Gi
```

The shared-uploads workload, correctly on CephFS:

```yaml
# upload-writer.yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: shared-uploads
spec:
  accessModes: ["ReadWriteMany"]
  storageClassName: rook-cephfs
  resources:
    requests:
      storage: 1Gi
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: upload-writer
spec:
  replicas: 3
  selector:
    matchLabels: { app: upload-writer }
  template:
    metadata:
      labels: { app: upload-writer }
    spec:
      containers:
        - name: writer
          image: busybox:1.36
          command: ["sh", "-c", "while true; do echo \"$(hostname) $(date)\" >> /uploads/log.txt; sleep 5; done"]
          volumeMounts:
            - name: uploads
              mountPath: /uploads
      volumes:
        - name: uploads
          persistentVolumeClaim:
            claimName: shared-uploads
```

```bash
kubectl apply -f postgres.yaml
kubectl apply -f upload-writer.yaml

kubectl wait --for=condition=ready pod -l app=postgres --timeout=180s
kubectl wait --for=condition=ready pod -l app=upload-writer --timeout=180s --all=true

kubectl get pods -l app=upload-writer
```

Correct output — all three replicas running at once, which an RBD-backed
`ReadWriteOnce` claim cannot do:

```
NAME                             READY   STATUS    RESTARTS   AGE
upload-writer-7d4b9c8f6c-2k9pl   1/1     Running   0          40s
upload-writer-7d4b9c8f6c-8j2qw   1/1     Running   0          40s
upload-writer-7d4b9c8f6c-xk2p9   1/1     Running   0          40s
```

```bash
kubectl exec deploy/upload-writer -- tail -n 6 /uploads/log.txt
```

```
upload-writer-7d4b9c8f6c-2k9pl Thu Sep  3 10:14:05 UTC 2026
upload-writer-7d4b9c8f6c-8j2qw Thu Sep  3 10:14:06 UTC 2026
upload-writer-7d4b9c8f6c-xk2p9 Thu Sep  3 10:14:07 UTC 2026
upload-writer-7d4b9c8f6c-2k9pl Thu Sep  3 10:14:10 UTC 2026
upload-writer-7d4b9c8f6c-8j2qw Thu Sep  3 10:14:11 UTC 2026
upload-writer-7d4b9c8f6c-xk2p9 Thu Sep  3 10:14:12 UTC 2026
```

Three different pod hostnames writing into the same file from what may be three
different nodes is the proof: this is genuine concurrent shared access, not three pods
that happened to land on one node. Change `upload-writer.yaml`'s `storageClassName` to
`rook-ceph-block` and its access mode to `ReadWriteOnce`, re-apply, and `kubectl get
pods` reproduces the `Multi-Attach` failure from the opening of this article — the same
manifest, the same replica count, failing for exactly the reason described there.

## Conclusion

RBD and CephFS aren't a slower option and a faster option on the same axis — they answer
different questions, and the failure mode for asking the wrong one is different in each
direction: RBD fails loudly and immediately when asked for concurrency it structurally
cannot provide; CephFS fails quietly, by working, while trading away consistency
guarantees and latency a single-writer workload never needed to give up.

**Let the access pattern choose the provisioner, not the other way round.** "Does more
than one pod need to write to this concurrently" is the entire decision — once that's
answered, the `StorageClass` follows directly, and second-guessing it after seeing which
one is already configured tends to produce exactly the two failure modes above.

**A `ReadWriteOnce` failure is informative; a wrong-but-working setup is not.** The
`Multi-Attach` error is Kubernetes telling you the access pattern doesn't match the
storage's actual contract. A single-writer workload quietly running on a shared
filesystem gives you no such signal — only a latency and consistency profile that's
subtly worse than it should be, discovered later, usually under load.

**One Ceph cluster serving both is an operational win with an operational cost.** It
means one thing to run, back up and monitor instead of two storage systems — but it also
means MDS capacity for CephFS and OSD load from RBD now share the same cluster's
resources, and sizing that cluster has to account for both workloads' demands, not just
whichever one was provisioned first.
</content>
