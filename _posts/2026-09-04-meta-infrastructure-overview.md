---
layout: post
title: "The Whole Estate in One Article: How Every Layer Fits Together"
subtitle: "The dependency order behind a complete self-hosted platform, layer by layer."
date: 2026-09-04 09:00:00 +0200
tags: [infrastructure-as-code, kubernetes, ceph, observability, architecture]
description: >-
  Each layer of a self-hosted platform is well documented in isolation, but
  nothing describes the order they have to arrive in or why getting that
  order wrong fails weeks later rather than immediately. This article walks
  the full dependency chain from bare metal to self-hosted model serving,
  and demonstrates the ordering discipline with a runnable Compose file.
---

## The problem

Every layer of a self-hosted platform has its own documentation, its own getting-started
guide, its own set of well-worn tutorials: how to write an Ansible role, how to bring up a
Kubernetes cluster, how to deploy Ceph as a set of pods on top of one, how to wire an
identity provider in front of a handful of internal tools, how to stand up a metrics and
logging stack, how to serve a model on your own hardware instead of someone else's.

None of those documents tell you which of the others has to already exist. Each one is
written as though it is the first thing you're doing, because from the point of view of the
software it introduces, it is. In practice these layers are not independent: a cluster's
own networking model assumes node-to-node reachability that has to be settled below it;
storage-that-lives-on-the-cluster assumes the cluster is already trustworthy; identity in
front of internal tools assumes those tools didn't already grow their own logins while
nobody was looking. Get the order wrong and it rarely fails immediately. It fails weeks
later, at the worst possible moment: a node gets drained for maintenance and a "persistent"
volume turns out to have been node-local all along; a new office or a second site needs to
join the cluster and the network layer that should have made that trivial doesn't exist,
so it becomes a redesign instead of a config change; a dozen internal dashboards each have
their own password, because nothing arrived before them to give them a shared one.

This article is the map that's usually missing: not how to configure any one layer, but
the order they have to arrive in, and why the dependency runs in that direction and not the
other one.

## Working through it

### Bare metal and the inventory of truth

Before any automation runs, there has to be something automation can act on consistently:
machines with an operating system installed, SSH reachable, and — critically — a single
inventory that says what a "node" is, what role it plays, and what its stable identifier is
supposed to be. This sounds trivial and is the layer everything above silently trusts.
If two people's mental model of "which machines exist and what they're for" disagree, every
later layer inherits that disagreement without ever being told about it.

### Configuration convergence before anything else runs

Ansible's job at this stage is not to deploy an application; it is to make every node boring
in the same way — the same kernel parameters, the same package baseline, the same users,
the same firewall defaults, the same time synchronisation. Kubernetes and Ceph both assume,
implicitly, that the nodes underneath them are homogeneous enough that a workload scheduled
on any of them behaves the same as on any other. Configuring nodes by hand instead, even
carefully, means every later layer inherits invisible per-node drift that nobody chose and
nobody can easily see.

### A container runtime as a packaging boundary, before orchestration

A container runtime has to exist and be verified, node by node, before an orchestrator is
built on top of it, because the orchestrator's entire job is scheduling containers onto
capacity that can already run them — it does not itself provide the ability to run a
container, it only decides where and how many. Skipping straight to a cluster and
discovering the runtime is subtly misconfigured on one node out of several is a debugging
session that the previous layer, done in order, would have made unnecessary.

### The overlay mesh, before the cluster spans more than one boundary

If every node isn't already sitting on one flat, trusted network — because there is more
than one physical site, or a mix of on-premises and remote capacity — an overlay mesh
needs to exist before the cluster does. A WireGuard-based mesh gives every node a stable
address it can reach every other node at, regardless of what sits between them physically.
Kubernetes' own networking layer, whichever CNI implements it, assumes pod and node
addresses are mutually reachable at a stable address; it does not solve reachability across
sites on your behalf. Building the cluster first and trying to introduce a mesh underneath
it afterwards means renumbering a live cluster's network — closer to a rebuild than a
change, because every component that cached an old address has to be found and corrected.

### The cluster itself, before storage

Once nodes are configured and reachable, the control plane and worker nodes can be joined
into an actual cluster. This has to happen before persistent storage exists as a *cluster
workload*, as opposed to storage that predates the cluster and is merely consumed by it,
because a storage layer like Ceph, deployed through an operator, is itself scheduled as pods
on the cluster it goes on to serve. There is a real circularity here worth naming honestly:
the storage the cluster's own workloads will rely on is, at the moment it's deployed,
itself just another workload on that cluster, trusting a control plane that has to already
be solid.

### Storage, before anything stateful

Ceph is what turns "a pod" into "a pod whose data survives that pod being rescheduled
somewhere else." Deploying anything stateful — a database, an object store, model weights —
before this layer exists means it defaults to storage tied to whatever node it happened to
land on. That looks like it works, right up until a node is drained or replaced and
whatever was "persistent" turns out not to have been, which is a bad way to discover a
storage layer was missing.

### Identity, before anything gets a user interface

An identity provider — OIDC-based single sign-on in front of internal tools — belongs early,
before dashboards and internal utilities start proliferating, for a reason that has nothing
to do with security posture and everything to do with arithmetic: every tool that ships
before identity exists gets its own login, its own user table, its own password reset flow,
because there was nothing else for it to defer to. Migrating N separately-authenticated
tools onto a shared identity provider afterwards is N migrations, each with its own edge
cases and its own risk of locking someone out. Standing up identity first and pointing every
new tool at it from day one is zero migrations, because there was never anything else to
migrate away from.

### Observability, before anything is load-bearing

