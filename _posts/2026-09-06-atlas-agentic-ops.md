---
layout: post
title: "Running the models yourself"
subtitle: "atlas: self-hosted inference, honest benchmarks, and letting agents near infrastructure."
date: 2026-09-06 13:00:00 +0200
tags: [LLM, Self-hosting, Agents, Python]
description: >-
  Sending every prompt to someone else's GPU is a decision, not a default. What
  it takes to serve models on hardware you own, how to know whether they are
  actually good, and what has to be true before an agent touches production.
---

Every prompt you send to a hosted model is a copy of your data leaving your building. For
a lot of work that is completely fine. For infrastructure work — where the context you
would naturally paste is config, logs, topology, sometimes credentials — it deserves at
least a decision rather than a default.

That is the reason atlas exists: a self-hosted inference stack, an OpenAI-compatible API in
front of it, and benchmarks to tell me whether the models are actually any good.

## Why own the hardware

**Data locality.** The strongest argument. Debugging context is exactly the material you
would least like to send somewhere else. Local inference makes that a non-question rather
than a policy you have to remember.

**Predictable cost.** Hosted inference is priced per token, which means your bill is a
function of how useful you find it. That is a strange incentive — it makes people ration a
tool that gets better the more you use it. A GPU is a fixed cost and marginal use is free.

**Latency and availability.** No round trip, no rate limits, no dependency on somebody
else's capacity planning.

**It still works when the internet does not.** Which is precisely when you are debugging.

The honest counterweight: the best hosted models are better than what you can run
locally, and the gap on hard reasoning is real. Local models are excellent at the bulk of
practical work — summarising, transforming, drafting, structured extraction, code you will
review anyway — and noticeably weaker at the genuinely difficult problems. Pretending
otherwise leads to disappointment. Use both, and know which is which.

## Serving them

The practical shape is a model server behind an API. Three things matter more than the
choice of server:

**Model swapping.** You cannot hold every model in VRAM at once, and you do not want a
separate service per model. A layer that loads on demand and evicts what is idle turns a
fixed allocation into a pool. It costs a cold-start delay on the first request, which is
almost always the right trade.

**An OpenAI-compatible API.** Not because that API is well designed, but because
everything already speaks it. Adopting it means every tool, library and editor plugin
works against your own hardware with a changed base URL and nothing else. Compatibility
with a widespread interface beats a better interface nobody implements.

**Quantisation is the lever.** The difference between a model that does not fit and one
that fits comfortably is usually quantisation, and the quality cost at sensible levels is
much smaller than people expect. This is what decides whether a given GPU is useful.

## "It feels smarter" is not a measurement

This is the part I would push hardest.

Model evaluation by vibes is the norm and it is worthless. You try a new model, ask it a
few things, form an impression, and swap. Your impression is shaped by the last thing you
happened to ask, by prompt phrasing, and by wanting the new thing to be better.

So atlas has benchmarks: a fixed set of tasks representative of what I actually use models
for, run against every served model, repeatably. Not academic benchmarks — those measure
something real but not necessarily the thing you need. Your own tasks, scored consistently.

What this buys you is the ability to answer *did that upgrade help?* with evidence. It
also produces genuinely surprising results. Bigger is not reliably better for a specific
task. A quantisation step you assumed was lossy may cost nothing measurable on your
workload. The model everyone recommends may be worse at your actual job than a smaller one.

You cannot learn any of that from impressions.

## Agents touching infrastructure

The interesting and dangerous part. An agent that can read logs, query metrics and propose
changes is enormously useful. An agent that can *apply* changes is a different risk
category, and the difference is worth being deliberate about.

The rules I hold to:

**Read-only by default, and generously so.** Reading logs, metrics, configuration and
state covers most of the value. Most of what makes infrastructure work slow is gathering
context, not typing the fix. An agent that only reads is already worth having.

**Dry run before apply, always.** Every serious infrastructure tool can show you what it
would do. Agents should be required to use it, and the output is the thing a human reads.
This is not agent-specific — it is the same discipline that should apply to a person — but
agents make it non-negotiable, because an agent's confidence is uncorrelated with its
correctness.

**A human approves anything that changes state.** Not a rubber stamp: the dry-run output,
read, then approved. The agent's job is to do the work and present the evidence. The
decision stays with a person.

**Never on a base branch, never without a trail.** Agent work goes through the same
process as human work — a branch, a pull request, a review, a merge. Partly for safety,
mostly so that in six months you can find out why something is the way it is.

**Evidence, not assertion.** The failure mode that matters is not an agent doing something
destructive — that is what approval gates are for. It is an agent *reporting* that
something is fixed, deployed or passing without having verified it. A claim without the
command output behind it is worth nothing, and it is worse than nothing if it is believed.
I would rather be told "I could not verify this" than be told a plausible falsehood.

## What it looks like in practice

The workflow that has actually stuck: an agent gathers context and proposes, a human
decides, a pipeline applies.

The agent reads the logs, correlates the metrics, finds the relevant config, and produces
a diff with a dry run attached. I read the dry run. If it is right, it goes through the
normal review path. The time saved is in the gathering, which is most of the time, and
none of the judgment has moved.

That is a much less exciting story than autonomous operations, and it is the one that
survives contact with production. The value is real and it is in the boring half.

## Where this is going

The direction I find genuinely promising is not agents that act more autonomously. It is
agents with better context — able to read the repository, the monitoring, the runbooks and
the incident history together, and tell you what changed and what it correlates with.

Most infrastructure problems are not hard to fix once understood. They are hard to
understand because the relevant information is spread across six systems and one person's
memory. That is a retrieval and correlation problem, and it is one that models are
genuinely good at right now, on hardware you can own, without anything leaving the
building.
