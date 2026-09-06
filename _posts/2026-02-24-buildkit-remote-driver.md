---
layout: post
title: "Running BuildKit as a Remote Builder Without a Docker Daemon"
subtitle: "buildx's default driver needs a docker.sock that a containerd-based CI worker does not have"
date: 2026-02-24 09:00:00 +0200
tags: [docker, ci-cd]
description: >-
  A CI runner scheduled on a containerd node has no Docker daemon to hand buildx, so the
  default docker-container driver cannot even start. This article sets up BuildKit as a
  standalone, always-on builder that buildx talks to over TLS instead, with a complete
  docker compose file, certificate generation and the exact buildx commands a pipeline
  needs.
---

## The problem

`docker buildx build` looks like a single command, but by default it needs somewhere to
run BuildKit itself. The `docker-container` driver, which is what you get out of the box,
creates that somewhere by asking the local Docker daemon to start a BuildKit container:

```bash
docker buildx create --use
docker buildx build -t demo:local .
```

```
error during connect: Get "http://%2Fvar%2Frun%2Fdocker.sock/v1.24/...":
open /var/run/docker.sock: no such file or directory
```

On a CI worker scheduled as a plain container under containerd — a Kubernetes job
without Docker-in-Docker, a Buildkite or GitLab agent running as a pod rather than a VM —
there is no `docker.sock` to ask. There is a container runtime, but it belongs to the
node, is not exposed to the workload, and mounting it in is exactly the "give CI a
socket that grants root on the node" problem most of these platforms are configured to
prevent.

The instinctive fixes make it worse. Docker-in-Docker means running a privileged
container to get a daemon you then throw away at the end of the job — real image-cache
loss between runs, plus the container runtime policy exception you were trying to avoid
in the first place. Falling back to a plain `docker build` does not exist as an option:
under containerd there is no `docker` at all, only a build tool that can talk to a
builder.

The part that is easy to miss: BuildKit itself does not need a Docker daemon. It is a
standalone build engine — `docker-container` is only one way to reach it, and it happens
to be the one that requires a daemon. buildx has a `remote` driver that connects straight
to an already-running BuildKit instance over gRPC, and that instance can live anywhere
that can be reached over the network.

## Working through it

### Separating "the tool that builds" from "the tool that talks to it"

`buildx` is a client. It sends a build request and streams back progress; it does not
itself compile anything. `docker-container` bundles "start a builder" and "talk to it"
into one driver because that is convenient on a workstation with Docker installed. The
`remote` driver splits those apart: something else is responsible for having BuildKit
running, and `buildx` just needs an address to connect to.

