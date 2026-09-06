---
layout: post
title: "Alert on symptoms, not on metrics"
subtitle: "Monitoring that tells you a user is unhappy, instead of that a number moved."
date: 2026-09-06 10:00:00 +0200
tags: [Monitoring, Prometheus, Alerting]
description: >-
  Most monitoring failures are not missing data. They are the wrong alert on
  good data — and the one class of outage nobody catches is the one where every
  metric is green.
---

The first monitoring system I built alerted on CPU. It worked exactly as designed and was
almost entirely useless.

High CPU is not a problem. It is sometimes a *sign* of a problem, and it is sometimes a
machine doing its job well. Alerting on it taught everyone the single worst lesson a
monitoring system can teach: that alerts are things you glance at and dismiss. Once people
learn that, the system is dead, and the outage it eventually catches correctly will be
dismissed along with everything else.

## The rule that fixed it

**Alert on what the user experiences. Graph everything else.**

If a page is slow, alert. If a queue is not draining, alert. If a certificate expires in
three days, alert. If CPU is at 90%, put it on a dashboard and leave me alone — I will
look at it when I am investigating the alert that actually fired.

This distinction is not about which metrics you collect. Collect everything; it is cheap
and you will want it during an incident. It is about which ones are allowed to wake
someone. The test I use: *if this fires and I do nothing, will anybody notice?* If the
honest answer is no, it is a graph, not an alert.

The corollary is uncomfortable: an alert that has fired ten times and been ignored ten
times is not a monitoring gap, it is a monitoring *bug*, and the fix is to delete it or
change it. Leaving it in place is choosing to train your team to ignore alerts.

## Black-box and white-box are answering different questions

White-box monitoring reads a system's own account of itself: metrics the service exports,
queue depths, internal error counters. It tells you *why*.

Black-box monitoring is an outside observer doing what a user does: fetch the URL, resolve
the name, complete the handshake. It tells you *whether*.

You need both, and the mistake is thinking the first can replace the second. A service can
report itself perfectly healthy while being completely unreachable, because everything it
knows how to measure is inside the boundary that broke. Every layer between the process
and the user — DNS, the load balancer, certificates, firewall rules, the ingress — is
invisible to it.

Black-box checks are also the ones that catch the failures where nothing is broken *at
all*, which brings me to the class of outage I find most interesting.

## The failure where every metric is green

Here is a shape worth internalising, because it generalises far beyond the specific
mechanism.

DNS wildcards do not work the way most people assume. A wildcard record synthesises an
answer for a name only if nothing exists beneath that name. Put any record — any type at
all — under a name, and the name now *exists*, and the wildcard is forbidden from
answering for it. The name then resolves with a perfectly successful response code and an
empty answer. No error. No failure. Just no address.

The practical consequence: a leftover record from a process that was supposed to clean up
after itself can take a hostname off the internet, silently, while every neighbouring name
served by the same wildcard keeps working perfectly. Query a random name under that
wildcard and it answers. Query the real one and you get success-with-nothing.

Now consider what your monitoring says. The service is up. Its metrics are green. Its
host is healthy. Certificates are valid. Every dashboard is fine, because the failure is
not in anything any of those things measure — it is in the resolution step that happens
before anyone reaches the service at all.

The only thing that catches this is an outside observer asking a public resolver for the
name you actually publish and checking that the answer contains an address. Not that the
query succeeded — that it *returned something*.

I like this example because it is not exotic. It is a category: **failures where every
component reports success and the composition still does not work.** Certificate chains
that validate individually and not together. A load balancer routing happily to a backend
pool that is empty. Auth that returns 200 with a redirect loop. If your monitoring only
ever asks components how they feel, none of these are visible.

## What a good alert contains

An alert is a message to a tired person. Write it for them.

It should say what is broken in user terms, since that is what determines urgency. It
should say how you know, so the first move is not re-deriving the query. It should point
at the thing to look at. And it should be honest about severity — if it is not worth
waking someone, it should not be able to.

The failure mode here is alerts written as an expression and a name and nothing else. Six
months later, at 3am, `ProbeFailureRateHigh` on a service you did not deploy tells you
nothing about whether to care.

## Cardinality, briefly

The other way monitoring dies is by becoming too expensive to run, and the cause is almost
always labels. Every distinct combination of label values is a separate time series. Put a
user ID, a request path, or anything else unbounded in a label and you have built a system
whose cost grows with your traffic in a way you did not intend.

The rule: labels are for things you would *group by*. If you would never write a query
grouping on it, it is not a label, it is a log field.

## The uncomfortable part

Good monitoring is mostly deletion. The instinct when something breaks is to add an alert
for it, and after two years you have three hundred alerts, most of which have never fired
usefully, and a team that has learned the notification channel is noise.

Every alert should have to justify its continued existence. When was it last right? What
did someone do about it? If it has never been right, it is not protecting you — it is
using up the attention you will need for the alert that matters.

I would rather have twelve alerts that everyone trusts than three hundred that everyone
ignores. The twelve will catch fewer things. They will catch them at 3am, when someone
actually reads them.
