---
layout: post
title: "Chaining Single-Purpose Redis Consumers into a Processing Pipeline"
subtitle: "Splitting a do-everything consumer into single-purpose stages linked by Redis Streams."
date: 2025-12-05 09:00:00 +0200
tags: [redis, architecture, python]
description: >-
  A single service that detects, tracks, calibrates and computes statistics
  over the same data becomes impossible to test or scale in isolation. This
  splits that service into four single-purpose consumers chained through
  Redis Streams, using consumer groups for at-least-once delivery and
  XAUTOCLAIM for crash recovery, in a complete example a reader can run
  against a local Redis container.
---

## The problem

The shape is familiar: a service reads incoming data, and a function called
`process_reading()` does everything to it in one place.

```python
# Broken. Do not copy this.
def process_reading(reading):
    reading["anomaly"] = abs(reading["value"]) > 80
    window = fetch_recent_window(reading["sensor_id"])
    reading["rolling_average"] = sum(window) / len(window)
    offset = get_calibration_offset(reading["sensor_id"])
    reading["calibrated_value"] = reading["value"] + offset
    update_running_stats(reading)
    return reading
```

It works, and for a while that is the whole problem: it keeps working long
enough that nobody stops to split it up. Then requirements change one at a
time, the way they do — the anomaly threshold needs to vary per sensor type,
the rolling window needs a different size for one class of device, the
calibration lookup needs to hit a slower external source. Each change is
small, but it is a change to a function that also does three other things,
so every change is tested against all four responsibilities whether it
touches them or not.

Two failures compound this. It is untestable in isolation: a unit test for
the anomaly check also has to fake a rolling window, a calibration store and
a stats sink, so the tests people actually write cover the whole function or
nothing. And it fails as one unit: if the statistics step throws on a schema
mismatch, no reading gets detected, tracked or calibrated either, even
though those three steps were fine. A slow stage — the calibration lookup,
say — throttles every other stage too, because they all run on the same
thread for the same message.

None of this shows up under light load. It shows up when one responsibility
needs to change faster than the others, scale differently, or fail without
taking the rest down — by which point all four are woven through one
function and one deploy.

## Working through it

### Split by responsibility, and let a queue own the boundary

The fix is not simply "four functions" — calling code would still run them
as one unit. The four responsibilities need four *processes*, each
testable, deployable and scalable independently, with something between
them that survives one process being slow or gone: a broker, not a
function call.

### Choosing Redis Streams over pub/sub or plain lists

Redis offers three plausible primitives, and they are not interchangeable.

Pub/sub delivers a message only to subscribers connected at that moment. A
consumer redeploying, or restarting after a crash, simply never sees what
was sent during that gap — fine for fire-and-forget notifications, not for
a pipeline stage where a missed reading is data loss.

A plain list (`LPUSH` / `BRPOP`) fixes that — the message sits until popped
— but a popped message is gone the instant it is popped. If the consumer
crashes after `BRPOP` and before finishing the work, that message is lost
with no record it was ever taken.

Streams (`XADD` / `XREADGROUP`) add the piece both are missing: a *consumer
group* tracks, per message, which consumer took it and whether it was
acknowledged. A message taken but never acknowledged stays visible — as
pending, not gone — so a replacement consumer can pick it back up. That is
the guarantee a pipeline stage needs: at-least-once delivery with an
explicit acknowledgement step.

The cost is that "at-least-once" is exactly what it says: a consumer can
crash after finishing work but before sending `XACK`, so the message is
redelivered and processed twice. Every handler here has to tolerate that,
which is why the stages below only overwrite state (a fixed-length rolling
window) rather than blindly incrementing a counter. `stats.py` is the one
exception, kept simple below: `HINCRBY` on a redelivered message
double-counts it. Where exact counts matter, the fix is a dedup key per
message ID — a real cost of this architecture, not a footnote.

### Consumer groups, acknowledgement, and what "done" means

Each stage creates its own consumer group on its input stream and reads with
`XREADGROUP`. Reading does not remove the message — it moves it into that
consumer's pending entries list. Only `XACK` clears it. That distinguishes
"the process read the message" from "the process finished the message": a
stage can read a reading, crash while computing the rolling average, and
never acknowledge it. The message is not lost, only pending.

### Recovering from a stage that dies mid-message

That is `XAUTOCLAIM`: given a minimum idle time, it takes pending messages
nobody has acknowledged in that window and reassigns them to the calling
consumer. Running it at the top of every read loop means a restarting
consumer first mops up its own — or a dead sibling's — unfinished work
before moving on. No separate monitoring process, no dead-letter queue to
build; the stream already has the bookkeeping.

