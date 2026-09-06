---
layout: post
title: "CRUSH Hybrid Rules: Pinning the Read Primary to SSD Without Rebalancing"
subtitle: "Placing one replica on flash and the rest on spinning disk with a two-step CRUSH rule."
date: 2025-11-07 09:00:00 +0200
tags: [ceph, storage, performance]
description: >-
  Metadata-heavy pools suffer when their primary replica sits on a spinning
  disk, but re-pointing the whole pool at SSD means paying for all-flash
  capacity and triggering a full rebalance. This shows how to write a CRUSH
  rule that places only the primary on SSD and the remaining replicas on HDD,
  and how to test it with crushtool before touching a live map.
---

## The problem

A CephFS metadata pool, or an RGW bucket index pool, is small but latency-sensitive: every
metadata read and write goes through it, and in a replicated pool every one of those ops
goes through the primary OSD first. If the primary happens to sit on a spinning disk, its
seek latency becomes the floor for every metadata operation in the cluster, no matter how
fast the replicas are.

The obvious fix — move the pool's CRUSH rule to select only SSD-class OSDs — works, but it
changes every replica for every PG in the pool. Ceph treats a CRUSH rule change like any
other change to placement: it recomputes the up set for every PG against the new rule, and
any PG whose replicas moved has to backfill. For a pool with real data, that is a
cluster-wide data migration, and it means buying enough SSD capacity to hold the entire
pool three times over (for the default replication factor), not just enough to hold one
replica's worth.

`osd primary-affinity` looks like it should help — it lets you tell Ceph "prefer not to
make this OSD primary" — but affinity only reorders *already-placed* replicas. If none of
a PG's three replicas is on SSD, adjusting affinity has nothing to work with. The disk that
matters was never a candidate.

What is actually needed is asymmetric placement: exactly one replica constrained to SSD,
the rest constrained to HDD, decided at CRUSH time rather than after the fact. CRUSH's
default `chooseleaf` step does not do this — it selects N replicas from a single device
class in one pass. Getting an asymmetric result means writing a rule with two separate
selection passes.

## Working through it

### How the up set decides who is primary

For a PG, CRUSH produces an ordered list of OSDs — the up set — and, barring a primary-
affinity override, the first entry in that list is the primary. The order comes directly
from the sequence of `take`/`chooseleaf`/`emit` steps in the rule: whatever a rule selects
first becomes eligible to be first in the list. This means the primary's device class is a
property of the rule's structure, not something tuned separately from placement.

### Writing a two-step rule

CRUSH rules are a sequence of steps executed in order, and a rule can `take` a different
root or device class more than once, accumulating selections across separate `emit`
statements:

```
rule mixed_replicated_rule {
        id 11
        type replicated
        step take default class ssd
        step chooseleaf firstn 1 type host
        step emit
        step take default class hdd
        step chooseleaf firstn 0 type host
        step emit
}
```

The first block takes the SSD-class subtree of the `default` root and chooses exactly one
leaf (`firstn 1`) at the `host` failure domain — this becomes the first, and therefore
primary, OSD. The second block takes the HDD-class subtree and chooses `firstn 0`, which
in CRUSH's rule language means "as many as the pool's size requires, minus what has
already been chosen" — so for a 3x replicated pool this picks the remaining two replicas
from HDD hosts.

The `type host` failure domain applies independently to each block, so the SSD pick and
the two HDD picks are not forced onto separate hosts from each other by this rule alone —
each block enforces host-level separation only among its own selections. In practice this
is normally fine because the SSD and HDD device classes are already on physically
different hosts in a hybrid cluster, but it is worth checking `ceph osd tree` for the
actual host layout rather than assuming it.

### Testing a rule before it touches a live map

CRUSH maps can be edited and tested entirely offline with `crushtool`, which compiles,
decompiles and simulates placement without a running cluster:

```bash
# Pull the live map, decompile it to text, or start from a synthetic one.
ceph osd getcrushmap -o live.bin
crushtool -d live.bin -o live.txt

# Edit live.txt: add the mixed_replicated_rule block above.

# Compile the edited text back to binary.
crushtool -c live.txt -o edited.bin

# Simulate placement against the new rule without touching the cluster.
crushtool -i edited.bin --test --rule 11 --num-rep 3 \
  --show-mappings --min-x 0 --max-x 20
```

