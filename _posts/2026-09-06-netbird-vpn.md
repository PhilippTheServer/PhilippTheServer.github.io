---
layout: post
title: "An overlay mesh, and the DNS trap underneath it"
subtitle: "WireGuard between every pair of machines, and the failure mode nobody tells you about."
date: 2026-09-06 11:00:00 +0200
tags: [NetBird, WireGuard, DNS, Networking]
description: >-
  An overlay mesh gives every machine a stable address and an encrypted path to
  every other one. It also gives your internal names a way to silently resolve
  somewhere else.
---

The traditional VPN is a hub. Everything dials in to one concentrator, all traffic goes
through it, and that box is simultaneously your security boundary, your bandwidth
bottleneck, and your single point of failure. It works. It has worked for thirty years.
It also means two machines sitting in the same room send their traffic through a data
centre in another country to talk to each other.

An overlay mesh inverts that. Every machine gets a stable address on a private overlay
network, and any two of them establish a direct encrypted tunnel — peer to peer, not
through a hub. A control plane handles identity, key distribution and NAT traversal, then
gets out of the path. WireGuard does the actual encryption.

The result is that "which network is this machine on" stops being a question you care
about. A laptop on hotel wifi, a server in a rack, a device behind a carrier NAT — all of
them have an address, and the address does not change when the network does.

## Why this is a bigger deal than it sounds

Once every machine has a stable identity independent of its location, a whole category of
configuration disappears.

You stop writing firewall rules against office IP ranges that change when the ISP feels
like it. You stop maintaining a list of which subnet is which site. Automation stops
needing different connection paths depending on where it runs. Services that should only
be reachable internally can bind to the overlay address and simply be invisible from the
internet — not firewalled off, *not present*.

And crucially, NAT traversal is handled for you. Getting a direct tunnel between two hosts
that are both behind NAT is genuinely difficult, and the machinery to do it — STUN, hole
punching, and a relay for the cases where it fails — is the actual value the control plane
provides. When it works, traffic is direct. When it cannot, it falls back to a relay, and
you get connectivity instead of a support ticket.

## Now the trap

Every mesh gives you an internal naming scheme, so you can reach peers by name rather than
by overlay address. The mesh resolver answers those names with the peer's overlay address,
and only for machines on the mesh. That is the design and it is good.

Here is what nobody puts in the quick-start guide.

Those names are usually subdomains of a domain you own publicly. And if that public zone
has a catch-all wildcard — which an enormous number of zones do — then **public DNS will
answer for your internal names too.** A DNS wildcard matches at any depth, not just one
label. So a name intended to exist only inside the mesh gets a public answer, pointing at
whatever the wildcard points at, which is typically your main public host.

While the mesh resolver is working, nobody notices. The mesh answers first, you get the
right address, everything is fine. The problem appears the moment the mesh resolver *is
not* answering: the client restarted, the tunnel dropped, someone disabled the resolver,
the daemon is mid-reconnect. Then the query falls through to public DNS, and public DNS
answers — confidently, successfully, with the address of a completely different machine.

No error. Nothing fails. The name resolves. It just points somewhere else now.

Sit with what that means for automation. Configuration management that connects to hosts
by internal name, with credentials, running privileged operations. If a name silently
resolves to a different machine, the automation connects to that machine and does what it
was told to do. It may hand over a password on the way.

## Failing closed

The fix is small and you should do it before you need it: publish an explicit record for
the internal namespace in your public zone that points somewhere guaranteed to be
unroutable.

A more specific wildcard beats the general catch-all for that subtree, so a single record
covering the internal namespace takes it out of the catch-all's reach without touching the
records everything else depends on. Point it at documentation-reserved address space —
addresses set aside by RFC precisely so they can never route anywhere.

The effect is that a fall-through now *times out* instead of reaching a live machine. That
is a much better failure: slow and obviously broken, rather than fast and quietly wrong.
Fail closed, not sideways.

Then add the second layer, because DNS alone is not enough. Host key checking is what
turns "this is a different machine" from an invisible event into a loud one. If your
automation is configured to accept any host key without complaint — and a surprising
amount is, because it makes bootstrapping new machines convenient — then it will connect
to the wrong host without a word.

The configuration you want accepts a key it has never seen (so provisioning a fresh
machine still works unattended) but refuses loudly when a key *changes*. Those are
different situations and they deserve different responses. A tool that treats them the
same is one DNS blip away from an incident.

## Other things worth knowing

**The control plane is infrastructure.** It handles enrolment, key exchange and policy. If
it is down, existing tunnels generally keep working, but nothing new can join and policy
changes do not propagate. Treat it accordingly — it is not a nice-to-have service, it is a
dependency of your ability to reach everything else.

**Access policy is not the same as network reachability.** The mesh gives every peer an
address, and by default that can mean far more connectivity than you intended. A laptop
should probably not be able to reach every storage node. Policy is where you say so, and
it deserves the same review any firewall rule would get.

**Overlay addressing overlaps with things.** Meshes commonly use carrier-grade NAT space,
which is not routable on the public internet but is very much in use inside some ISP
networks. Know which range you are on before you debug a connectivity problem that only
affects one person's home connection.

**Understand relayed versus direct.** When hole punching fails, traffic goes through a
relay, and your throughput and latency change character. It will still work, which is why
you might not notice for months — until someone copies a large file and asks why it is
slow.

## Worth it?

Yes, and not marginally. Being able to address every machine you own by a stable name,
encrypted end to end, regardless of where it physically sits, removes an entire class of
network configuration from your life.

Just publish the blackhole record first. The trap costs one line of DNS to close and it
is not the sort of thing you want to discover by finding out which machine your automation
has been talking to.
