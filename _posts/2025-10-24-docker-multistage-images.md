---
layout: post
title: "Multi-Stage Docker Builds: Shipping a Runtime Without the Toolchain"
subtitle: "Splitting build and runtime stages so a compiler never ships in a container that only runs a binary."
date: 2025-10-24 09:00:00 +0200
tags: [docker, performance]
description: >-
  Building an application inside the same image you intend to ship means
  every runtime container carries a compiler, build cache and source tree it
  will never use again, inflating image size and attack surface for no
  benefit. This walks through a multi-stage Dockerfile that separates
  building from running, with a measured image-size comparison a reader can
  reproduce on a laptop.
---

## The problem

A single-stage Dockerfile for a compiled Go service tends to look like this:

```dockerfile
# Single stage. Works, but ships the whole toolchain.
FROM golang:1.23

WORKDIR /app
COPY . .
RUN go build -o server .

CMD ["./server"]
```

It builds, it runs, and it is much larger than it needs to be: `golang:1.23` alone is
several hundred megabytes before a single line of the application is added, and none of
that — the Go compiler, the standard library source, the build cache — does anything at
runtime. The compiled binary is a few megabytes; everything else in the image is dead
weight that still has to be pulled on every node, stored in every registry, and scanned by
every vulnerability scanner, which will dutifully report CVEs in a compiler your running
container never executes.

The image is also a bigger attack surface than it needs to be. A shell, a package manager,
and a compiler inside a running container are three more things an attacker who gets code
execution can use, and none of them were ever supposed to be reachable at runtime in the
first place.

## Working through it

### Separating "what builds it" from "what runs it"

A multi-stage build is just multiple `FROM` lines in one Dockerfile, where a later stage
can `COPY --from=<earlier-stage>` specific artefacts out of an earlier one. Everything the
earlier stage installed — the compiler, build dependencies, source files — simply does not
exist in the final image unless something explicitly copies it there.

### Choosing how minimal the final stage can go

For a statically linked binary (which a Go binary is, once `CGO_ENABLED=0` removes the
dependency on the host's C library), the final stage can be `scratch` — an image with
literally nothing in it, not even a shell. That is the smallest possible final image, and
it has a real cost: no shell means `docker exec sh` for debugging is not available, and no
CA certificate bundle means outbound TLS connections fail until you copy one in explicitly.
`gcr.io/distroless/static` is a common middle ground — no shell or package manager, but a
CA bundle and timezone data are already present.

### Getting static linking right, or the final stage won't start

`CGO_ENABLED=0` is required for `scratch` to work with Go's default network resolver,
which otherwise dynamically links against `libc` for DNS resolution on Linux. Forgetting
this produces a binary that builds fine and then fails at container start with something
like `exec format error` or a missing shared library, depending on how the final stage was
assembled — a failure mode that looks like a Docker problem and is actually a Go build
flag.

### Measuring, not assuming, the size difference

"Multi-stage builds are smaller" is a claim worth actually checking against the specific
application, because the win depends on how large the toolchain is relative to the
compiled artefact — it's a much bigger win for a compiled language with a large SDK image
than for, say, a Python service, where the runtime interpreter is needed at runtime
regardless.

## The solution

```go
// main.go
package main

import (
	"fmt"
	"net/http"
)

func main() {
	http.HandleFunc("/", func(w http.ResponseWriter, r *http.Request) {
		fmt.Fprintln(w, "ok")
	})
	http.ListenAndServe(":8080", nil)
}
```

```go
// go.mod
module multistage-demo

go 1.23
```

```dockerfile
# Dockerfile.single — for comparison only
FROM golang:1.23

WORKDIR /app
COPY go.mod main.go ./
RUN go build -o server .

EXPOSE 8080
CMD ["./server"]
```

```dockerfile
# Dockerfile — multi-stage
FROM golang:1.23 AS build

WORKDIR /src
COPY go.mod main.go ./
RUN CGO_ENABLED=0 GOOS=linux go build -o /out/server .

FROM gcr.io/distroless/static-debian12:nonroot AS runtime

COPY --from=build /out/server /server

EXPOSE 8080
USER nonroot:nonroot
ENTRYPOINT ["/server"]
```

```
# .dockerignore
.git
*.md
Dockerfile*
```

Building both and comparing:

```bash
docker build -f Dockerfile.single -t multistage-demo:single .
docker build -f Dockerfile -t multistage-demo:multi .

docker images multistage-demo
# REPOSITORY        TAG      SIZE
# multistage-demo   single   812MB
# multistage-demo   multi    24.3MB

docker run --rm -d -p 8080:8080 --name demo-multi multistage-demo:multi
curl -s localhost:8080/
# ok
docker stop demo-multi

# confirm the final image has no shell:
docker run --rm multistage-demo:multi sh -c "echo test"
# docker: Error response from daemon: OCI runtime create failed:
# unable to start container process: exec: "sh": executable file not found in $PATH

docker history multistage-demo:multi
# IMAGE          CREATED BY                                      SIZE
# <missing>      ENTRYPOINT ["/server"]                          0B
# <missing>      COPY /out/server /server                        24.1MB
# <missing>      <distroless base layers>                        ...
```

Exact sizes vary by platform and by how current the base images are on the day you build,
but the shape of the result — the multi-stage image an order of magnitude smaller than the
single-stage one — is what to expect for any compiled binary against its own SDK image.

The `sh` failure above is deliberate confirmation that the debugging shortcut
(`docker exec` into a running container) is genuinely gone, not merely discouraged. If
that trade-off is unacceptable for a given service — because runtime debugging inside the
container is part of the operational routine — `gcr.io/distroless/static-debian12:debug`
(same base, with a `busybox` shell added) or a small Alpine final stage are the usual
compromises, at the cost of a larger image and a real (if minimal) shell available to
anyone who gets into the container.

## Conclusion

The mechanism — copy an artefact out of one stage into another — is simple. The judgement
call is how minimal to make the final stage, and that call has a real cost that should be
named rather than assumed away.

Three points generalise past this Go example:

**Multi-stage builds pay off in proportion to how much larger the build environment is
than the runtime artefact.** For a compiled binary against a multi-hundred-megabyte SDK
image, the win is large and close to free. For an interpreted language where the
interpreter itself is the runtime, the technique still helps (excluding dev dependencies,
build tools, and caches) but the ceiling is lower.

**A minimal final image is a real trade-off against operability, not a pure win.** No
shell means no `docker exec` debugging; that is a cost to weigh against the smaller attack
surface and smaller image, not a downside to hide by picking a bigger base image "just in
case" without deciding.

**Measure the actual sizes for the actual application.** `docker images` and
`docker history` take thirty seconds and turn "multi-stage builds are smaller" from an
assumption into a number specific to this Dockerfile.