`--show-mappings` prints, for each simulated input (`x`), the OSD list CRUSH would
produce. The first OSD in each line is the one that would become primary; confirming it is
always drawn from the SSD device class, and the remaining two are always HDD, is the whole
test. This catches a rule that accidentally allows the HDD block to pick an SSD OSD (or
vice versa) before it can affect a single PG.

### Accepting the rebalance this does cause

This is not free. Changing a pool's `crush_rule` still recomputes every PG's up set, and
the primary — one out of three replicas — moves for every PG in the pool. That is real
data movement. The point of the hybrid rule is not to avoid rebalancing altogether; it is
to shrink the migration from "the whole pool times its replication factor" to "one replica
per PG", which is the difference between an operation that fits in spare SSD capacity and
one that does not.

## The solution

A minimal, fully offline demonstration using a synthetic CRUSH map — no live cluster
required to prove the rule behaves correctly:

```
# hybrid.txt
tunable choose_local_tries 0
tunable choose_local_fallback_tries 0
tunable choose_total_tries 50
tunable chooseleaf_descend_once 1
tunable straw_calc_version 1

device 0 osd.0 class ssd
device 1 osd.1 class hdd
device 2 osd.2 class hdd
device 3 osd.3 class ssd
device 4 osd.4 class hdd
device 5 osd.5 class hdd

host node-a {
        id -2
        alg straw2
        hash 0
        item osd.0 weight 1.00
        item osd.1 weight 1.00
}
host node-b {
        id -3
        alg straw2
        hash 0
        item osd.2 weight 1.00
        item osd.3 weight 1.00
}
host node-c {
        id -4
        alg straw2
        hash 0
        item osd.4 weight 1.00
        item osd.5 weight 1.00
}

root default {
        id -1
        alg straw2
        hash 0
        item node-a weight 2.00
        item node-b weight 2.00
        item node-c weight 2.00
}

rule mixed_replicated_rule {
        id 11
        type replicated
        step take default class ssd
        step chooseleaf firstn 1 type host
        step emit
        step take default class hdd
        step chooseleaf firstn 0 type host
        step emit
}
```

```bash
crushtool -c hybrid.txt -o hybrid.bin
crushtool -i hybrid.bin --test --rule 11 --num-rep 3 \
  --show-mappings --min-x 0 --max-x 10
```

Each output line lists three OSDs per simulated PG; the first OSD is always one of the
`ssd`-class devices (0 or 3), and the remaining two are drawn from the `hdd`-class devices,
confirming the rule's intent before it is applied anywhere real.

Applying it to a live cluster is the standard CRUSH map update sequence, followed by
pointing the pool at the new rule:

```bash
ceph osd getcrushmap -o live.bin
crushtool -d live.bin -o live.txt
# add the mixed_replicated_rule block to live.txt
crushtool -c live.txt -o edited.bin
ceph osd setcrushmap -i edited.bin

ceph osd pool set cephfs_metadata crush_rule mixed_replicated_rule
```

`ceph osd pool set ... crush_rule` triggers backfill for the affected pool only; watch it
with `ceph -s` and `ceph osd pool stats`.

## Conclusion

`osd primary-affinity` and CRUSH placement solve different problems: affinity chooses
among replicas that already exist, and cannot compensate for a device class that was never
selected in the first place. When the goal is a specific device class for a specific
replica, that has to be expressed in the rule itself.

CRUSH rules are not restricted to uniform selection — a rule is a sequence of independent
`take`/`choose`/`emit` blocks, and each block can constrain a different device class,
which is what makes asymmetric replica placement possible without inventing a new feature.

`crushtool` turns "I think this rule does what I want" into something verified before it
touches a running map: it compiles, decompiles and simulates placement standalone, so a
mistake in the rule shows up as an obviously wrong `--show-mappings` output rather than as
a live migration to the wrong device class.
