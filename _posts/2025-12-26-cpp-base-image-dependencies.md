---
layout: post
title: "A Shared Multi-Stage Base Image for a C++ Service Fleet"
subtitle: "Compiling the JSON library and HTTP framework once, in a base image, instead of a dozen times."
date: 2025-12-26 09:00:00 +0200
tags: [cpp, docker, performance]
description: >-
  A dozen C++ services that each install and build the same HTTP framework
  and JSON library from source turn every Docker build into a multi-minute
  wait, for a change that touched one function. Here is a shared, versioned
  base image that compiles those dependencies once, and per-service
  Dockerfiles that only ever rebuild the service's own code against it.
---

## The problem

A small fleet of C++ services shares the same shape: an HTTP entry point, JSON in and
out, a handful of endpoints. Each one has its own `Dockerfile`, and each one starts more
or less the same way:

```dockerfile
# One of a dozen near-identical Dockerfiles. Do not copy this.
FROM debian:12.7-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential cmake ninja-build git curl zip unzip tar pkg-config \
    && rm -rf /var/lib/apt/lists/*
RUN git clone --depth 1 https://github.com/microsoft/vcpkg.git /opt/vcpkg \
    && /opt/vcpkg/bootstrap-vcpkg.sh -disableMetrics
RUN /opt/vcpkg/vcpkg install nlohmann-json:x64-linux pistache:x64-linux
COPY . /src
RUN cmake -S /src -B /src/build -G Ninja \
    -DCMAKE_TOOLCHAIN_FILE=/opt/vcpkg/scripts/buildsystems/vcpkg.cmake \
    && cmake --build /src/build
```

Nothing here is wrong in isolation. The problem is that the expensive line —
`vcpkg install` — is duplicated in every one of those Dockerfiles, byte for byte, and
each service compiles the same HTTP framework and the same JSON library from source. A
JSON library that is header-only is nearly free. A real HTTP framework is not: it has
its own dependency chain, its own translation units, and building it from scratch is
routinely the single slowest step in the whole image, independent of how small the
service's own source code is.

The result is a build that does not scale with the size of the change. Editing one
handler in one service should be a fast, boring rebuild. Instead, on a CI runner with no
warm cache, or after a base OS image bump that invalidates the layer, every one of those
dozen services pays the same multi-minute dependency build again, for the same libraries,
compiled to the same output, twelve separate times.

It is also a build that drifts without anyone deciding to let it drift. Docker's build
cache is content-addressed, so as long as every Dockerfile has byte-identical
instructions above the `vcpkg install` line, a single developer's laptop can sometimes
reuse that layer across services. But that guarantee is fragile: one service adds an
`apt-get install` for a debugging tool, another orders its `COPY` differently, someone
bumps only that service's Debian base tag ahead of the rest — and the shared layer stops
being shared, silently, because cache identity depends on everything upstream of it
matching exactly. Nobody notices until a routine change takes ten times longer than it
used to, and by then nobody remembers which Dockerfile diverged first.

## Working through it

### Separating what changes often from what does not

A service's own code changes on every commit. The HTTP framework and the JSON library it
links against change on a cadence closer to "a few times a year, deliberately". Those two
things belong in different images, built by different people at different times, for a
straightforward reason: the artefact that is expensive to build should be the one that
is rebuilt least often, and the artefact that changes constantly should be the one that
is cheap to rebuild.

That split is exactly what a base image is for. Build the dependencies once, tag the
result, and have every service's `Dockerfile` start `FROM` that tag instead of from the
bare OS image. The dependency build then happens once per version bump, not once per
service per build.

### Building the shared base once

The base image only needs a toolchain and the two libraries every service links against.
Pinning matters twice here: the OS base image, and the exact version of the libraries
vcpkg resolves, so that "rebuild the base" is a deliberate, reviewable change rather than
something that silently pulls newer code.