### Independent scaling and backpressure

Because each stage is its own consumer group, `detect` can run three
processes and `calibrate` can run one, in proportion to how expensive each
step is — the slow calibration lookup no longer sets the pace for
detection. And because a stream is a durable buffer, a slow stage does not
drop messages, it accumulates a backlog visible with `XLEN` and `XPENDING`.
That visibility has a cost: an unbounded backlog fills memory if a stage
stays down long enough, which is why every `XADD` below caps the stream
with `MAXLEN ~`.

### Testing each stage as a pure function

The other payoff shows up in the tests. Each stage keeps its actual
decision — is this an anomaly, what is the calibrated value — in a plain
function with no Redis client and no I/O, wrapped in a thin handler the
shared run-loop calls. The decision is what changes when requirements
change, so it is the only thing that needs a test.

## The solution

```yaml
# docker-compose.yml
services:
  redis:
    image: redis:7.4-alpine
    ports:
      - "6379:6379"
```

```ini
# requirements.txt
redis==5.0.8
pytest==8.3.3
```

```python
# pipeline/base.py
"""Shared consumer-loop helper for every stage in the pipeline."""
from __future__ import annotations

import json
import os
from typing import Callable, Optional

import redis


def main_for(
    *,
    in_stream: str,
    group: str,
    default_consumer_name: str,
    handler_factory: Callable[[redis.Redis], Callable[[dict], Optional[dict]]],
    out_stream: Optional[str] = None,
) -> None:
    """Build a Redis client from REDIS_HOST, pass it to handler_factory, and
    run the stage — the boilerplate every stage's __main__ would repeat."""
    client = redis.Redis(
        host=os.environ.get("REDIS_HOST", "localhost"), port=6379, decode_responses=True
    )
    run_stage(
        redis_client=client,
        in_stream=in_stream,
        group=group,
        consumer_name=os.environ.get("CONSUMER_NAME", default_consumer_name),
        handler=handler_factory(client),
        out_stream=out_stream,
    )


def run_stage(
    *,
    redis_client: redis.Redis,
    in_stream: str,
    group: str,
    consumer_name: str,
    handler: Callable[[dict], Optional[dict]],
    out_stream: Optional[str] = None,
    block_ms: int = 5000,
) -> None:
    """Read in_stream via a consumer group, call handler() on each message,
    forward the result to out_stream if given, then acknowledge."""
    try:
        redis_client.xgroup_create(in_stream, group, id="0", mkstream=True)
    except redis.ResponseError as exc:
        if "BUSYGROUP" not in str(exc):
            raise

    while True:
        # Reclaim work a dead consumer in this group left pending for more
        # than 30 seconds, so a crashed process does not lose messages.
        _, claimed, _ = redis_client.xautoclaim(
            in_stream, group, consumer_name, min_idle_time=30_000, start_id="0-0"
        )
        messages = claimed or []

        if not messages:
            response = redis_client.xreadgroup(
                group, consumer_name, {in_stream: ">"}, count=10, block=block_ms
            )
            if not response:
                continue
            _, messages = response[0]

        for message_id, fields in messages:
            payload = json.loads(fields["data"])
            result = handler(payload)
            if result is not None and out_stream is not None:
                redis_client.xadd(
                    out_stream, {"data": json.dumps(result)}, maxlen=10_000, approximate=True
                )
            redis_client.xack(in_stream, group, message_id)
```

```python
# pipeline/detect.py
"""Detection stage: flags readings whose value is outside the expected band."""
from __future__ import annotations

import os

from base import main_for

THRESHOLD = float(os.environ.get("DETECT_THRESHOLD", "80.0"))


def is_anomaly(value: float, threshold: float = THRESHOLD) -> bool:
    return abs(value) > threshold


def handle(reading: dict) -> dict:
    reading["anomaly"] = is_anomaly(reading["value"])
    return reading


if __name__ == "__main__":
    main_for(
        in_stream="stream:raw",
        group="detectors",
        default_consumer_name="detect-1",
        handler_factory=lambda _client: handle,
        out_stream="stream:detected",
    )
```

```python
# pipeline/track.py
"""Tracking stage: maintains a rolling average per sensor over the last
N readings and tags each reading with it."""
from __future__ import annotations

import os

import redis

from base import main_for

WINDOW_SIZE = int(os.environ.get("TRACK_WINDOW", "5"))


def make_handler(client: redis.Redis):
    def handle(reading: dict) -> dict:
        key = f"window:{reading['sensor_id']}"
        client.rpush(key, reading["value"])
        client.ltrim(key, -WINDOW_SIZE, -1)
        window = [float(v) for v in client.lrange(key, 0, -1)]
        reading["rolling_average"] = sum(window) / len(window)
        return reading

    return handle


if __name__ == "__main__":
    main_for(
        in_stream="stream:detected",
        group="trackers",
        default_consumer_name="track-1",
        handler_factory=make_handler,
        out_stream="stream:tracked",
    )
```

