---
layout: post
title: "The NTP Clock Jump That Made the Scheduler Skip a Week of Measurements"
subtitle: "A periodic task that compares wall-clock timestamps to decide whether it is time to run will, on a backwards NTP correction, sleep for the size of the jump. The fix is a monotonic guard that treats a backwards step as 'not yet' rather than 'already done'."
date: 2026-09-14 09:00:00 +0200
tags: [embedded, reliability, python, linux]
description: >-
  A periodic measurement task on an embedded Linux box stopped running after
  an NTP step correction. The scheduler compared wall-clock timestamps, and a
  backwards jump made it believe the next run was weeks away. The fix is a
  monotonic-clock guard that treats a backwards step as 'not yet', plus a test
  that injects the jump and asserts the task still fires.
---

## The problem

A small embedded Linux device ran a periodic measurement task: sample a set of
sensors, write the result to a local database, every six hours. The task had
run without intervention for months. Then, after a routine NTP synchronisation
that corrected the system clock backwards by about nine days, the task stopped
producing measurements. It was not crashing. It was not logging an error. It
was simply not running, and would not run again for, by its own arithmetic,
about nine days.

The device was not unusual. It was a single-board computer running a Python
process under systemd, with the clock kept by `systemd-timesyncd` against an
internal NTP source. The nine-day backwards step was not a mistake by the NTP
source; it was the correction for a clock that had drifted forward while the
device's network was down for a week. The correction was correct. The scheduler
was not built to receive it.

## Working through it

### The scheduler, and the assumption it made

The task's scheduler was the common shape: a loop that records the wall-clock
time of the last run, and on each iteration of a fast tick checks whether the
difference between now and the last run has reached the period.

```python
# The shape of the scheduler, before the fix.
last_run = time.time()          # wall clock, seconds

while True:
    now = time.time()           # wall clock, seconds
    if now - last_run >= PERIOD:
        run_measurement()
        last_run = now
    time.sleep(TICK)
```

The assumption this makes is that `time.time()` is a clock that only moves
forwards, at roughly one second per second. It is not. `time.time()` is the
system's wall clock, and the kernel is allowed to step it, forwards or
backwards, whenever the NTP discipline decides the error is too large to
slew. On a device that has been offline for a week, the correction on
reconnection is exactly the kind of step that is applied instantly rather than
slewed in over minutes.

When the clock stepped backwards by nine days, `now - last_run` became
negative, and large in magnitude. The condition `now - last_run >= PERIOD`
would not be true again until the wall clock had advanced by the period
*plus* the size of the step. The scheduler did not skip one run. It skipped
nine days of runs, and it did so silently, because from its point of view it
was simply waiting for the next scheduled time, and the next scheduled time
had just moved into the future.

### Why the fix is a monotonic clock, not a bigger tolerance

The first instinct is to add a tolerance: if the difference is negative, treat
it as zero, or clamp it to the period. This works for the specific jump and
breaks for the next one, because it does not address the assumption. The
scheduler is still reading a clock that can move backwards, and it is still
making scheduling decisions from it.

The clock that cannot move backwards is `time.monotonic()`. It is not
affected by NTP steps, by `settimeofday`, or by any other adjustment to the
wall clock. It moves forwards at a steady rate, and it is the correct clock
for measuring *durations*, which is what a scheduler needs. The wall clock is
the correct clock for *labeling* a measurement with the time it was taken,
which the task also needs, but those are two different uses and they should
use two different clocks.

The fix splits the two:

```python
import time

PERIOD = 6 * 3600   # six hours
TICK = 30           # seconds between checks

last_run = time.monotonic()

while True:
    now_mono = time.monotonic()
    if now_mono - last_run >= PERIOD:
        run_measurement(timestamp=time.time())   # wall clock for the label
        last_run = now_mono
    time.sleep(TICK)
```

The scheduling decision is made entirely on the monotonic clock. The wall
clock appears exactly once, to stamp the measurement with the time it was
taken, where a backwards step is a data-quality note, not a scheduling
event.

### The guard that still matters: a backwards step is 'not yet'

There is a second, subtler case that the monotonic clock does not by itself
handle, and it is the one worth a named guard. The measurement's *label* is
the wall clock, and a consumer of the data may compare labels to decide
whether a measurement is stale. If the wall clock steps backwards, a
measurement taken just before the step has a label *later* than one taken just
after it. A staleness check that compares labels will, for the duration of the
step, believe the newer measurement is older than the older one.

The guard is to treat a backwards step in the label as 'not yet stale', not as
'an error'. Concretely, the staleness check compares the label against the
*maximum* label seen so far, not against the current wall clock, so a backwards
step cannot make a fresh measurement look old:

```python
max_label_seen = 0.0

def is_stale(label: float, now_wall: float) -> bool:
    global max_label_seen
    max_label_seen = max(max_label_seen, label)
    return (max_label_seen + STALE_AFTER) < now_wall
```

This is the part that is easy to leave out and that produces the second,
quieter bug: the scheduler fires on time, but the data consumer drops the
measurement as stale for the duration of the step.

### Keeping the property honest with a test that injects the jump

The failure mode is a clock step, which does not happen in a normal test run.
The test has to inject one. The way to do that without waiting nine days is to
make the clocks injectable and to step them inside the test:

```python
# tests/test_scheduler_clock_jump.py
import time
from unittest.mock import patch

import periodic_measurement as pm


def test_backwards_jump_does_not_skip_runs():
    """A backwards wall-clock step must not delay the next run."""
    ticks = []

    with patch.object(pm.time, "monotonic", side_effect=[0.0, pm.PERIOD + 1.0]), \
         patch.object(pm.time, "time", side_effect=[1_000_000.0, 990_000.0]), \
         patch.object(pm, "run_measurement") as run:
        # First tick: t=0, last_run=0.
        # Second tick: monotonic has advanced past PERIOD, wall clock has
        # stepped backwards by ~9 days. The run must still fire.
        pm.tick()
        pm.tick()
        assert run.call_count == 1
```

The test patches the two clocks independently, which is the point: the
monotonic clock advances by the period, the wall clock steps backwards, and
the assertion is that the run fires. If someone changes the scheduler back to
reading the wall clock for the scheduling decision, the wall-clock step makes
`now - last_run` negative, the run does not fire, and the test fails.

## The solution

The state of the fix:

1. The scheduling decision is made on `time.monotonic()`, which NTP steps do
   not affect, so a backwards correction cannot delay the next run.
2. The measurement's timestamp label is the wall clock, used exactly once,
   where a step is a data-quality note rather than a scheduling event.
3. The staleness check compares against the maximum label seen, so a backwards
   step cannot make a fresh measurement look old.
4. A test injects a backwards wall-clock step alongside a forward monotonic
   advance and asserts the run still fires, so a regression to wall-clock
   scheduling fails the build.

The general shape of the problem is a scheduler that reads the wall clock for
a duration, on a system where the wall clock can step. The answer is the same
every time: use the monotonic clock for the duration, the wall clock for the
label, and a test that steps the wall clock to prove the two are not coupled.

## Conclusion

A periodic task that compares wall-clock timestamps to decide when to run will,
on a backwards NTP step, sleep for the size of the step. The device did not
crash, did not log an error, and was doing exactly what its code told it to
do: wait until the next scheduled time, which the step had just moved nine
days into the future.

The fix is to use the clock that cannot step, `time.monotonic()`, for the
scheduling decision, and the wall clock only to label the measurement. The
staleness check compares against the maximum label seen, so the step cannot
make fresh data look old. The test injects the step and asserts the run still
fires, so the regression is caught at build time rather than on the next
week-long network outage.