```dockerfile
# base/Dockerfile
FROM debian:12.7-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    cmake \
    ninja-build \
    git \
    curl \
    zip \
    unzip \
    tar \
    pkg-config \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Pin vcpkg itself, so the exact versions it resolves for nlohmann-json and
# Pistache are reproducible. Check https://github.com/microsoft/vcpkg/releases
# and pin whichever tag you choose to.
ARG VCPKG_REF=2024.07.12
RUN git clone --branch ${VCPKG_REF} --depth 1 \
    https://github.com/microsoft/vcpkg.git /opt/vcpkg \
    && /opt/vcpkg/bootstrap-vcpkg.sh -disableMetrics

ENV VCPKG_ROOT=/opt/vcpkg
ENV PATH="${VCPKG_ROOT}:${PATH}"

# This is the step every service used to repeat. It happens exactly once per
# base image version, here, and nowhere else.
RUN vcpkg install nlohmann-json:x64-linux pistache:x64-linux
```

`nlohmann-json` is header-only, so it costs almost nothing to "build". Pistache is a
real, compiled HTTP framework with its own source tree, and it is the line item that
actually takes minutes on ordinary laptop hardware. That asymmetry is normal — most fleets
have one or two dependencies that dominate build time, and those are the ones worth
isolating.

### Reusing the base from each service without rebuilding anything

A service's own `Dockerfile` now has nothing to install. It configures against the
libraries that are already sitting, pre-built, in the base image's layer:

```dockerfile
# services/order-service/Dockerfile
FROM cpp-fleet-base:2024.07.12 AS build

WORKDIR /src
COPY CMakeLists.txt .
COPY src ./src

RUN cmake -B build -G Ninja \
    -DCMAKE_TOOLCHAIN_FILE=${VCPKG_ROOT}/scripts/buildsystems/vcpkg.cmake \
    -DVCPKG_TARGET_TRIPLET=x64-linux \
    -DCMAKE_BUILD_TYPE=Release \
    && cmake --build build

FROM debian:12.7-slim AS runtime
RUN apt-get update && apt-get install -y --no-install-recommends \
    libstdc++6 \
    libssl3 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=build /src/build/order-service /usr/local/bin/order-service
EXPOSE 9080
ENTRYPOINT ["/usr/local/bin/order-service"]
```

`cmake --build` here only ever compiles `src/main.cpp` and links it against the static
archives vcpkg already produced in the base image. Nothing under `/opt/vcpkg/installed`
is touched. A one-line change to a handler is now a rebuild of one translation unit, not
a rebuild of a whole HTTP framework.

### Keeping the runtime image slim despite a heavy builder

The base image is not small — it carries a full C++ toolchain, `git`, and vcpkg's build
artefacts, easily the better part of a gigabyte. That is fine, because nothing downstream
of the `build` stage ships it. The second `FROM` in the service `Dockerfile` starts a
fresh, minimal image and copies across only the compiled binary. Run `ldd` on that binary
before deciding which runtime packages to install — it tells you exactly which shared
libraries it needs, rather than guessing:

```bash
docker run --rm --entrypoint ldd cpp-fleet-base:2024.07.12 /src/build/order-service
```

vcpkg's default Linux triplet (`x64-linux`) links `nlohmann-json` and Pistache
statically, so the only shared libraries left are ordinary system ones — `libstdc++`,
`libssl` if Pistache was built with TLS support, `libpthread`. Anything the runtime stage
installs beyond what `ldd` actually lists is dead weight.

### Making a dependency bump an explicit, single-line change

Because the version lives in one place — the base image's `ARG VCPKG_REF` and its tag —
upgrading Pistache or `nlohmann-json` across the whole fleet is one build, of one image,
reviewed once. Every service picks it up by bumping the tag in its own `FROM` line, on
its own schedule, and a service that has not been touched keeps building against the old
base until someone deliberately moves it forward. That is the property that was missing
before: the fleet's dependency versions were twelve independent copies of the same
decision, made once and then left to drift.

## The solution

The full, runnable layout: one base image, two example services (a dozen would look the
same, just more directories), and a Compose file that builds both against the shared
base.

