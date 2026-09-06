---
layout: post
title: "Tuning Ceph Recovery: The Trade Between Degraded Time and Client Latency"
subtitle: "Choosing an mClock profile deliberately instead of inheriting whatever ships by default."
date: 2025-11-14 09:00:00 +0200
tags: [ceph, storage, performance, reliability]
description: >-
  Recovery I/O competes with client I/O on the same disks exactly when
  hardware is already reduced, and the wrong balance either starves
  applications or leaves the cluster degraded for longer than the next
  failure can wait. This explains how the mClock scheduler's profiles
  replace the old manual throttles, and gives a reproducible cephadm setup
  for measuring the trade-off directly.
---

## The problem

An OSD or a whole host fails. Ceph starts recovering redundancy for every PG that lost a
replica, which means reading surviving copies and writing new ones — on the same disks and
the same network links that client I/O uses. This happens at the worst possible time:
capacity and I/O headroom are already reduced by the failure, and now a second workload is
competing for what is left.

Throttle recovery too aggressively and applications see it as an outage — write latency
climbs, timeouts start, and a routine disk replacement looks to on-call like a cluster-wide
incident. Throttle it too gently and the cluster spends longer in a degraded state, during
which a second failure — on a different disk holding one of the same PGs — can mean an
unrecoverable object rather than an inconvenience. There is no setting that avoids this
trade-off; there is only a decision about which side to lean on, made either deliberately
or by accident.

It is easy to make this decision by accident, because it looks like a solved problem.
Since the Quincy release, Ceph's default OSD operation scheduler is mClock, which promises
built-in QoS between client and recovery I/O without hand-tuned throttles. That is true as
far as it goes — but mClock ships with a default profile (`balanced`), and "balanced" is
itself a choice about the client/recovery trade-off, not a resolution of it. A cluster
running the default without anyone having chosen it is one operational-incident-shaped
surprise away from someone tuning `osd_max_backfills` by hand, discovering under mClock it
does nothing, and being confused about why.

## Working through it

### Two schedulers, two different sets of knobs

Ceph has carried two OSD operation schedulers. The older one (`wpq`, weighted priority
queue) is throttled with manual, relative knobs: `osd_max_backfills` (how many backfill
operations an OSD runs concurrently), `osd_recovery_max_active` (concurrent recovery
operations), `osd_recovery_op_priority`, and `osd_recovery_sleep` (a fixed delay inserted
between recovery operations, still useful as a blunt brake even under mClock).

The current default, `mclock_scheduler`, works differently: it allocates I/O capacity
between client ops, recovery/backfill, and scrub according to a cost model and a chosen
*profile*, rather than a set of independent relative priorities. Under mClock, most of the
old manual knobs are overridden to values the profile has already decided on —
`osd_max_backfills` and `osd_recovery_max_active` among them — and changing them has no
effect unless `osd_mclock_override_recovery_settings` is explicitly set, which the
documentation itself recommends against doing casually.

### Choosing a profile instead of a value

The built-in profiles are:

- `balanced` — the default; a middle ground between client and recovery I/O.
- `high_client_ops` — prioritises client latency, recovery proceeds more slowly.
- `high_recovery_ops` — prioritises finishing recovery, at a real cost to client latency
  during the window it runs.

The profile is a cluster-wide (or per-OSD) config value, changeable at runtime:

```bash
ceph config set osd osd_mclock_profile high_recovery_ops
```

The choice depends on what actually failed and what is actually at risk. A single disk
replacement in an otherwise healthy, low-utilisation cluster is a reasonable case for
leaving the default alone — the redundancy loss is small and recovery is naturally cheap. A
whole host failure that drops a pool's replication factor by a third, in a cluster that is
already busy, is a reasonable case for `high_recovery_ops` — the risk being managed is a
second failure during an unusually long exposure window, and it is worth paying client
latency to shorten that window. Be honest with whoever owns the affected applications that
this is the trade being made; `high_recovery_ops` is not a free performance setting, it
takes client latency to give recovery bandwidth.

### Proving the effect rather than assuming it

The only way to know what a profile actually costs a given cluster's hardware is to
measure it, because the right answer depends on disk speed, network headroom and how busy
the cluster already is. This is reproducible on a single disposable host with `cephadm`,
using only public container images:

```bash
# Bootstrap a single-node cluster (adjust the IP to the host running this).
cephadm bootstrap --mon-ip 10.0.0.10 --allow-fqdn-hostname

# Add OSDs from spare/loopback devices as needed, e.g.:
ceph orch daemon add osd node-a:/dev/sdb
ceph orch daemon add osd node-a:/dev/sdc
ceph orch daemon add osd node-a:/dev/sdd

ceph osd pool create bench-pool 32 32
```

## The solution

A script that forces a backfill by taking an OSD out, measures client-facing write latency
under each mClock profile while that backfill runs, and prints a comparison:

```bash
#!/usr/bin/env bash
# compare_mclock_profiles.sh
# Forces recovery by marking one OSD out, then measures rados bench write
# latency under each mClock profile while that recovery runs.
# Usage: ./compare_mclock_profiles.sh <osd-id-to-take-out> <pool>
set -euo pipefail

OSD_ID="$1"
POOL="$2"
PROFILES=(balanced high_client_ops high_recovery_ops)

for profile in "${PROFILES[@]}"; do
  echo "=== profile: ${profile} ==="
  ceph config set osd osd_mclock_profile "${profile}"

  # Re-trigger backfill so this profile is measured against a comparable
  # amount of recovery work each time.
  ceph osd in "osd.${OSD_ID}"
  sleep 5
  ceph osd out "osd.${OSD_ID}"

  echo "waiting for backfill to start..."
  until ceph -s | grep -q "backfilling"; do sleep 2; done

  echo "client latency while backfill is active:"
  rados bench -p "${POOL}" 30 write --no-cleanup -t 16 | grep -E "Average Latency|Stddev Latency"

  ceph osd in "osd.${OSD_ID}"
  echo "waiting for recovery to settle before the next profile..."
  until ceph -s | grep -q "HEALTH_OK"; do sleep 5; done
done
```

Run it against the same disposable cluster for each profile in turn; the `rados bench`
output for `high_recovery_ops` should show materially worse average and tail write latency
than `high_client_ops`, with `balanced` in between — the actual gap is specific to the
underlying disks and network, which is exactly why it needs to be measured on the hardware
in question rather than assumed from the profile names.

Confirm which profile is actually active on a given OSD, since a config set at the `osd`
section applies cluster-wide but per-OSD overrides can exist:

```bash
ceph config get osd.0 osd_mclock_profile
```

## Conclusion

Since mClock became the default, the meaningful decision moved from tuning a handful of
independent throttles to choosing one of three profiles — know which scheduler a cluster
is running before reaching for `osd_max_backfills`, because under mClock it is very likely
inert.

There is no profile that avoids the trade-off, only ones that lean further toward client
latency or further toward shortening the degraded window; choose per incident based on
what is actually at risk, not once, globally, and forget about it.

`osd_recovery_sleep` remains a legitimate blunt instrument even under mClock, for the case
where a profile alone still leaves recovery too aggressive for a particular set of slow
disks — it is not overridden the way the backfill/recovery concurrency settings are.
