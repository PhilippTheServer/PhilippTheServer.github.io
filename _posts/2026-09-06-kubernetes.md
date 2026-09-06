---
layout: post
title: "Kubernetes, and when it earns its place"
subtitle: "It is not a better Docker. It is a different bargain, and the price is real."
date: 2026-09-06 08:00:00 +0200
tags: [Kubernetes, ArgoCD, GitOps]
description: >-
  Kubernetes is not the next step up from Docker Compose — it is a trade, and
  most people are told about the benefits without being told the price. Here is
  what it actually bought, and what it cost.
---

I run both. A Docker cluster carries a large share of the workloads, and a Kubernetes
cluster carries the rest. People find this inconsistent. It is the most deliberate
decision in the whole estate.

The framing that gets everyone into trouble is that Kubernetes is what you graduate to
when Compose stops being enough. That makes it sound like a bigger version of what you
already have. It is not. It is a different bargain: you hand over control of *where things
run* and *when they restart*, and in exchange you get a system that keeps working when a
machine dies. If you do not need the second thing, you have paid for the first for nothing.

## What you actually give up

The thing nobody warns you about is that debugging changes shape.

With Compose, a service is a process on a host you can name. Something is wrong, you SSH
in, you read the logs, you see the process. The mental model is one hop deep. With
Kubernetes, "where is it running" is a question with a query attached, and the answer
changes. Between you and the process there is now a scheduler, a CNI plugin, a service
abstraction, an ingress, and a set of health checks any one of which can be the reason
nothing is responding.

That is not a criticism. Every one of those layers is doing something you asked for. But
it means your debugging is no longer "read the log", it is "work out which layer is
lying". The first few times, that takes hours, and you will feel it acutely because the
equivalent Compose problem would have taken minutes.

You also give up a certain kind of quick fix. On a single host, restarting a container to
clear a bad state is a legitimate move. In a cluster, whatever put the container in that
state will do it again, on a different node, at a worse time. The cluster removes your
ability to paper over things — which is good, and which does not feel good.

## What you get

**Machine failure stops being an incident.** This is the whole thing. On a single host, a
dead disk is a phone call. In a cluster with a real failure domain, it is a rescheduling
event you read about later. Everything else Kubernetes gives you is a consequence of the
control loop that makes this work.

**Deployment becomes declarative all the way down.** With GitOps — a controller watching a
repository and reconciling the cluster toward it — the deployment story becomes identical
to the infrastructure story: the repository is the truth, and the cluster converges on it.
Nobody deploys. They merge. The cluster notices.

That is a genuinely different operational posture. There is no deploy script that can be
run with the wrong arguments, because there is no deploy script. There is no "did the
staging change get applied to production" because the answer is in git. Rolling back is a
revert.

**Capacity becomes fungible.** Once workloads are not pinned to hosts, adding a node adds
capacity to everything at once, instead of to whichever service you decided lives there.

## When it does not earn its place

If you have one machine, Kubernetes gives you nothing except the layers. A single-node
cluster has the failure characteristics of a single node plus the debugging surface of a
cluster. That is the worst of both, and it is where an enormous number of installations
actually sit.

If your workloads are not replicable — a database with local state, something with a
license tied to a MAC address, a service that cannot tolerate being moved — then the
scheduler cannot do the thing you are paying it for. You can pin them, and people do, but
at that point you have a very elaborate way of running a process on a specific host.

If nobody on the team wants to learn it, it will rot. This is the one people find rude and
it is the most reliable predictor I know. Kubernetes has a real learning curve and a fast
release cadence. A cluster nobody is curious about becomes a cluster nobody upgrades,
and an unpatched cluster is a worse liability than the Compose setup it replaced.

## The split I actually run

So the division is not ideological, it is about what each workload needs.

Things that are stateless, replicable, and benefit from surviving a node failure go to the
cluster. Things that are pinned by their nature, or that are simple enough that the
cluster would only add layers, stay on Docker. The test I apply is: *if this host died
right now, do I want the system to handle it, or do I want to be told?* Both are legitimate
answers. Pretending only one is legitimate is how you end up with a database in a pod
that reschedules itself away from its disk.

The cost of running both is real — two deployment paths, two sets of habits. It is smaller
than the cost of forcing everything into either one.

## Things I would tell myself earlier

**Learn the failure modes before you need them.** Take a node out on purpose, on a
weekday, while you are calm. Watch what happens. The whole value proposition is behaviour
under failure, and if you have never seen it, you do not actually know whether you have it.

**Resource requests are not paperwork.** They are how the scheduler makes decisions. Leave
them unset and you have asked it to pack your machines by guesswork, and it will guess
wrong under exactly the load that made you care.

**The ingress layer will surprise you.** More outages come from the path into the cluster
than from anything scheduling-related. Certificates, DNS, the exact behaviour of the
controller when two things claim the same hostname — that is where the sharp edges are.

**Do not let the cluster become the place where architecture goes to be forgotten.** It is
easy to add one more deployment, then another, until nobody can say what runs there or
why. The cluster does not organise your system for you. It just makes disorganisation
survive node failures.

Kubernetes is a good answer to a question a lot of people have not actually asked. Ask the
question first: *what do I want to happen when a machine dies?* If the honest answer is
"someone will notice and fix it, and that is fine" — then it is fine, and you have just
saved yourself a great deal of YAML.