```python
# pipeline/calibrate.py
"""Calibration stage: applies a per-sensor offset looked up from Redis."""
from __future__ import annotations

import redis

from base import main_for


def make_handler(client: redis.Redis):
    def handle(reading: dict) -> dict:
        offset = client.hget("calibration_offsets", reading["sensor_id"])
        reading["calibrated_value"] = reading["value"] + float(offset or 0.0)
        return reading

    return handle


if __name__ == "__main__":
    main_for(
        in_stream="stream:tracked",
        group="calibrators",
        default_consumer_name="calibrate-1",
        handler_factory=make_handler,
        out_stream="stream:calibrated",
    )
```

```python
# pipeline/stats.py
"""Statistics stage, the terminal consumer. HINCRBY is not idempotent
under redelivery, as discussed above."""
from __future__ import annotations

import redis

from base import main_for


def make_handler(client: redis.Redis):
    def handle(reading: dict) -> None:
        key = f"stats:{reading['sensor_id']}"
        client.hincrby(key, "count", 1)
        client.hincrbyfloat(key, "sum", reading["calibrated_value"])
        if reading["anomaly"]:
            client.hincrby(key, "anomalies", 1)
        return None

    return handle


if __name__ == "__main__":
    main_for(
        in_stream="stream:calibrated",
        group="stats",
        default_consumer_name="stats-1",
        handler_factory=make_handler,
    )
```

```python
# pipeline/producer.py
"""Demo producer: writes synthetic sensor readings onto stream:raw."""
from __future__ import annotations

import json
import os
import random
import time

import redis


def main() -> None:
    client = redis.Redis(
        host=os.environ.get("REDIS_HOST", "localhost"), port=6379, decode_responses=True
    )
    sensors = ["sensor-a", "sensor-b", "sensor-c"]
    for i in range(200):
        reading = {
            "sensor_id": random.choice(sensors),
            "value": round(random.gauss(50, 20), 2),
            "seq": i,
        }
        client.xadd("stream:raw", {"data": json.dumps(reading)}, maxlen=10_000, approximate=True)
        time.sleep(0.05)
    print("producer done: 200 readings written to stream:raw")


if __name__ == "__main__":
    main()
```

```python
# pipeline/test_detect.py
from detect import is_anomaly


def test_within_band_is_not_anomaly():
    assert is_anomaly(50.0, threshold=80.0) is False


def test_above_band_is_anomaly():
    assert is_anomaly(95.0, threshold=80.0) is True


def test_negative_above_band_is_anomaly():
    assert is_anomaly(-95.0, threshold=80.0) is True
```

Running it end to end:

```bash
docker compose up -d
pip install -r requirements.txt

cd pipeline
pytest -q                          # 3 passed, no Redis needed

python detect.py &
python track.py &
python calibrate.py &
python stats.py &
python producer.py                 # writes 200 readings, then exits

redis-cli HGETALL stats:sensor-a
# 1) "count"      2) "63"
# 3) "sum"        4) "3150.42"
# 5) "anomalies"  6) "4"
```

The numbers vary run to run since the producer generates random values, but
`count` for a sensor should match how many readings it was assigned, and
`XLEN stream:raw` minus `XPENDING stream:raw detectors` should converge to
zero once every stage catches up — confirmation nothing was dropped.

## Conclusion

**A shared function is not a shared boundary.** Splitting one function into
four still leaves one deploy, one failure domain and one test surface,
unless something durable sits between the pieces. The queue is the
boundary, not the refactor.

**At-least-once is a real constraint on every handler, not a broker
detail.** A crash between finishing work and acknowledging it is not an
edge case — it is the case acknowledgement exists to handle, and every
downstream handler must tolerate seeing a message twice. `stats.py` here is
the honest counter-example: it gets this wrong for brevity, and a
production version needs a dedup key per message ID.

**Recovery is a property of the loop, not a separate process.** Putting
`XAUTOCLAIM` at the top of the read loop means every consumer rescues its
own crashed predecessor on the way to new work, with no supervisor
watching for stuck messages.

**Decomposition earns nothing if you cannot see the queues between the
pieces.** `XLEN` and `XPENDING` on each stream are what turn "the pipeline
feels slow" into "stage three has a backlog of four thousand messages" —
visibility a single monolithic function never gave you in the first place,
because there was never a boundary to inspect.