That split is what makes a containerd-based CI worker workable: BuildKit runs once, as a
long-lived service, wherever it is convenient to run a normal (non-privileged-from-the-
worker's-perspective) container — a dedicated node, a small VM, a Kubernetes deployment
outside the CI runners' own restricted namespace. The CI job's only requirement becomes
network reachability and a certificate.

### Exposing BuildKit safely

`buildkitd` can listen on a TCP address, but an unauthenticated BuildKit listener is
equivalent to a root shell on whatever runs it — a build can mount and write anything
the container can. It ships with an `--addr` flag plus TLS flags, so treat this exactly
like any other remotely reachable privileged service: client-certificate authentication,
not just server TLS.

### Pointing buildx at it

Once a certificate-authenticated `buildkitd` is running somewhere reachable, the client
side is one `buildx create` call, and it needs no daemon at all:

```bash
docker buildx create \
  --name remote-builder --driver remote \
  --driver-opt servername=buildkitd \
  --driver-opt cacert=./certs/ca.pem \
  --driver-opt cert=./certs/client.pem \
  --driver-opt key=./certs/client-key.pem \
  tcp://buildkitd:1234
docker buildx use remote-builder
```

Everything downstream — `docker buildx build`, cache import/export, multi-platform
builds — behaves exactly as it does with `docker-container`, because from buildx's point
of view it is still just talking to a BuildKit gRPC endpoint. Only how it got there
changed.

## The solution

A complete, laptop-runnable setup: certificates, a standalone `buildkitd` service, and a
build run against it with no Docker daemon involved in the build itself (the `buildkitd`
container is the only place BuildKit runs; `docker compose` is only used here to stand
the demo up).

```bash
#!/usr/bin/env bash
# gen-certs.sh — a minimal CA plus server and client certs for buildkitd
set -euo pipefail
mkdir -p certs && cd certs

openssl req -x509 -newkey rsa:2048 -days 365 -nodes \
  -keyout ca-key.pem -out ca.pem -subj "/CN=buildkit-demo-ca"

openssl req -newkey rsa:2048 -nodes -keyout server-key.pem -out server.csr \
  -subj "/CN=buildkitd"
openssl x509 -req -in server.csr -CA ca.pem -CAkey ca-key.pem -CAcreateserial \
  -days 365 -out server.pem \
  -extfile <(printf "subjectAltName=DNS:buildkitd")

openssl req -newkey rsa:2048 -nodes -keyout client-key.pem -out client.csr \
  -subj "/CN=buildkit-client"
openssl x509 -req -in client.csr -CA ca.pem -CAkey ca-key.pem -CAcreateserial \
  -days 365 -out client.pem
```

```yaml
# docker-compose.yml
services:
  buildkitd:
    image: moby/buildkit:v0.13.2
    privileged: true
    command:
      - --addr=tcp://0.0.0.0:1234
      - --tlscacert=/certs/ca.pem
      - --tlscert=/certs/server.pem
      - --tlskey=/certs/server-key.pem
    volumes:
      - ./certs:/certs:ro
    ports:
      - "1234:1234"
```

```dockerfile
# app/Dockerfile — a trivial image to build against the remote builder
FROM alpine:3.20
RUN echo "built by a remote BuildKit, no docker.sock involved" > /note.txt
CMD ["cat", "/note.txt"]
```

```bash
./gen-certs.sh
docker compose up -d
sleep 2

docker buildx create \
  --name remote-builder --driver remote \
  --driver-opt servername=buildkitd \
  --driver-opt cacert=./certs/ca.pem \
  --driver-opt cert=./certs/client.pem \
  --driver-opt key=./certs/client-key.pem \
  tcp://localhost:1234
docker buildx use remote-builder

docker buildx build --load -t buildkit-remote-demo:local ./app
docker run --rm buildkit-remote-demo:local
```

```
[+] Building 1.2s (5/5) FINISHED
 => [internal] load build definition from Dockerfile
 => [1/1] RUN echo "built by a remote BuildKit, no docker.sock involved" > /note.txt
 => exporting to docker image format
built by a remote BuildKit, no docker.sock involved
```

`docker buildx create` never touched a local `docker.sock` for the build itself — it
only used one here, on the laptop, to stand up the demo's `buildkitd` container via
`docker compose`. In a containerd-based CI worker, `buildkitd` would already be running
as a standing deployment (on a node or namespace that is allowed a privileged workload),
and the job would run only the `buildx create` / `buildx build` block above against that
address.

## Conclusion

**A CLI's default driver is a convenience, not the tool's actual requirement.** buildx
needing a Docker daemon is an artefact of `docker-container` being the default driver,
not a property of BuildKit. Read past the default before concluding a platform cannot do
something.

**Run privileged, long-lived infrastructure once, and give many short-lived workers
network access to it, rather than giving every worker the privilege itself.** A single
`buildkitd` under a policy exception is a much smaller surface than granting every CI job
a socket that behaves like root on its node.

**Authenticate a shared build daemon like you would any other privileged service.** An
open BuildKit TCP listener is a bigger problem than the daemon you removed by no longer
using Docker-in-Docker; client-certificate authentication is not optional here.
