---
layout: post
title: "Sizing Redis maxmemory Against a Stream Trim Threshold"
subtitle: "Trimming happens on write, so a stream that hits maxmemory first can never shrink again."
date: 2025-12-12 09:00:00 +0200
tags: [redis, performance, databases, reliability]
description: >-
  Redis Streams trim themselves as a side effect of XADD, and XADD is refused
  once maxmemory is reached under the default eviction policy. That leaves a
  gap where a stream can hit the memory ceiling before it ever gets trimmed
  down, and stay stuck there. Here is how to reproduce that state, why
  maxmemory-policy will not save you, and how to size and sequence trimming
  so it cannot happen.
---

## The problem

A Redis Stream capped with `XADD ... MAXLEN ~ 500` looks self-limiting: every write
also asks Redis to trim the stream back down to roughly 500 entries. It is easy to
read that and conclude the stream can never grow past its footprint at 500 entries,
so `maxmemory` only needs to cover that footprint.

It does not work that way, because trimming is not a background process. It is a
side effect that happens *inside* the `XADD` call, after the append. If `XADD` itself
is refused, the trim it would have performed never happens either:

```
127.0.0.1:6379> XADD events MAXLEN ~ 500 * payload "..."
(error) OOM command not allowed when used memory > 'maxmemory'
```

Under the default `maxmemory-policy` of `noeviction`, that is exactly what happens
once used memory crosses the ceiling: every command that could grow memory is
rejected outright, including the `XADD` that would have shrunk the stream back
towards 500 entries. The stream is stuck at whatever size it reached the moment
memory ran out — which, as shown below, can be well short of the 500 entries you
thought you were budgeting for — and every subsequent write fails the same way,
forever, until something else intervenes.

This is easy to miss during testing because it only shows up under sustained load.
A few manual `XADD` calls during development never get near `maxmemory`, the
trimming looks like it works, and the config ships. The failure mode only appears in
production, under a write rate high enough to reach the ceiling before the stream
has grown to its own configured `MAXLEN`. Because the affected key is a stream and
not the whole instance, it does not show up as "Redis is full" in the obvious
places — it shows up as one producer's writes failing while everything else on the
instance is fine.

## Working through it

### Confirming which commands are actually blocked

Redis's own command table settles this rather than guessing. Every command carries
flags, and one of them, `denyoom`, marks commands that are refused once used memory
exceeds `maxmemory` under a non-evicting policy:

```
127.0.0.1:6379> COMMAND INFO xadd
1) "xadd"
2) (integer) -5
3) 1) write
   2) denyoom
   3) fast
...
127.0.0.1:6379> COMMAND INFO xtrim
1) "xtrim"
2) (integer) -4
3) 1) write
...
```

`XADD` carries `denyoom`. `XTRIM` does not. That difference is the whole story: an
`XADD` with an embedded `MAXLEN` is denied wholesale once over the cap, trim
included, but a plain `XTRIM` issued on its own keeps working even while the
instance is over `maxmemory`, because it can only shrink memory, never grow it.

### Reproducing the stuck state

The example in the next section pins a stream to `MAXLEN ~ 500` with roughly
520-byte entries. Measuring it directly shows the steady-state footprint settles
around 1.52MB. Set `maxmemory` to anything comfortably below that — say 1300kb —
and start writing:

```
$ python producer.py
i=100 xlen=100 used_memory=1197712 maxmemory=1331200
i=200 xlen=200 used_memory=1290288 maxmemory=1331200
XADD failed at i=268: command not allowed when used memory > 'maxmemory'.
stream stuck at xlen=267, used_memory=1331824, maxmemory=1331200
```

The stream never even reached the 500 entries it was configured to hold before
memory ran out — trimming had nothing to remove yet, because the trim only
triggers past the `MAXLEN` threshold. Retrying the same `XADD` afterwards changes
nothing:

```
$ redis-cli xadd events maxlen '~' 500 '*' payload retry
(error) OOM command not allowed when used memory > 'maxmemory'
$ redis-cli xlen events
(integer) 267
```

It stays at 267 entries indefinitely. The bug was not a traffic spike or a slow
consumer; `maxmemory` was simply set to less than the memory 500 entries actually
need, so the ceiling was reached before the tool meant to enforce it ever had a
chance to run.

### Why a maxmemory-policy will not fix this

The obvious next idea is to swap `noeviction` for something that evicts under
pressure, such as `allkeys-lru`. That does not do what it sounds like it does for a
stream. Redis's eviction policies pick whole top-level keys to remove — they have
no concept of removing individual entries from inside a stream, a list, or a hash.
The unit of eviction is the key.

That is straightforward to see:

```
$ redis-cli config set maxmemory-policy allkeys-lru
$ redis-cli xlen events
(integer) 501
$ # write enough other keys to force eviction under memory pressure
$ redis-cli exists events
(integer) 0
```

The entire stream was evicted, not trimmed. `allkeys-lru`, `allkeys-lfu`,
`allkeys-random` and their `volatile-*` counterparts all work the same way. There is
no `maxmemory-policy` in Redis that trims a stream's old entries as a memory-pressure
response — eviction is a coarser tool than trimming, and reaching for it here trades
a write failure for silent data loss of the whole stream.