A metrics, logs, and traces stack needs to exist before the platform is trusted to run
anything that actually matters, because the alternative to it is debugging a storage
rebalance or a pod eviction storm by reading raw logs off individual nodes after the fact.
That is possible. It is not a process — it does not scale past the first incident, it
depends entirely on whoever's doing it knowing which node to look at, and it leaves nothing
behind for the next person who hits a similar problem. Observability deployed after
something has already gone wrong once is observability built in exactly the wrong order:
reactively, around the specific failure that just happened, rather than as a general
capability that would have shown that failure coming.

### Model serving, last, because it depends on everything below it

Self-hosted model serving is the layer with the most dependencies and the least tolerance
for any of them being wrong. It needs a scheduler capable of placing a workload against
specific hardware, which is the cluster. It needs storage for model weights that survives a
reschedule without a slow re-download, which is Ceph — and a slow storage backend under a
multi-gigabyte checkpoint turns into a slow cold start every single time a pod moves,
which is a cost paid on a schedule nobody chose. It needs to be reachable from wherever it's
actually used, which is the mesh. It needs a login in front of it, which is identity. And it
needs a way for someone to notice when it's saturated, or restarting in a loop, which is
observability. None of this is specific to serving a model — it is true of any workload —
but a model-serving workload tends to be the first one heavy and expensive enough that every
weakness in the layers below it stops being theoretical.

## The solution

The build order, stated as a checklist:

1. Bare metal, OS, SSH access, and one inventory that is the single source of truth.
2. Ansible convergence: identical baseline configuration across every node.
3. A verified container runtime on every node.
4. An overlay mesh, if nodes span more than one trusted network.
5. The cluster's control plane and worker nodes.
6. Storage, deployed as a cluster workload once the cluster is trustworthy.
7. Identity, in front of the first internal tool, not the fifteenth.
8. Observability, before anything above it is treated as load-bearing.
9. Model serving, or any other workload that depends on every layer beneath it.

The ordering discipline itself is worth seeing work, even in miniature, because "it starts
in the right order" is a testable property. This Compose file stands in each real layer
with a trivial service that only reports itself ready once the layer beneath it already
has:

```yaml
# docker-compose.yml
services:
  mesh:
    image: alpine:3.20
    command: sh -c "sleep 2 && touch /tmp/ready && echo 'mesh: overlay network is up' && sleep infinity"
    healthcheck:
      test: ["CMD", "test", "-f", "/tmp/ready"]
      interval: 1s
      retries: 10

  cluster:
    image: alpine:3.20
    depends_on:
      mesh:
        condition: service_healthy
    command: sh -c "sleep 2 && touch /tmp/ready && echo 'cluster: control plane is up' && sleep infinity"
    healthcheck:
      test: ["CMD", "test", "-f", "/tmp/ready"]
      interval: 1s
      retries: 10

  storage:
    image: alpine:3.20
    depends_on:
      cluster:
        condition: service_healthy
    command: sh -c "sleep 2 && touch /tmp/ready && echo 'storage: persistent volumes available' && sleep infinity"
    healthcheck:
      test: ["CMD", "test", "-f", "/tmp/ready"]
      interval: 1s
      retries: 10

  identity:
    image: alpine:3.20
    depends_on:
      storage:
        condition: service_healthy
    command: sh -c "sleep 2 && touch /tmp/ready && echo 'identity: single sign-on is up' && sleep infinity"
    healthcheck:
      test: ["CMD", "test", "-f", "/tmp/ready"]
      interval: 1s
      retries: 10

  observability:
    image: alpine:3.20
    depends_on:
      identity:
        condition: service_healthy
    command: sh -c "sleep 2 && touch /tmp/ready && echo 'observability: metrics and logs are flowing' && sleep infinity"
    healthcheck:
      test: ["CMD", "test", "-f", "/tmp/ready"]
      interval: 1s
      retries: 10

  model-serving:
    image: alpine:3.20
    depends_on:
      observability:
        condition: service_healthy
    command: sh -c "sleep 2 && echo 'model-serving: only starts once everything below it is healthy'"
```

Run it:

```bash
docker compose up
```

```
mesh-1            | mesh: overlay network is up
cluster-1         | cluster: control plane is up
storage-1         | storage: persistent volumes available
identity-1        | identity: single sign-on is up
observability-1   | observability: metrics and logs are flowing
model-serving-1   | model-serving: only starts once everything below it is healthy
```

The messages always appear in that order, because each service's `depends_on` with a
`service_healthy` condition blocks its start until the marker file below it exists. Delete
the `depends_on` blocks and the same six services race each other — on a fast machine,
`model-serving` can print its line before `storage` has printed its own, which is exactly
the failure this whole article is about, just compressed into a few seconds instead of
spread across the weeks it actually takes to build a real platform in the wrong order.

## Conclusion

The order these layers arrive in is not tidiness for its own sake; it's a dependency graph,
and violating it doesn't fail at build time — it fails later, at whatever moment first
exercises the missing layer, which is usually the worst possible moment: an incident, a
reschedule, a new site that needed to join cleanly and can't.

Every individual layer here is well documented and, in isolation, unremarkable. Almost all
of the real engineering effort turns out to live in the seams between them, not inside any
single layer's own configuration — which is exactly the part none of the individual guides
can tell you about, because each one only knows about itself.

Retrofitting a lower layer once upper layers already depend on it — a mesh introduced after
the cluster exists, identity introduced after a dozen tools already have their own login —
is not a delayed version of doing it right the first time. It is a separate, larger, and
riskier project, roughly in proportion to how much was already built on top of the gap.

None of this scales with the size of the platform. The same order applies to three nodes
under a desk and to a rack's worth of hardware, because it is the dependency between the
layers that dictates the order, not how much of each layer there happens to be.
