---
layout: post
title: "SMR Drives in a Replicated Cluster: Finding the Disk That Ruins the Pool"
subtitle: "Why inventory and SMART data cannot tell you which drive will collapse under load."
date: 2025-11-04 09:00:00 +0200
tags: [ceph, storage, embedded, linux]
description: >-
  Drive-managed SMR disks report the same capacity, connector and SMART
  attributes as conventional drives, then fall off a latency cliff once their
  persistent cache is exhausted by sustained random writes. This walks through
  why that pattern matches exactly what a Ceph OSD does to its backing disk,
  and gives a reproducible benchmark that catches the problem before the disk
  is holding data.
---

## The problem

A batch of 3.5" drives arrives. `lsblk` shows the right capacity. `smartctl -a` shows a
clean health report and a rotational spindle. Nothing in the inventory distinguishes them
from the model they replaced. Three weeks after they go into a replicated pool as OSD
backing stores, client write latency on that pool climbs into the seconds, `ceph -s`
starts reporting slow requests, and the OSDs on the new drives flap under load that the
old drives handled without complaint.

The drives are shingled magnetic recording (SMR), and specifically the drive-managed
variant (DM-SMR), which is the kind that is hardest to catch because it is designed to be
invisible to the host.

Conventional recording (CMR) writes non-overlapping tracks, so any sector can be
rewritten independently. SMR overlaps tracks like roof shingles to pack more capacity
into the same platter area, which means writing one track can clobber the edge of its
neighbour. Host-managed and host-aware SMR expose this as zones and require (or allow)
the host to write sequentially within a zone. Drive-managed SMR does not expose any of
this: the drive presents an ordinary block device, translates random writes into
sequential zone writes internally, and buffers incoming writes in a small conventional
(CMR) cache region while a background process migrates them into shingled zones.

That cache region is the whole story. While writes land in the CMR cache, the drive
performs like any other disk. Once the cache fills and the drive has to service writes by
reading a shingled band, modifying it in memory and rewriting the whole band, throughput
falls by an order of magnitude and latency becomes wildly inconsistent. A short benchmark
never fills the cache, so it never sees this. A Ceph OSD's backing store does, because
BlueStore's write path — the WAL, deferred writes, and periodic RocksDB compaction — is
sustained, small, and semi-random for as long as the OSD is in service. It is close to
the worst-case access pattern for a DM-SMR translation layer.

This is why the failure is easy to miss during procurement and easy to misdiagnose in
production: it requires both sustained duration and a particular I/O pattern to appear,
and once it appears it looks like a generic "slow disk" or a network problem rather than
an architectural mismatch between the drive and the workload.

## Working through it

### Why a replicated pool amplifies one slow disk

In a replicated pool, the primary OSD for a PG applies the write locally and forwards it
to the replicas, then acknowledges the client only once all replicas have committed (for
the default durability settings). The commit latency for that PG is bounded by the
slowest replica, not the average. A single DM-SMR OSD, once its cache is exhausted, does
not just get slightly slower — it can be tens of times slower than the CMR drives beside
it. Every PG that has an acting set including that OSD inherits its latency, and if enough
PGs land there, the effect is visible cluster-wide even though only one drive misbehaves.

### Why the standard detection check does not work here

Recent Linux kernels expose zoned-device information for host-managed and host-aware
drives:

```bash
lsblk -o NAME,ROTA,ZONED,MODEL
```

A host-managed SMR drive reports `ZONED=host-managed`. This is a genuinely useful check —
run it — but it only catches the honest case. Drive-managed SMR exists specifically to
present a standard block interface, so it reports `ZONED=none`, identically to a CMR
drive. There is no reliable field in `lsblk`, `smartctl`, or `hdparm -I` that
distinguishes a DM-SMR drive from a CMR drive of the same interface. Manufacturer
datasheets are the only fully reliable source, and for several years around 2020 several
vendors did not disclose which models used DM-SMR, which is why community-maintained
model lists exist and are worth checking before a purchase — but a benchmark that does
not depend on the vendor telling the truth is worth more than any list.

### Building a benchmark that forces the cache to empty

The test has to run long enough, and write enough data, to exhaust the drive's CMR cache
and observe what happens after. Drive-managed SMR cache sizes vary by model and are
rarely published, so the practical approach is to write substantially more than any
plausible cache size (tens of gigabytes) over a sustained period, using a pattern close to
what an OSD actually does: small, random, direct (bypassing the page cache so the test
measures the device, not RAM).

```ini
# smr_probe.fio
[global]
ioengine=libaio
direct=1
bs=4k
rw=randwrite
iodepth=32
runtime=1800
time_based
log_unit=usec
write_lat_log=smr_probe
log_avg_msec=1000

[probe]
filename=/dev/sdX
size=20G
```

Replace `/dev/sdX` with the candidate drive. This is destructive to whatever is on that
device — run it against a spare disk before it is provisioned, never against a disk
already carrying data. Thirty minutes is long enough to expose a cache in the low tens of
gigabytes; drives with a larger cache need a longer run or a larger `size`.