### The actual fix: decouple trimming from the write path, and size for headroom

Two changes, and both matter:

1. Run `XTRIM` (or `XADD ... MAXLEN` from a low-volume maintenance path, or an
   `XTRIM MINID` job keyed off consumer-group progress) independently of the
   producer's own writes, on a schedule. Because `XTRIM` is not `denyoom`-flagged, it
   keeps working even after the instance is already over `maxmemory`, so it can pull
   a stuck instance back under the ceiling and unblock writes again:

   ```
   $ redis-cli xtrim events maxlen 100
   (integer) 167
   $ redis-cli xadd events maxlen '~' 500 '*' payload recovered
   "1788714881382-0"
   ```

2. Size `maxmemory` above the stream's actual steady-state footprint at its
   configured `MAXLEN`, not above some arbitrary round number. Measure it — run the
   workload against a throwaway instance and read `INFO memory` — rather than
   estimating from entry count alone, because approximate (`~`) trimming batches
   removals at the macro-node granularity Redis uses internally (`stream-node-max-entries`,
   100 by default), so memory saws up and down between trim events rather than
   sitting flat at the target size. Leave enough headroom to clear that oscillation,
   not just the nominal target.

## The solution

A complete, runnable setup: a Redis instance sized correctly, and a producer that
shows the difference between the broken and working configuration by nothing more
than the value of `maxmemory`.

```yaml
# docker-compose.yml
services:
  redis:
    image: redis:7.4-alpine
    ports:
      - "6379:6379"
    volumes:
      - ./redis.conf:/usr/local/etc/redis/redis.conf:ro
    command: ["redis-server", "/usr/local/etc/redis/redis.conf"]
```

```ini
# redis.conf
# Stream is capped at MAXLEN ~ 500 entries of ~520 bytes each, which settles
# around 1.52MB once trimming has run a few times (measured with producer.py).
# 2mb leaves headroom above that measured steady state, not just above the
# nominal 500 * 520 bytes.
maxmemory 2mb
maxmemory-policy noeviction
save ""
appendonly no
```

```python
# producer.py
import sys
import time

import redis

STREAM = "events"
MAXLEN = 500
PAYLOAD = "x" * 512

r = redis.Redis(host="localhost", port=6379, decode_responses=True)

i = 0
try:
    while True:
        i += 1
        r.xadd(STREAM, {"payload": PAYLOAD}, maxlen=MAXLEN, approximate=True)
        if i % 100 == 0:
            info = r.info("memory")
            print(f"i={i} xlen={r.xlen(STREAM)} "
                  f"used_memory={info['used_memory']} maxmemory={info['maxmemory']}")
        time.sleep(0.001)
except redis.exceptions.ResponseError as exc:
    info = r.info("memory")
    print(f"XADD failed at i={i}: {exc}")
    print(f"stream stuck at xlen={r.xlen(STREAM)}, "
          f"used_memory={info['used_memory']}, maxmemory={info['maxmemory']}")
    sys.exit(1)
```

Run it:

```bash
docker compose up -d
python3 -m venv .venv && .venv/bin/pip install redis
.venv/bin/python producer.py
```

With `maxmemory 2mb` as shipped above, it runs indefinitely, oscillating in a narrow
band under the ceiling:

```
i=100 xlen=500 used_memory=1517496 maxmemory=2097152
i=200 xlen=502 used_memory=1518456 maxmemory=2097152
i=300 xlen=504 used_memory=1518552 maxmemory=2097152
...
```

Reproduce the broken behaviour on the same stack by shrinking the ceiling below the
measured footprint, with no other change:

```bash
docker compose exec redis redis-cli config set maxmemory 1300kb
docker compose exec redis redis-cli flushall
.venv/bin/python producer.py
```

```
i=100 xlen=100 used_memory=1197712 maxmemory=1331200
i=200 xlen=200 used_memory=1290288 maxmemory=1331200
XADD failed at i=268: command not allowed when used memory > 'maxmemory'.
stream stuck at xlen=267, used_memory=1331824, maxmemory=1331200
```

Recover it without touching `maxmemory` at all, to confirm the standalone-trim fix
independently of the sizing fix:

```bash
docker compose exec redis redis-cli xtrim events maxlen 100
docker compose exec redis redis-cli xadd events maxlen '~' 500 '*' payload recovered
```

## Conclusion

Three things generalise past streams specifically.

**A cleanup mechanism embedded in a write is only as available as the write itself.**
Anywhere Redis (or any store) offers "trim on insert" as a convenience, ask what
happens to that trim when the insert is refused. The trim is not a separate
guarantee; it inherits every failure mode of the write it rides on.

**"Eviction" and "trimming" are different operations, and a policy built for one does
not substitute for the other.** `maxmemory-policy` picks and deletes whole keys. A
stream, list, hash or set has no partial-eviction path — either the whole key goes,
or nothing does. Do not reach for an eviction policy to solve a within-key sizing
problem.

**Size infrastructure limits from a measurement of the real steady state, not from
the nominal number in the config next to it.** `MAXLEN ~ 500` does not mean "500
entries' worth of memory" in any tight sense — approximate trimming, per-entry
overhead and internal data-structure bookkeeping all add slack that is easy to
underestimate and cheap to measure directly.
