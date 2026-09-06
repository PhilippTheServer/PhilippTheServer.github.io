---
layout: post
title: "A Layered Storage Benchmark: Isolating Device, Network and Protocol Bottlenecks"
subtitle: "Four numbers instead of one, so the bottleneck is a layer, not a guess."
date: 2025-10-31 09:00:00 +0200
tags: [ceph, storage, testing, performance]
description: >-
  A single throughput number from an RBD volume or a CephFS mount hides
  whether the limit is a slow disk, a saturated replication network, or
  overhead in Ceph's own protocol path, and each of those has a completely
  different fix. This builds a benchmark methodology that measures the
  device, the network and the protocol separately before measuring the full
  stack, using fio, iperf3 and rados bench against a single-node Ceph
  container a reader can run without a real cluster.
---

## The problem

"RBD gives us 150 MB/s" is a measurement of the entire path from an application, through
the kernel RBD client or librbd, across the network to every OSD holding a copy of the
data, through each OSD's own I/O path, down to the physical device, and back. Four
completely different things could be responsible for that number: the underlying disks
are slow, the network between nodes is saturated, Ceph's own replication and
acknowledgement protocol adds overhead the raw numbers don't show, or the client-side
path (RBD caching, queue depth, block size) is the actual limit.

Chasing the wrong one wastes real time. Someone reads 150 MB/s, assumes the disks are the
problem, and spends a week justifying a hardware upgrade — when the disks were never the
bottleneck, and the actual limit was a replication network running at a tenth of the
device's raw throughput because of a bad NIC negotiation. The single number cannot tell
you this; it can only tell you that something, somewhere in the stack, capped the result.

The fix is not a better single benchmark. It is benchmarking each layer in isolation,
bottom to top, so each number has exactly one thing it could plausibly be measuring.

## Working through it

### Building the stack bottom to top, on purpose

1. **Device.** `fio` directly against the raw block device or a file on the OSD's
   filesystem, on one node, with no network and no Ceph in the path at all. This is the
   ceiling — nothing above this layer can go faster than this number.
2. **Network.** `iperf3` between the nodes that will carry Ceph's cluster (replication)
   traffic. This is the second ceiling: if the network is slower than the device, the
   network is now the limit regardless of how fast the disks are.
3. **Protocol.** `rados bench`, writing and reading objects directly against a pool, with
   no filesystem or block device abstraction on top. This measures Ceph's own client
   protocol and replication overhead, isolated from anything a specific consumer (RBD,
   CephFS, RGW) adds on top.
4. **Full stack.** `fio` against a mounted RBD image or CephFS, which is finally the
   number that matches what an application actually experiences.

Each layer's result should be less than or equal to the layer below it. When it isn't
(full-stack faster than protocol, say), that's a sign of client-side caching giving a
number that doesn't reflect durable throughput, not a sign the methodology is wrong — call
that out explicitly rather than reporting it as a clean result.

### Reading the four numbers together

If the device number and the network number are both comfortably above the rados bench
number, the overhead is in Ceph's own protocol or cluster-side placement — worth
investigating OSD CPU usage, replication factor, and whether the pool's PG count is
reasonable for its OSD count. If the network number is close to the rados bench number and
both are well below the device number, the network is the actual limit, and no amount of
OSD tuning will move the full-stack number. If the full-stack number is far below the
rados bench number, the client path (RBD queue depth, cache settings, the specific `fio`
parameters used against the mount) is where to look next.

### The caveat a single-node test cannot avoid

A genuinely useful network-layer measurement needs at least two physically or virtually
separate hosts with a real link between them — on one host, `iperf3` between two
containers measures the loopback interface or a virtual bridge, not the network Ceph's
replication traffic will actually cross in production, and reporting it as if it were the
same thing is the kind of dishonesty this whole methodology exists to prevent. The
reproducible example below runs a single-node Ceph demo container, which is enough to
prove the *four-command sequence and how to read the results together*, but the network
number it produces is not meaningful the way it would be with real inter-node hardware.
Say this out loud in any real write-up rather than letting a green single-node run imply
more than it measured.

## The solution

A single-node Ceph cluster, via the demo container the Ceph project publishes for exactly
this kind of testing:

```bash
mkdir -p /tmp/ceph-demo/etc /tmp/ceph-demo/lib
docker run -d --name ceph-demo --net=host --privileged \
  -v /tmp/ceph-demo/etc:/etc/ceph \
  -v /tmp/ceph-demo/lib:/var/lib/ceph \
  -e MON_IP=127.0.0.1 \
  -e CEPH_PUBLIC_NETWORK=127.0.0.1/32 \
  -e CEPH_DEMO_UID=demo \
  -e CEPH_DEMO_ACCESS_KEY=demo \
  -e CEPH_DEMO_SECRET_KEY=demo \
  -e DEMO_DAEMONS="mon mgr osd" \
  quay.io/ceph/demo:latest-reef

# wait for the cluster to settle
docker exec ceph-demo ceph -s
#   cluster:
#     health: HEALTH_OK
#   services:
#     mon: 1 daemons, quorum ceph-demo
#     mgr: ceph-demo(active)
#     osd: 1 osds: 1 up, 1 in
```

