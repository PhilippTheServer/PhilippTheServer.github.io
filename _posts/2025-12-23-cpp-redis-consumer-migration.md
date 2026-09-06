---
layout: post
title: "Migrating Redis Consumers from Python to C++ on Constrained Hardware"
subtitle: "What a dozen idle interpreters cost you, and what replacing the hot path with C++ actually buys and costs."
date: 2025-12-23 09:00:00 +0200
tags: [cpp, python, redis, performance]
description: >-
  A dozen Python processes each blocked on a Redis stream look harmless until
  you add up their idle cost on a small device. Here is how to replace just
  that consume loop with a C++ equivalent, built and run through Docker so
  you can measure the trade-off yourself instead of taking anyone's word for it.
---

## The problem

A Redis stream consumer is a small, boring loop: block on a read, get a batch of entries,
do something with each one, acknowledge it, repeat. In Python it is a few lines:

```python
import os
import redis

r = redis.Redis(host=os.environ.get("REDIS_HOST", "127.0.0.1"), port=6379)
group, consumer, stream = "workers", "consumer-1", "events"

try:
    r.xgroup_create(stream, group, id="$", mkstream=True)
except redis.ResponseError as exc:
    if "BUSYGROUP" not in str(exc):
        raise

while True:
    resp = r.xreadgroup(group, consumer, {stream: ">"}, count=10, block=5000)
    for _, entries in resp or []:
        for entry_id, fields in entries:
            print(entry_id, fields)
            r.xack(stream, group, entry_id)
```

On a development laptop this is invisible. On an edge device — a small ARM board or a
low-power industrial PC with a handful of cores and a gigabyte or two of RAM — a dozen of
these, one per stream, stop being invisible. Each is a separate CPython process: its own
interpreter startup, its own copy of every imported module's bytecode, its own garbage
collector, its own heap, none of it doing useful work. It exists because the interpreter
has to exist before these five lines of logic can run.

The failure mode is not a crash. It is a slow squeeze: `free -h` shows less headroom every
month as consumers accrue, and eventually the thing actually justifying the device's
existence starts missing its deadlines because a dozen idle-looking Python processes are
quietly holding most of the RAM and cache.

It is easy to miss because no single consumer looks wrong. Profile one and it spends 99%
of its time blocked in a `read()` syscall, which looks exactly like a process doing
nothing. The cost is not in what any one process does; it is in what twelve of them cost
merely by existing, multiplied by however many streams get added next.

## Working through it

### Why the cost multiplies instead of adding up

Consolidating the twelve consumers into one Python process with twelve threads looks like
the obvious fix, and it half-works: you go from twelve interpreter baselines to one. But
Python's GIL means only one thread executes bytecode at a time. While a consumer is
blocked on Redis I/O the GIL is released, so idle consumers do coexist cheaply as threads.
The moment several need to parse, validate or dispatch a message at the same time, that
work serialises on the GIL regardless of core count. Threading buys back memory, not
throughput — the fix for the memory problem and the fix for the CPU-under-load problem are
not the same fix in Python, and only a language with real OS threads gets you both from one
change.

### Choosing a client library

`hiredis` is the C library everything else here is built on. It hands back a raw reply as a
tree of typed nodes and leaves parsing a `XREADGROUP` reply — nested arrays of streams,
entries, then alternating field/value strings — to you: not hard, but exactly the kind of
hand-rolled parsing that is easy to get subtly wrong once and never notice.

