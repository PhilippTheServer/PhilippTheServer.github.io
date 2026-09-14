---
layout: post
title: "A Scheduler That Took a Measurement Every Second, Because the Clock Was Wrong"
subtitle: "A device with no battery-backed RTC boots with a stale clock, NTP jumps it forward, and a wall-clock-aligned scheduler tries to catch up — one measurement at a time."
date: 2026-09-14 09:00:00 +0200
tags: [embedded, reliability, python, linux]
description: >-
  A periodic measurement loop on a small computer-on-module ran a full
  measurement every second for minutes after every cold boot. The scheduler was
  wall-clock aligned and correct; the clock was not. The fix is a skip-ahead
  guard that absorbs a forward jump in one iteration, and the reason it needs a
  resolution threshold is a detail about how often the loop actually runs.
---

## The problem

A measurement device that is supposed to take a reading every fifteen minutes
took one every second, for the first few minutes after every cold boot. Not
occasionally — reliably. Every power cycle, the same behaviour: the scheduler
came alive, and instead of sleeping until the next tick it ran measurement
after measurement as fast as the loop allowed, until the pace settled back to
fifteen minutes.

The device is a small computer-on-module — a CM5 — running a Python service.
It has no battery-backed real-time clock. That one hardware fact is the whole
story, and it is worth stating plainly because it is the kind of fact that
looks irrelevant until it is not: on a cold boot, before NTP has spoken, the
system clock is whatever the firmware left it at, which on this module is a
stale time, sometimes hours in the past. NTP then syncs, and the clock jumps
forward.

A scheduler that aligns its ticks to the wall clock — the correct design for a
periodic measurement, because it means the readings land at the same times
every day regardless of how long each one takes — has exactly one assumption
it cannot check: that the wall clock does not move under it.

## Working through it

### What the scheduler does, and why it is correct

The scheduler runs an infinite loop. Each iteration it reads the current
schedule, works out which step is active, computes the next tick, sleeps until
it, runs the measurement, and loops. Ticks are wall-clock aligned against an
anchor timestamp: if the interval is fifteen minutes and the schedule started
at 08:00, ticks fire at 08:00, 08:15, 08:30, and so on. If a measurement runs
longer than the interval, the missed tick is skipped — the next tick is the
first one at or after now, not a queue of owed ticks.

That last sentence is the important design decision, and it is the one that
makes the bug possible. The scheduler already knows how to skip ahead: when it
enters a step, it computes how many cycle boundaries have fallen between the
step's start and now, and schedules the tick after the last one. It does not
try to run the missed measurements. The catch-up logic exists. It is in the
`step_changed` branch. It is not in the other branch.

### The branch that misses it

In steady state — same step as the previous iteration — the next tick is
computed the simple way:

```python
next_tick = last_tick + timedelta(seconds=interval)
```

That is correct as long as `last_tick` was in the recent past. It stops being
correct when the clock jumps forward between the previous iteration and this
one: `last_tick` is now hours in the past, `next_tick` is also hours in the
past, and the sleep returns immediately. The loop runs a measurement, sets
`last_tick` to that tick, computes the next one — still in the past — and runs
again. The scheduler is not broken. It is faithfully executing "run at every
tick boundary", and there are hundreds of tick boundaries between where the
clock was and where it is.

The `step_changed` branch does not have this problem, because it never
computes `last_tick + interval`. It recomputes from the anchor: how many
cycles elapsed between the step start and now, and schedule the one after.
The clock jump is absorbed in one iteration, exactly as the design intends.
The defect is that the two branches disagree about what to do when the
computed tick is in the past, and the branch that runs most of the time is the
one that does not handle it.

### The fix: skip ahead in the steady-state branch too

The guard is the same arithmetic the other branch already uses, applied after
the simple computation: if the next tick is still solidly in the past, compute
how many cycles elapsed since the last tick, jump to that boundary, and step
one more if it is still past.