### Layer 1: the device, with fio

```ini
; device.fio
[global]
ioengine=libaio
direct=1
runtime=30
time_based=1
group_reporting=1

[device-write]
rw=write
bs=4k
iodepth=16
size=1G
directory=/tmp/ceph-demo/lib/osd/ceph-0/fio-test
filename=device-test-file
```

```bash
mkdir -p /tmp/ceph-demo/lib/osd/ceph-0/fio-test
docker run --rm -v /tmp/ceph-demo/lib:/var/lib/ceph -v "$PWD/device.fio:/device.fio" \
  --entrypoint fio ceph/fio:latest /device.fio
# WRITE: bw=210MiB/s (220MB/s), 210MiB/s-210MiB/s, io=6300MiB (6606MB), run=30001-30001msec
```

This is the ceiling: nothing built on this device, through any protocol, will sustain more
than roughly this number.

### Layer 2: the network, with iperf3

```bash
# On a real second host (or accept the single-node caveat above):
docker run --rm -d --net=host --name iperf-server networkstatic/iperf3 -s
docker run --rm --net=host networkstatic/iperf3 -c 127.0.0.1 -t 10
# [ ID] Interval           Transfer     Bitrate
# [  5]   0.00-10.00  sec  11.4 GBytes  9.83 Gbits/sec
docker stop iperf-server
```

On loopback this number is essentially meaningless as a ceiling — it reflects the kernel's
loopback path, not a NIC, a switch, or a cable. Run this step between two real hosts to get
a number worth comparing against the device layer.

### Layer 3: the protocol, with rados bench

```bash
docker exec ceph-demo ceph osd pool create bench-pool 32 32
docker exec ceph-demo rados bench -p bench-pool 30 write --no-cleanup
#  Total time run:         30.05
#  Total writes made:      1284
#  Write size:             4194304
#  Bandwidth (MB/sec):     170.9
#  Average Latency(s):     0.374

docker exec ceph-demo rados bench -p bench-pool 30 seq
#  Bandwidth (MB/sec):     205.3
docker exec ceph-demo rados cleanup -p bench-pool
```

### Layer 4: the full stack, with fio against an RBD image

```bash
docker exec ceph-demo rbd create bench-image --size 4096 --pool bench-pool
docker exec ceph-demo rbd map bench-image --pool bench-pool
# /dev/rbd0

docker run --rm --privileged -v /dev:/dev -v "$PWD/device.fio:/device.fio" \
  --entrypoint fio ceph/fio:latest \
  --name=rbd-test --filename=/dev/rbd0 --rw=write --bs=4k --iodepth=16 \
  --ioengine=libaio --direct=1 --runtime=30 --time_based=1 --group_reporting
# WRITE: bw=98.2MiB/s (103MB/s), 98.2MiB/s-98.2MiB/s, io=2946MiB (3089MB), run=30004-30004msec

docker exec ceph-demo rbd unmap /dev/rbd0
```

### Reading this specific run

Device: ~210 MB/s. Protocol (`rados bench` write): ~171 MB/s — already below the device
ceiling, consistent with replication acknowledgement overhead on a single-OSD pool with
default replica settings applied loosely. Full stack (RBD): ~103 MB/s — noticeably below
the protocol layer, pointing at the client path (small `fio` queue depth relative to what
the kernel RBD client can pipeline, or cache settings) as the next thing to tune, not the
OSD or the disk. On a single-node setup the network layer cannot meaningfully confirm or
rule anything out; on real hardware, comparing the network number against the ~171 MB/s
protocol number is what would tell you whether the network or the cluster's own overhead
explains that first drop.

## Conclusion

The benchmark is only useful if the four numbers are kept separate and compared in order.
Averaging them, or reporting only the last one, throws away exactly the information that
made the exercise worth doing.

Three points generalise past Ceph specifically:

**Any layered system deserves a benchmark of the same shape.** The same bottom-to-top
approach — raw resource, transport, protocol, full stack — applies to a database behind a
network filesystem, a message queue behind a service mesh, or an object store behind a
CDN; the specific tools change, the structure does not.

**Each layer's number is a ceiling for everything above it.** The moment a higher layer
reports a number close to a lower layer's ceiling, that lower layer is worth investigating
first, because nothing above it can exceed what it measured.

**Say what a test environment cannot prove.** A single-node cluster proves the methodology
and the command sequence; it does not prove anything about network behaviour, and
reporting its network number as if it reflected real inter-node hardware would make the
whole result less trustworthy, not more complete.
