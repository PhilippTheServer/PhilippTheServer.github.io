---
layout: post
title: "Capacity Planning Against the Fullest OSD, Not the Average"
subtitle: "Why a cluster at 70% average can still halt every write in the pool."
date: 2025-11-11 09:00:00 +0200
tags: [ceph, storage, performance, reliability]
description: >-
  Ceph's full_ratio applies per OSD, so placement variance means the fullest
  disk in a cluster can be far ahead of the cluster-wide average, and one OSD
  crossing that threshold stops writes cluster-wide. This works through why
  the variance exists, how to see it before it becomes an incident, and gives
  a script that alerts on the tail of the distribution instead of the mean.
---

## The problem

`ceph df` reports a single, reassuring number: the cluster is 70% full. Capacity planning
gets done against that number, replacement drives get ordered against that number, and the
next incident is a surprise: `ceph -s` reports `HEALTH_ERR`, client writes stop across
pools that appeared to have plenty of headroom, and `ceph health detail` names a single OSD
that has crossed `full_ratio` — the default is 0.95, i.e. 95% used.

This is not a bug in the reporting. `ceph df`'s cluster-wide percentage is an average
across every OSD's capacity. `full_ratio` is not applied to that average; it is applied to
each OSD individually, and Ceph stops accepting writes cluster-wide the moment *any* OSD
crosses it — not just writes destined for that OSD, because CRUSH could place any new
object's PG there. A cluster comfortably under its average capacity target can still have
one disk well past the danger line, and that one disk is enough to stop everything.

The gap between the average and the fullest disk comes from CRUSH itself. Placement is
pseudo-random and weighted, not perfectly even — the seed for each placement group's
mapping is effectively arbitrary with respect to how full any given OSD happens to be. Over
many placement groups this averages out, but "many" is doing real work in that sentence,
and with the PG counts most clusters actually run, the spread is large enough to matter.

It is easy to miss because the dashboards most teams look at first — `ceph df`, Prometheus
cluster-capacity panels — are built around the aggregate, and the aggregate is genuinely
useful for most decisions. It is simply the wrong number for the one decision that ends a
cluster's ability to accept writes at all.

## Working through it

### Why the variance is structural, not a symptom of imbalance

CRUSH (with the straw2 algorithm) selects OSDs for each placement group using a weighted
pseudo-random process. For a given set of weights, this converges to the correct
proportional distribution as the number of independent placements grows — but with a
finite number of PGs per OSD, there is real statistical variance around that average, and
it shrinks slowly, proportional to the square root of the number of PGs per OSD, not
linearly. Doubling PGs per OSD does not halve the variance; it takes four times as many to
do that.

### Making the variance visible without a cluster

The shape of the problem can be demonstrated without touching a running cluster, using the
same statistical process CRUSH relies on — weighted random assignment of placement groups
to OSDs — at a scale small enough to run instantly:

```python
#!/usr/bin/env python3
"""Simulate pseudo-random PG placement across OSDs of equal weight and
report the spread between the fullest and average OSD.

This mimics what CRUSH does at a statistical level (weighted random
placement of PGs onto OSDs) without needing a running cluster.
"""
import argparse
import random
import statistics


def simulate(num_osds: int, pgs_per_osd: int, replicas: int, seed: int) -> list[int]:
    rng = random.Random(seed)
    total_pgs = (num_osds * pgs_per_osd) // replicas
    counts = [0] * num_osds
    for _ in range(total_pgs):
        chosen = rng.sample(range(num_osds), replicas)
        for osd in chosen:
            counts[osd] += 1
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--osds", type=int, default=30)
    parser.add_argument("--pgs-per-osd", type=int, default=100)
    parser.add_argument("--replicas", type=int, default=3)
    parser.add_argument("--seed", type=int, default=1)
    args = parser.parse_args()

    counts = simulate(args.osds, args.pgs_per_osd, args.replicas, args.seed)
    mean = statistics.mean(counts)
    stdev = statistics.pstdev(counts)
    fullest = max(counts)

    print(f"OSDs: {args.osds}, target PGs/OSD: {args.pgs_per_osd}")
    print(f"mean PGs/OSD:    {mean:.1f}")
    print(f"stdev:           {stdev:.1f}")
    print(f"fullest OSD:     {fullest} PGs ({(fullest / mean - 1) * 100:+.1f}% vs mean)")


if __name__ == "__main__":
    main()
```

```
$ python3 simulate_placement.py --osds 30 --pgs-per-osd 100
OSDs: 30, target PGs/OSD: 100
mean PGs/OSD:    100.0
stdev:           9.7
fullest OSD:     122 PGs (+22.0% vs mean)
```