[redis-plus-plus](https://github.com/sewenew/redis-plus-plus) wraps `hiredis` with an
idiomatic C++ API that already understands streams: `xreadgroup` fills a container you
provide via an output iterator, with fields already decoded to `std::string`. The cost is
a second dependency with no distribution package to rely on — both libraries have to be
built from source and pinned deliberately, which is the first real difference from
`pip install redis`.

### The blocking-read timeout trap

`redis-plus-plus` is a synchronous client: one socket, one blocking call at a time. Give
`XREADGROUP` a five-second block timeout and the *command* may wait five seconds inside
Redis — but the client's own socket, by default, has a shorter read timeout and raises a
timeout error on the C++ side while Redis is still waiting. The fix is to disable the
socket-level timeout and let the command's own block timeout be the only one that matters:

```cpp
sw::redis::ConnectionOptions opts;
opts.host = host;
opts.port = port;
opts.socket_timeout = std::chrono::milliseconds(0);  // no socket timeout; the
                                                       // block timeout on xreadgroup
                                                       // below is what actually limits the wait
```

Nothing in the compiler or the type system catches this. It surfaces at runtime as an
exception that reads like a network fault on a machine with no network fault, the kind of
thing you find once and must remember for every service using the same client.

### Making a restart safe

A consumer that loses its process — OOM-killed, crashed, redeployed — has to come back and
pick up cleanly. `XGROUP CREATE` with `MKSTREAM` handles the first run; every run after
hits `BUSYGROUP` because the group exists already, which is not an error worth stopping for:

```cpp
try {
    redis.xgroup_create(stream_key, group_name, "$", true);
} catch (const sw::redis::Error &err) {
    if (std::string(err.what()).find("BUSYGROUP") == std::string::npos) {
        throw;
    }
}
```

Matching on a substring of an error message is a wart worth naming rather than hiding: it
works, but is coupled to Redis's current wording, not a typed code. It is the C++
equivalent of the Python `except redis.ResponseError` block above — handling "this specific
failure is fine" did not get easier by changing language, only the syntax did.

### Keeping the win inside the image

The entire point of the rewrite is a smaller footprint. An image that ships
`build-essential`, `cmake`, `git` and two source trees alongside the binary has thrown that
away before the container starts. The build must happen in one stage, with only the
compiled binary and the two shared libraries it needs crossing into the runtime stage —
this is the part of "the solution" below that most directly decides whether the migration
was worth doing.

### Measuring it rather than assuming it

Resist inventing a number here. Honestly, without a benchmark: a compiled C++ binary has no
interpreter to start, no module import graph to walk and no garbage collector pausing it,
so its idle RSS and cold-start time are structurally smaller than a CPython process's, by a
margin that depends on your image, allocator and kernel — not on a number in a blog post.
Measure it on your own device with the two consumers side by side:

```bash
docker stats --no-stream
```

Run the Python script above and the C++ program from this article as two containers on the
same host and compare the RSS and CPU columns yourself. That number is real; one lifted
from an article you cannot reproduce is not.

## The solution

The complete, runnable consumer: a CMake project, its Dockerfile, and a Compose file that
brings up Redis alongside it.

```cmake
# CMakeLists.txt
cmake_minimum_required(VERSION 3.16)
project(stream_consumer CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

find_path(HIREDIS_HEADER hiredis)
find_library(HIREDIS_LIB hiredis)

find_path(REDIS_PLUS_PLUS_HEADER sw)
find_library(REDIS_PLUS_PLUS_LIB redis++)

add_executable(stream_consumer src/main.cpp)
target_include_directories(stream_consumer PRIVATE ${HIREDIS_HEADER} ${REDIS_PLUS_PLUS_HEADER})
target_link_libraries(stream_consumer PRIVATE ${REDIS_PLUS_PLUS_LIB} ${HIREDIS_LIB} pthread)
```

```cpp
// src/main.cpp
#include <sw/redis++/redis++.h>

#include <chrono>
#include <cstdlib>
#include <iostream>
#include <string>
#include <thread>
#include <unordered_map>
#include <vector>

namespace {

std::string env_or(const char *name, const std::string &fallback) {
    const char *value = std::getenv(name);
    return value != nullptr ? std::string(value) : fallback;
}

}  // namespace

int main() {
    const std::string host = env_or("REDIS_HOST", "127.0.0.1");
    const int port = std::stoi(env_or("REDIS_PORT", "6379"));
    const std::string stream_key = env_or("STREAM_KEY", "events");
    const std::string group_name = env_or("GROUP_NAME", "workers");
    const std::string consumer_name = env_or("CONSUMER_NAME", "consumer-1");

    sw::redis::ConnectionOptions opts;
    opts.host = host;
    opts.port = port;
    opts.socket_timeout = std::chrono::milliseconds(0);

    sw::redis::Redis redis(opts);

    try {
        redis.xgroup_create(stream_key, group_name, "$", true);
        std::cout << "Created consumer group '" << group_name
                  << "' on stream '" << stream_key << "'\n";
    } catch (const sw::redis::Error &err) {
        if (std::string(err.what()).find("BUSYGROUP") == std::string::npos) {
            throw;
        }
        std::cout << "Consumer group '" << group_name << "' already exists\n";
    }

    std::cout << "Listening on '" << stream_key << "' as '" << consumer_name << "'\n";

    using Attrs = std::vector<std::pair<std::string, std::string>>;
    using Item = std::pair<std::string, sw::redis::Optional<Attrs>>;
    using ItemStream = std::vector<Item>;

    while (true) {
        std::unordered_map<std::string, ItemStream> result;

        try {
            redis.xreadgroup(group_name, consumer_name, stream_key, ">",
                              std::chrono::milliseconds(5000), 10,
                              std::inserter(result, result.end()));
        } catch (const sw::redis::Error &err) {
            std::cerr << "Read failed: " << err.what() << ", retrying in 1s\n";
            std::this_thread::sleep_for(std::chrono::seconds(1));
            continue;
        }

        auto it = result.find(stream_key);
        if (it == result.end() || it->second.empty()) {
            continue;  // block timeout with nothing new - loop and block again
        }

        for (const auto &item : it->second) {
            const std::string &id = item.first;

            std::cout << "id=" << id;
            if (item.second) {
                for (const auto &field : *item.second) {
                    std::cout << ' ' << field.first << '=' << field.second;
                }
            }
            std::cout << '\n';

            redis.xack(stream_key, group_name, id);
        }
    }
}
```

```dockerfile
# syntax=docker/dockerfile:1
FROM debian:bookworm-slim AS builder

ARG HIREDIS_VERSION=v1.2.0
ARG REDIS_PLUS_PLUS_VERSION=1.3.15

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential cmake git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /src

RUN git clone --depth 1 --branch ${HIREDIS_VERSION} https://github.com/redis/hiredis.git \
    && cmake -S hiredis -B hiredis/build -DCMAKE_BUILD_TYPE=Release \
    && cmake --build hiredis/build --parallel \
    && cmake --install hiredis/build

RUN git clone --depth 1 --branch ${REDIS_PLUS_PLUS_VERSION} \
        https://github.com/sewenew/redis-plus-plus.git \
    && cmake -S redis-plus-plus -B redis-plus-plus/build \
        -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_PREFIX_PATH=/usr/local \
        -DREDIS_PLUS_PLUS_BUILD_TEST=OFF \
    && cmake --build redis-plus-plus/build --parallel \
    && cmake --install redis-plus-plus/build

COPY CMakeLists.txt /app/CMakeLists.txt
COPY src/ /app/src/

RUN cmake -S /app -B /app/build -DCMAKE_BUILD_TYPE=Release \
    && cmake --build /app/build --parallel

FROM debian:bookworm-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        libstdc++6 ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /usr/local/lib/libhiredis*.so* /usr/local/lib/
COPY --from=builder /usr/local/lib/libredis++.so* /usr/local/lib/
COPY --from=builder /app/build/stream_consumer /usr/local/bin/stream_consumer

RUN ldconfig

ENTRYPOINT ["/usr/local/bin/stream_consumer"]
```

```yaml
# docker-compose.yml
services:
  redis:
    image: redis:7.4.11-alpine3.21
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 2s
      timeout: 2s
      retries: 15

  consumer:
    build: .
    depends_on:
      redis:
        condition: service_healthy
    environment:
      REDIS_HOST: redis
      REDIS_PORT: "6379"
      STREAM_KEY: events
      GROUP_NAME: workers
      CONSUMER_NAME: consumer-1
```

Bring it up and push a message through it:

```bash
docker compose up --build -d
docker compose exec redis redis-cli XADD events '*' sensor temp-01 value 21.6
docker compose logs -f consumer
```

Correct output from the last command:

```
consumer-1  | Created consumer group 'workers' on stream 'events'
consumer-1  | Listening on 'events' as 'consumer-1'
consumer-1  | id=1735000000000-0 sensor=temp-01 value=21.6
```

Confirm the acknowledgement actually happened — the pending-entries list for the group
should be empty after the log line appears:

```bash
docker compose exec redis redis-cli XPENDING events workers
```

An empty first line means `XACK` succeeded; a non-empty one means a message is stuck
unacknowledged, which is the one failure mode this design cannot silently hide.

## Conclusion

**Migrate the boundary that is actually structural, not the whole service.** The cost here
was the interpreter, the GIL and the per-process baseline, costs that exist even while the
consumer is idle. That is a narrow, well-defined seam to rewrite; business logic outside it
buys nothing for the risk added.

**A systems-language rewrite relocates the awkward parts, it does not remove them.**
Ignoring one expected error by matching a substring of its message was necessary in Python
and still is in C++. Reconnection logic and the blocking-read timeout trap are new problems
the move introduced, not ones it solved.

**The real price is memory safety, a standing cost, not a one-time one.** Build complexity
is paid once, inside a Dockerfile, then done. A dangling reference or buffer overrun in a
hand-written loop is paid indefinitely, at whatever hour it surfaces, and no CMake changes
that.

**Do not take the improvement on faith.** Run both consumers side by side under
`docker stats` rather than quote a number — your device, kernel and workload are not the
ones in any article. Measure on the hardware the decision is actually about.
