---
layout: post
title: "A Single-Writer Rule for Configuration Held in Redis"
subtitle: "Giving shared configuration in Redis exactly one owner, a version, and a way to tell readers it changed."
date: 2025-12-09 09:00:00 +0200
tags: [redis, infrastructure-as-code, architecture, reliability]
description: >-
  When several services can write the same Redis key, a read after a write
  can return someone else's value and nobody owns the truth. This walks
  through a lease-and-version pattern that makes one process the only
  writer, with a complete Python example a reader can run on a laptop.
---

## The problem

Redis is convenient for shared configuration precisely because anything with a
connection string can read and write it. That convenience is also the defect. Once two
services can both run `SET config:app:data ...`, the key no longer has a single history —
it has two, interleaved, and whichever write lands last wins regardless of which one was
actually correct.

```python
# Service A
r.set("config:app:data", json.dumps({"feature_flags": {"new_checkout": True}}))

# Service B, moments later, unaware of A's change
r.set("config:app:data", json.dumps({"feature_flags": {"new_checkout": False}}))
```

A reader that fetches the key after both writes sees `False`, not because that is the
intended state but because B happened to run second. Nothing in Redis records that a
conflict occurred. There is no error, no warning, no version mismatch — just a value that
is quietly wrong from one service's point of view.

This is easy to miss during development because it usually is not a race in the strict
sense. It is two components that both believe they are responsible for the same piece of
configuration, each acting correctly by its own logic, with no mechanism between them to
say only one of you gets to decide. The bug shows up later, under load or after a
deploy reorders which service starts first, and by then the plain `GET`/`SET` calls are
scattered across enough of the codebase that finding all the writers is itself a search
task.

The fix is not a faster or more careful `SET`. It is deciding, structurally, that exactly
one process may write a given key at a time, making that ownership visible in Redis
itself, and giving readers a way to know a change happened instead of polling and hoping.

## Working through it

### Why "last write wins" is not a rule at all

"Last write wins" sounds like a policy, but it is really the absence of one — it means
whichever write happens to arrive last decides the outcome, and nothing about the system
chose that outcome on purpose. A real policy has to say, before any write happens, who is
allowed to write. In Redis terms that means introducing a lease: a key that names the
current owner, with a time-to-live, so ownership itself is data other processes can check.

### Making the writer's identity explicit with a lease

`SET key value NX PX <ttl>` sets a key only if it does not already exist, with an
expiry. That single command is a lease: the first process to run it becomes the owner
until the lease expires, and every other process's attempt fails immediately because the
key is already there.

```python
token = str(uuid.uuid4())
acquired = client.set("config:app:lease", token, nx=True, px=15_000)
```

The token matters as much as the lease. Without it, any process holding a reference to
the lease key could renew or release it, including one that lost ownership a long time
ago — for instance after a garbage-collection pause or a network partition long enough
for the lease to expire and be taken by someone else. Comparing the stored token against
the caller's own token before allowing a renewal or a write is what is usually called a
fencing token: a write from a process that no longer holds the lease is rejected even if
that process still thinks it does.

### Turning the value into a fact with a version number

A lease decides who may write. It does not, on its own, help a reader decide whether the
value it just fetched is current. For that, the write itself needs to advance a version
counter atomically with the data, so a version number becomes a fact about the data:
"this is the seventh value this key has ever held," not a timestamp anyone could
disagree about.

Doing this safely means the check-lease, increment-version, and write-data steps must
happen as one atomic unit — otherwise a lease could expire and be taken by a second
writer in the gap between a first writer's check and its `SET`. Redis's `EVAL` runs a Lua
script atomically against the keys it touches, which is the natural place to put this:

```lua
local current = redis.call('GET', KEYS[1])
if current == false or current ~= ARGV[1] then
    return -1
end
local version = redis.call('INCR', KEYS[3])
redis.call('SET', KEYS[2], ARGV[2])
redis.call('PUBLISH', KEYS[4], version)
return version
```

A write that arrives from a process without the current lease token gets `-1` back and
nothing changes. A legitimate write gets a version number it can log, and every reader
that later loads the value gets that same number alongside it.

### Telling readers instead of making them ask

The last piece is distribution. Readers could poll the data key on an interval, but that
trades correctness problems for a lag-versus-load-on-Redis trade-off, and it hides
outages behind whatever interval you pick. Redis pub/sub lets the writer announce a
change the moment it happens, so readers apply it immediately instead of waiting out a
poll interval.

Pub/sub in Redis is at-most-once — a subscriber that is disconnected, or a message sent
while nobody is listening, is simply gone. A reader that only reacts to notifications
will silently drift if it ever misses one. The fix is not to make pub/sub reliable, which
it cannot be made to be without additional infrastructure; it is to keep the version
number as the source of truth and periodically reconcile by reading the data key
directly, using pub/sub only to make that reconciliation happen sooner than the next poll
would.

## The solution

Four files: a Redis container, a shared module with the Lua scripts, one writer, and one
reader. Anyone can run this on a laptop with Docker and Python installed — no private
infrastructure involved.

```yaml
# docker-compose.yml
services:
  redis:
    image: redis:7.2.5-alpine
    ports:
      - "6379:6379"
    command: ["redis-server", "--appendonly", "no"]
```

```
# requirements.txt
redis==5.0.8
```