At 100 PGs per OSD — a commonly used target, and within Ceph's own recommended range —
this run's fullest OSD carries 22% more placement groups than the mean. Run it again with
`--pgs-per-osd 30` and the spread widens further; run it with `--pgs-per-osd 300` and it
narrows, but does not disappear. PG count is not free (each one costs peering and
recovery overhead), so there is a practical floor below which this variance cannot be
engineered away by adding more PGs.

### Reading the actual tail, not the aggregate

`ceph osd df` reports per-OSD utilisation, and its JSON form is easy to script against —
either against a live cluster or, for testing the check itself, against a saved sample:

```json
{
  "nodes": [
    {"id": 0, "device_class": "hdd", "name": "osd.0", "kb": 1953514584, "kb_used": 1245830000, "kb_avail": 707684584, "utilization": 63.77, "pgs": 118, "status": "up"},
    {"id": 1, "device_class": "hdd", "name": "osd.1", "kb": 1953514584, "kb_used": 1690214000, "kb_avail": 263300584, "utilization": 86.52, "pgs": 141, "status": "up"},
    {"id": 2, "device_class": "hdd", "name": "osd.2", "kb": 1953514584, "kb_used": 1102040000, "kb_avail": 851474584, "utilization": 56.41, "pgs": 97, "status": "up"},
    {"id": 3, "device_class": "hdd", "name": "osd.3", "kb": 1953514584, "kb_used": 1201880000, "kb_avail": 751634584, "utilization": 61.53, "pgs": 109, "status": "up"}
  ]
}
```

Here the cluster-wide average utilisation is comfortably under 70%, and one OSD is already
at 86.5% — closer to the 95% `full_ratio` than the average suggests, and the number that
actually predicts the next outage.

### What to change operationally, not just what to watch

Ceph's own `mgr` balancer module (`upmap` mode) actively reduces this variance by
overriding individual PG mappings, rather than only reporting it:

```bash
ceph balancer mode upmap
ceph balancer on
```

This is the operational fix. The alerting change below is what buys the time to notice a
problem before the balancer catches up or before a genuine capacity shortfall develops —
they are complementary, not alternatives.

## The solution

A monitoring check that reads `ceph osd df -f json`, from a live cluster or from a saved
file, and fails if the fullest OSD's headroom to `full_ratio` drops below a configurable
margin:

```python
#!/usr/bin/env python3
"""Check the fullest OSD in a Ceph cluster against the full_ratio headroom.

Reads `ceph osd df -f json` output, either piped in or from a file, and
exits non-zero if any OSD is within `--margin` percentage points of
`--full-ratio`. Designed to run as a cron or monitoring check:

    ceph osd df -f json | ./check_fullest_osd.py --full-ratio 0.95 --margin 10
    ./check_fullest_osd.py --full-ratio 0.95 --margin 10 osd_df_sample.json
"""
import argparse
import json
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", nargs="?", help="JSON file; omit to read stdin")
    parser.add_argument("--full-ratio", type=float, default=0.95)
    parser.add_argument("--margin", type=float, default=10.0,
                         help="warn if headroom to full-ratio is under this many points")
    args = parser.parse_args()

    text = open(args.source).read() if args.source else sys.stdin.read()
    data = json.loads(text)

    full_pct = args.full_ratio * 100
    nodes = [n for n in data["nodes"] if n.get("status") == "up"]
    fullest = max(nodes, key=lambda n: n["utilization"])

    headroom = full_pct - fullest["utilization"]
    print(f"fullest OSD: {fullest['name']} at {fullest['utilization']:.2f}% "
          f"(full_ratio is {full_pct:.0f}%, headroom {headroom:.2f} points)")

    if headroom < args.margin:
        print(f"WARNING: headroom below margin of {args.margin} points", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

```
$ python3 check_fullest_osd.py --full-ratio 0.95 --margin 10 osd_df_sample.json
fullest OSD: osd.1 at 86.52% (full_ratio is 95%, headroom 8.48 points)
WARNING: headroom below margin of 10.0 points
$ echo $?
1
```

`full_ratio`, along with `nearfull_ratio` (default 0.85, triggers `HEALTH_WARN`) and
`backfillfull_ratio` (default 0.90, blocks backfill to that OSD), are all live-tunable via
`ceph osd set-full-ratio` and equivalents, but changing the thresholds is not a substitute
for alerting on the actual tail of the distribution — the point of this check is to fire
before any of those thresholds are crossed for real.

## Conclusion

Capacity planning that budgets against the cluster-wide average is budgeting against a
number that placement variance guarantees no single OSD will match — some will always run
ahead of it, and Ceph's write-availability guarantee depends on the worst one, not the
mean.

Increasing PGs per OSD narrows the spread but only as the square root of the increase, and
runs into peering and recovery costs of its own — this is a trade-off to tune deliberately,
not a dial to max out.

The `balancer` module in `upmap` mode is the actual operational fix for the variance
itself; per-OSD headroom alerting is what catches the case the balancer has not gotten to
yet, and it is worth having both rather than treating either as sufficient on its own.
