---
layout: post
title: "Tracking Moving Objects with Redis Sorted Sets and an Atomic Lua Tick"
subtitle: "Answering 'where is everything now' and 'what just left' without a database."
date: 2025-12-02 09:00:00 +0200
tags: [redis, architecture]
description: >-
  Tracking several objects moving along a line at once needs two guarantees
  at every step: a consistent view of where everything currently is, and a
  clean way to detect what has left the tracked range. This builds that on a
  Redis sorted set with a single atomic Lua script, and shows both guarantees
  holding under a real docker-based Redis instance.
---

## The problem

A line of physical or logical positions — a conveyor, a pipeline stage, a queue with
positional slots — holds several objects moving at the same rate at once. Two questions
need reliable answers at any moment: where is everything right now, and which objects have
just moved past the end and need to be handed off to whatever comes next.

The naive approach is a table keyed by object ID with a position column, updated by
scanning every row and incrementing it, then a separate query to find and delete the ones
past the threshold. Done as two operations, this has a gap: between "read all positions",
"write new positions", and "delete the ones that exited", another reader can observe a
half-advanced state, and nothing stops two advance operations from racing each other and
double-advancing an object.

Reaching for a full database and a transaction to close that gap is not wrong, but it is
more machinery than the problem needs. A Redis sorted set already stores exactly this
shape of data — a set of members each with a numeric score — and Redis's scripting model
means the entire "advance everything, then remove whatever exited" step can be a single
atomic operation with no separate transaction wrapper required.

## Working through it

### Sorted sets already model "position" directly

A sorted set entry is a member (the object's ID) and a score (a number Redis keeps sorted
on). Using score as position, "where is everything" is `ZRANGE key 0 -1 WITHSCORES`, and
"what is between position A and B" — a specific zone on the line — is
`ZRANGEBYSCORE key A B WITHSCORES`. Both are native sorted-set operations; no schema, no
index to maintain.

### Advancing every object is a write per member, not a single command

Redis has no "add this delta to every score in a sorted set" primitive — `ZINCRBY` only
updates one member at a time. Advancing everything by one delta genuinely needs a loop over
the members. Doing that loop from a client means one round trip per object, and — the part
that matters here — another client's `ZRANGE` or another advance call can interleave with
that loop, observing or acting on a set that is only partially advanced.

### Redis scripting removes the interleaving, not the loop

Redis executes a Lua script to completion before serving any other command — a script is
not a transaction wrapping several client round trips, it is a single opaque server-side
operation from every other client's point of view. The loop over members still exists, it
just moves onto the server, where nothing can observe or interleave with the partial
state:

```lua
-- tick.lua
-- KEYS[1] = sorted set key
-- ARGV[1] = delta to advance (can be negative)
-- ARGV[2] = exit threshold: positions >= this have left the tracked range
local key = KEYS[1]
local delta = tonumber(ARGV[1])
local exit_threshold = tonumber(ARGV[2])

local members = redis.call('ZRANGE', key, 0, -1, 'WITHSCORES')
local exited = {}
for i = 1, #members, 2 do
  local member = members[i]
  local score = tonumber(members[i + 1])
  local new_score = score + delta
  if new_score >= exit_threshold then
    redis.call('ZREM', key, member)
    table.insert(exited, member)
  else
    redis.call('ZADD', key, new_score, member)
  end
end
return exited
```

This is the whole mechanism: read every member and its current score, decide per-member
whether the new position exits the range, and either remove it (returning it to the caller
as "just exited") or write the new score — all inside one atomic execution. A client that
runs `ZRANGE` immediately before or after a tick observes either the fully-old state or the
fully-new state, never something in between, and two ticks issued back to back are
serialised by Redis, never interleaved.

### Choosing what the score actually means

Score does not have to be a raw distance. A timestamp of entry combined with a known
constant speed gives "current position" as a derived value without any tick at all — trade
a script for a read-time calculation. A raw position updated by explicit ticks, as above,
suits a system where the rate of advance is not constant or is driven by an external event
(a physical sensor, a batch step) rather than wall-clock time. Pick based on what actually
drives movement in the system being modelled; both are legitimate uses of the same sorted
set.

## The solution

A complete, runnable demonstration against a disposable Redis instance:

```bash
docker run -d --name conveyor-demo -p 6379:6379 redis:7.4-alpine
docker cp tick.lua conveyor-demo:/tmp/tick.lua
```

Save the Lua script above as `tick.lua` before running the `docker cp`. Then, from a shell
with the container running:

```bash
# Three objects enter at positions 0, 10 and 90 on a line that ends at 100.
docker exec conveyor-demo redis-cli ZADD conveyor:positions 0 crate-1 10 crate-2 90 crate-3

echo "--- where is everything now ---"
docker exec conveyor-demo redis-cli ZRANGE conveyor:positions 0 -1 WITHSCORES

echo "--- advance by 15, exit threshold 100 ---"
docker exec conveyor-demo redis-cli --eval /tmp/tick.lua conveyor:positions , 15 100

echo "--- state after the tick ---"
docker exec conveyor-demo redis-cli ZRANGE conveyor:positions 0 -1 WITHSCORES
```

Expected output:

```
--- where is everything now ---
crate-1
0
crate-2
10
crate-3
90
--- advance by 15, exit threshold 100 ---
crate-3
--- state after the tick ---
crate-1
15
crate-2
25
```

`crate-3` moved from 90 to a would-be 105, crossed the 100 threshold, and the script
returned it as exited while removing it from the set in the same operation — the caller
gets the exact list of what just left, with no separate query needed to find it.
Zone queries work the same way against the post-tick state:

```bash
docker exec conveyor-demo redis-cli ZADD conveyor:positions 5 crate-4 60 crate-5
docker exec conveyor-demo redis-cli ZRANGEBYSCORE conveyor:positions 0 30 WITHSCORES
```

```
crate-4
5
crate-1
15
crate-2
25
```

That is every object currently between position 0 and 30 on the line, computed with a
single command against whatever the last tick left behind.

## Conclusion

A sorted set is the right structure whenever "position" is the primary thing being
tracked and queried — range queries over position come for free, without a secondary
index to keep in step with writes.

Atomicity in Redis does not require a transaction API; a Lua script that runs to
completion before anything else is served gives the same guarantee for anything that fits
in one script, and is often simpler to reason about than `MULTI`/`EXEC`, which only queues
commands and does not let one command's result influence the next within the same
transaction.

The pattern generalises past conveyors: anything with a numeric progress value, a
threshold that means "done" or "expired", and a need to atomically advance many of them
and collect whichever crossed the line — a TTL-like expiry sweep, a multi-stage pipeline,
a leaderboard cutoff — fits the same shape of one sorted set and one tick script.