```python
# config_store.py
"""Shared helpers for the single-writer configuration demo."""
import json
import uuid

import redis

LEASE_KEY = "config:app:lease"
DATA_KEY = "config:app:data"
VERSION_KEY = "config:app:version"
CHANNEL = "config:app:updates"
LEASE_TTL_MS = 15_000

_PUBLISH_SCRIPT = """
local current = redis.call('GET', KEYS[1])
if current == false or current ~= ARGV[1] then
    return -1
end
local version = redis.call('INCR', KEYS[3])
redis.call('SET', KEYS[2], ARGV[2])
redis.call('PUBLISH', KEYS[4], version)
return version
"""

_RENEW_SCRIPT = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('PEXPIRE', KEYS[1], ARGV[2])
end
return 0
"""


def connect():
    return redis.Redis(host="localhost", port=6379, decode_responses=True)


class WriterLease:
    """Acquires and renews the single-writer lease for one process."""

    def __init__(self, client):
        self.client = client
        self.token = str(uuid.uuid4())
        self._publish = client.register_script(_PUBLISH_SCRIPT)
        self._renew = client.register_script(_RENEW_SCRIPT)

    def acquire(self):
        return bool(self.client.set(LEASE_KEY, self.token, nx=True, px=LEASE_TTL_MS))

    def renew(self):
        return bool(self._renew(keys=[LEASE_KEY], args=[self.token, LEASE_TTL_MS]))

    def publish(self, config: dict):
        payload = json.dumps(config)
        version = self._publish(
            keys=[LEASE_KEY, DATA_KEY, VERSION_KEY, CHANNEL],
            args=[self.token, payload],
        )
        return int(version)
```

```python
# owner.py
"""The single writer for the shared configuration key.

Run a second copy of this against the same Redis instance to see the
lease reject it outright.
"""
import sys
import time

from config_store import WriterLease, connect


def main():
    client = connect()
    lease = WriterLease(client)

    if not lease.acquire():
        print("Another writer already holds the lease. Exiting.")
        sys.exit(1)

    print(f"Lease acquired, token={lease.token}")

    counter = 0
    while True:
        counter += 1
        config = {"feature_flags": {"new_checkout": counter % 2 == 0}, "counter": counter}

        if not lease.renew():
            print("Lost the lease - another writer must have taken over. Stopping.")
            sys.exit(1)

        version = lease.publish(config)
        if version == -1:
            print("Write rejected: this process no longer holds the lease.")
            sys.exit(1)

        print(f"Published version {version}: {config}")
        time.sleep(3)


if __name__ == "__main__":
    main()
```

```python
# reader.py
"""A read-only consumer of the shared configuration.

Loads the current value on startup, applies updates pushed over
pub/sub, and reconciles on a timeout in case a notification is missed.
"""
import json
import time

from config_store import CHANNEL, DATA_KEY, VERSION_KEY, connect


def load_current(client):
    pipe = client.pipeline()
    pipe.get(DATA_KEY)
    pipe.get(VERSION_KEY)
    data, version = pipe.execute()
    if data is None:
        return {}, 0
    return json.loads(data), int(version)


def main():
    client = connect()
    config, version = load_current(client)
    print(f"Starting at version {version}: {config}")

    pubsub = client.pubsub()
    pubsub.subscribe(CHANNEL)

    while True:
        message = pubsub.get_message(timeout=5.0)
        if message and message["type"] == "message":
            new_version = int(message["data"])
            if new_version > version:
                config, version = load_current(client)
                print(f"Applied version {version}: {config}")
            else:
                print(f"Ignored stale notification for version {new_version}")
        else:
            latest_config, latest_version = load_current(client)
            if latest_version > version:
                config, version = latest_config, latest_version
                print(f"Caught up to version {version} via poll: {config}")


if __name__ == "__main__":
    main()
```

Running it:

```bash
docker compose up -d
pip install -r requirements.txt

# terminal 1
python owner.py

# terminal 2
python reader.py

# terminal 3, while owner.py in terminal 1 is still running
python owner.py
```

The third process prints `Another writer already holds the lease. Exiting.` and makes no
write. The reader in terminal 2 prints a new applied version roughly every three seconds,
sourced entirely from the one process that holds the lease. Kill terminal 1's process and
wait fifteen seconds for the lease to expire, then run `python owner.py` again in a fresh
terminal: it acquires the lease and becomes the new sole writer, and the reader picks up
its first published version through the pub/sub path within moments, or through the
five-second poll fallback if a message happens to be missed.

## Conclusion

The underlying idea has nothing to do with Redis specifically: shared mutable state needs
exactly one writer at a time, that writer's identity needs to be checkable by anyone, and
every value needs a version so a reader can tell current from stale without guessing.

A few points generalise past this example:

**A lock only helps if writes check it atomically, not just before them.** Acquiring a
lease and then issuing a separate `SET` leaves a gap where the lease can be lost in
between. The Lua script closes that gap by making "is this still the owner" and "write
the value" one indivisible step.

**Notifications are an optimisation, never the source of truth.** Pub/sub delivers
updates quickly when everything is healthy and delivers nothing when a subscriber briefly
drops, with no error to say so. Keeping a version counter in the data itself, and
reconciling against it on a timer, means a missed message costs a few seconds of latency
instead of a permanently stale reader.

**This does not remove the cost of coordination — it makes the cost visible.** A second
writer no longer overwrites the first one; instead it fails loudly and has to be dealt
with, whether that means routing it to a different key, making it wait, or deciding it
should not have been writing in the first place. That failure is the point: a system that
cannot tell you two things tried to be the owner is not safer than one, it is only quieter
about the same problem.

**A single Redis instance is a single point of failure for the lease, not only for the
data.** Nothing here survives the lease's Redis node going down mid-lease; a deployment
that cannot tolerate a brief writer outage during failover needs Redis Sentinel or Cluster
underneath this pattern, not a change to the pattern itself.