{% raw %}
```cpp
// services/order-service/src/main.cpp
#include <pistache/endpoint.h>
#include <nlohmann/json.hpp>

using namespace Pistache;
using json = nlohmann::json;

class OrderHandler : public Http::Handler {
public:
    HTTP_PROTOTYPE(OrderHandler)

    void onRequest(const Http::Request&, Http::ResponseWriter response) override {
        json body = {{"service", "order-service"}, {"status", "ok"}};
        response.send(Http::Code::Ok, body.dump(), MIME(Application, Json));
    }
};

int main() {
    Address addr(Ipv4::any(), Port(9080));
    auto server = std::make_shared<Http::Endpoint>(addr);
    server->init(Http::Endpoint::options().threads(2));
    server->setHandler(Http::make_handler<OrderHandler>());
    server->serve();
}
```
{% endraw %}

```cmake
# services/order-service/CMakeLists.txt
cmake_minimum_required(VERSION 3.22)
project(order-service CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

find_package(nlohmann_json CONFIG REQUIRED)
find_package(Pistache CONFIG REQUIRED)

add_executable(order-service src/main.cpp)
target_link_libraries(order-service PRIVATE
    nlohmann_json::nlohmann_json
    Pistache::Pistache)
```

`services/inventory-service` is the same shape: identical `Dockerfile`, identical
`CMakeLists.txt` apart from the target name, a `main.cpp` that returns a different JSON
body from a different port. Copy the two files above, rename the executable and the
`json` payload, and it is a second, independent service built against the same base.

```yaml
# docker-compose.yml
services:
  order-service:
    build:
      context: ./services/order-service
    image: fleet/order-service:latest
    ports:
      - "9080:9080"

  inventory-service:
    build:
      context: ./services/inventory-service
    image: fleet/inventory-service:latest
    ports:
      - "9081:9080"
```

Compose builds services from their own `Dockerfile`; it does not know how to build a base
image referenced only in a `FROM` line, so that stays a separate, explicit step — which
is correct, since it is meant to run far less often than either service build.

```bash
# One-time (or once per dependency bump): build the shared base.
# This is the step that takes real wall-clock time — Pistache compiles from
# source here, and nowhere else.
time docker build -t cpp-fleet-base:2024.07.12 -f base/Dockerfile base

# Every day: build both services against the base that is already sitting
# on this machine. Neither of these touches vcpkg at all.
time docker compose build order-service
time docker compose build inventory-service

# Confirm the win: rebuilding after a source change only recompiles the
# service's own file, not the framework.
touch services/order-service/src/main.cpp
time docker compose build order-service
```

What to look for: the first `docker build` is the slow one, dominated by the
`vcpkg install` line. Both `docker compose build` runs afterwards, and the rebuild after
`touch`, should be visibly faster — CMake's configure step reports the libraries as
already found, and the only compiler invocation is for `main.cpp`. Confirm the runtime
images stayed thin despite the heavy builder:

```bash
docker images | grep -E 'cpp-fleet-base|fleet/'
```

`cpp-fleet-base` is the large one, holding the whole toolchain and vcpkg's build output.
`fleet/order-service` and `fleet/inventory-service` should each be a small fraction of
that size, because their final stage copied across a binary and two runtime packages,
nothing else.

## Conclusion

**Separate what is expensive to build from what changes often.** The base image pattern
is not specific to C++ or to these two libraries; it applies to any stack where a shared,
slow-to-build dependency sits underneath fast-moving service code — the same argument
holds for a shared Python wheel cache or a shared set of compiled Go modules.

**A shared base image is also a shared decision, made once.** Before it, "which version
of the HTTP framework are we on" had a dozen answers, one per service, updated whenever
whoever touched that service last happened to bump it. After it, the answer is the tag on
one image, and moving the fleet forward is a deliberate, visible change rather than an
accumulation of small, independent ones.

**This costs something too, and it is worth naming.** The base image is a new artefact
someone has to build, tag, push and keep an eye on, and a service now depends on that
image existing and being reachable, not just on its own source tree. For two services
that trade-off may not be worth it; for a dozen sharing the same expensive dependency, it
usually is.

**Multi-stage builds are what make the trade-off free at the runtime end.** The base
image can be as large as it needs to be — it never ships. Only the final stage's `COPY
--from=build` decides what actually reaches production, which is why a heavy shared
builder and a slim running container are not in tension with each other.