```python
next_tick = last_tick + timedelta(seconds=interval)

# Guard against the clock jumping forward (e.g. NTP sync after boot on a
# module with no battery-backed RTC). If next_tick is still solidly in the
# past, skip ahead to the next tick at or after now rather than running a
# rapid catch-up series of measurements.
if _seconds_until(next_tick) < -_TICK_RESOLUTION:
    elapsed_since_last = (now - last_tick).total_seconds()
    cycles_elapsed = int(elapsed_since_last / interval)
    next_tick = last_tick + timedelta(seconds=cycles_elapsed * interval)
    if _seconds_until(next_tick) < -_TICK_RESOLUTION:
        next_tick += timedelta(seconds=interval)
```

One jump, one iteration, back to the normal pace. No missed-tick queue, no
special case for "just booted", no flag that has to be set and cleared.

### Why the threshold, and why it is one second

The guard is not `if next_tick < now`. It is `if _seconds_until(next_tick) <
-_TICK_RESOLUTION`, with the resolution at one second. The difference matters
for the normal, non-jumped case.

A tick computed as `last_tick + interval` is, at the moment it is computed, a
fraction of a second in the future — the loop takes a little time to run. By
the time the sleep is requested, a few milliseconds have passed. A comparison
against bare `now` would treat that fraction as "in the past" on some runs and
"ahead" on others, depending on scheduling jitter, and the guard would fire on
ordinary iterations for no reason. The one-second threshold says: a tick that
is less than a second past is a tick that is effectively now, and the loop
should just run it. A tick that is more than a second past is not a tick that
is late, it is a tick that belongs to a different time, and the clock moved.

The threshold is also the honest statement of the system's timing precision.
A fifteen-minute measurement cadence has no business distinguishing between
"one second past" and "now" — if it did, the jitter in the loop itself would
be larger than the difference.

### Why this is a hardware fact, not a software bug

The fix is eleven lines in one branch, but the article is not really about
those eleven lines. The point is that the scheduler's contract — ticks align
to the wall clock — is only a contract if the wall clock is a wall clock. On a
device with a battery-backed RTC, the clock on boot is within a few seconds of
true time, NTP corrects the drift, and the jump, when it happens, is small
enough that the one-second threshold absorbs it without the skip-ahead ever
firing. On a module without one, the jump is hours, and the same code that is
correct on a laptop is a measurement generator on the device.

The same shape of bug exists anywhere a wall-clock-aligned loop runs on a
machine whose clock is not guaranteed to be sane at boot: a cron-style worker
on a VM that was suspended, a log rotation that fires by timestamp on a device
that was powered off, a batch job that schedules by absolute time on a cluster
node that just came out of maintenance. The fix is the same in every case:
when the computed "next" is solidly in the past, do not run it, do not queue
it, recompute from the anchor.

## The solution

The state of the fix:

1. The steady-state branch computes `last_tick + interval` as before.
2. If that tick is more than one second in the past, the branch recomputes
   from elapsed time: cycles since the last tick, jump to that boundary, step
   one more if still past.
3. The one-second threshold is the system's stated timing resolution, below
   which a tick is "now" and above which the clock has moved.

And the check that keeps it honest is the property the whole fix exists for:
**a forward clock jump of any size results in at most one measurement before
the next sleep is a normal-length sleep.** That is the property a test should
pin, with a fake clock that jumps two hours between two iterations and an
assertion on how many times the measurement ran.

## Conclusion

A device with no battery-backed clock boots stale, NTP jumps it forward, and
a wall-clock-aligned scheduler — the correct design — tries to run every
missed tick, one per loop iteration, for the first minutes of every boot. The
skip-ahead logic existed in the branch that handles step changes; it was
missing from the branch that handles the rest of the time.

The fix is to apply the same arithmetic in the steady-state branch: if the
next tick is solidly in the past, recompute from the anchor and land on the
first tick at or after now. The one-second threshold is what keeps the guard
from firing on ordinary jitter, and it is the honest statement of a
fifteen-minute cadence's precision.

The transferable rule: a wall-clock-aligned loop is only as trustworthy as the
clock it aligns to, and the check that belongs in the loop is not "is the next
tick in the past" but "is it in the past by more than this system can
legitimately be late".