### Reading the result as a cliff, not an average

A single average throughput or latency number hides the cliff, because it blends the
healthy first minutes with the degraded remainder. The useful comparison is the tail
latency at the start of the run against the tail latency at the end.

```python
#!/usr/bin/env python3
"""Detect an SMR-style write cliff in an fio completion-latency log.

fio's --write_lat_log produces lines of:
    time_ms,latency_usec,ddir,block_size,offset

This compares the p99 write latency in the first and last `--window-s`
seconds of the run. A drive-managed SMR disk whose persistent cache has
been exhausted shows a p99 an order of magnitude higher at the end than
at the start; a CMR disk stays roughly flat.

    fio smr_probe.fio
    ./detect_cliff.py smr_probe_clat.1.log --window-s 300 --threshold 5
"""
import argparse
import statistics
import sys


def p99(values: list[int]) -> float:
    return statistics.quantiles(values, n=100)[98]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("logfile")
    parser.add_argument("--window-s", type=int, default=300)
    parser.add_argument("--threshold", type=float, default=5.0,
                         help="flag a cliff if end p99 / start p99 exceeds this")
    args = parser.parse_args()

    window_ms = args.window_s * 1000
    start, end = [], []
    with open(args.logfile) as f:
        rows = [line.strip().split(",") for line in f if line.strip()]
    last_t = int(rows[-1][0])

    for t_ms, lat_usec, ddir, *_ in rows:
        t_ms, lat_usec = int(t_ms), int(lat_usec)
        if t_ms <= window_ms:
            start.append(lat_usec)
        if t_ms >= last_t - window_ms:
            end.append(lat_usec)

    start_p99, end_p99 = p99(start), p99(end)
    ratio = end_p99 / start_p99
    print(f"p99 latency, first {args.window_s}s:  {start_p99:.0f} us")
    print(f"p99 latency, last {args.window_s}s:   {end_p99:.0f} us")
    print(f"ratio: {ratio:.1f}x")

    if ratio >= args.threshold:
        print(f"CLIFF DETECTED: ratio >= {args.threshold}x threshold", file=sys.stderr)
        return 1
    print("no cliff within threshold")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

Run it on a synthetic log to see the shape it is looking for before pointing it at a real
drive: a log where the first ten minutes hold latencies in the hundreds of microseconds
and the last ten minutes hold latencies in the tens of milliseconds produces a ratio in
the tens, comfortably over the default threshold of 5x. A genuine CMR drive under the same
job produces a ratio close to 1.

## The solution

The complete qualification procedure, meant to run against every new drive model before
it is trusted with production data:

```bash
#!/usr/bin/env bash
# qualify_drive.sh — run before a new drive model goes into a pool.
# Usage: ./qualify_drive.sh /dev/sdX
set -euo pipefail

DEVICE="$1"
JOB=$(mktemp)
LOGDIR=$(mktemp -d)

trap 'rm -f "$JOB"; rm -rf "$LOGDIR"' EXIT

cat > "$JOB" <<EOF
[global]
ioengine=libaio
direct=1
bs=4k
rw=randwrite
iodepth=32
runtime=1800
time_based
log_unit=usec
write_lat_log=${LOGDIR}/probe
log_avg_msec=1000

[probe]
filename=${DEVICE}
size=20G
EOF

echo "Zoned-device check (catches host-managed/host-aware only):"
lsblk -o NAME,ROTA,ZONED,MODEL "$DEVICE"

echo "Running 30-minute sustained random-write probe against ${DEVICE}..."
fio "$JOB"

python3 detect_cliff.py "${LOGDIR}/probe_clat.1.log" --window-s 300 --threshold 5
```

Both `detect_cliff.py` above and this wrapper are complete and depend only on `fio` and a
Python 3 standard library — no cluster required to run the qualification step.

If a drive fails this check, it is not a candidate for OSD duty in a replicated pool
regardless of price or nominal capacity, and it is worth checking whether the model
appears in a community-maintained DM-SMR list so future purchases can be filtered before
they arrive.

## Conclusion

Inventory data describes what a drive claims to be, not how it behaves under the access
pattern that matters. A capacity figure, a SMART report and even the kernel's own zoned-
device attribute are all silent about drive-managed SMR, because that is what drive-managed
means: the translation is hidden by design, and the host has no reliable way to ask.

The only trustworthy test is one that reproduces the failure condition — sustained,
direct, small, semi-random writes for long enough to exhaust an unknown-sized cache — and
looks at the tail of the latency distribution over time rather than an average across the
whole run. A short or sequential benchmark will pass a drive that later ruins a pool.

In a replicated system, this is not a capacity-planning problem, it is a latency-budget
problem: the slowest member of an acting set sets the commit time for every write that
touches it, so one wrongly-qualified drive model can dominate cluster-wide latency long
before it dominates cluster-wide capacity.
