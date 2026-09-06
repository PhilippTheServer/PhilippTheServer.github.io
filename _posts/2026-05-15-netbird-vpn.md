---
layout: post
title: "Overlay Mesh Networking with NetBird: Peer Addressing and the Public DNS Fallthrough"
subtitle: "Every machine gets a stable address, and your internal names get a way to resolve elsewhere."
date: 2026-05-15 09:00:00 +0200
tags: [networking, dns, security, linux]
description: >-
  An overlay mesh gives every machine a stable address and a direct encrypted
  path to every other one, replacing a hub-and-spoke VPN. It also gives
  internal hostnames a silent way to resolve to the wrong place the moment the
  mesh resolver is not in the loop. This walks through why, and includes a
  runnable DNS setup that reproduces the fallthrough and the one-line fix that
  closes it.
---

## The problem

A traditional VPN is a hub. Everything dials into one concentrator, all traffic goes
through it, and that box is simultaneously the security boundary, the bandwidth
bottleneck, and the single point of failure. An overlay mesh inverts that: every machine
gets a stable address on a private network, and any two of them establish a direct
encrypted tunnel — peer to peer, not through a hub. A control plane handles identity, key
distribution and NAT traversal, then gets out of the path.

Once every machine has an identity independent of its location, a mesh usually gives you
internal hostnames too, so peers are reached by name instead of by address. A resolver on
each machine answers those names — but only for machines on the mesh, and only while it is
actually running.

Here is what does not make it into the quick-start guide: those hostnames are usually
subdomains of a domain the organisation owns publicly. If that public zone has a catch-all
wildcard — and a striking number of zones do — then **public DNS will answer for the
internal names too**, and it will do so successfully. While the mesh resolver is working,
nobody notices; it answers first, and the query never reaches the public zone at all. The
problem appears the moment the mesh resolver *is not* answering — the client restarted, the
tunnel dropped, someone disabled the resolver — because the query falls through to public
DNS, and public DNS answers confidently with the address of a completely different
machine. No error. Nothing fails. The name resolves. It just points somewhere else.

Sit with what that means for automation that connects to hosts by internal name, with
credentials, to run privileged operations. If the name silently resolves to a different
machine, the automation connects to that machine and does exactly what it was told to do.

## Working through it

### Why this class of failure is easy to miss

A DNS wildcard matches at any depth below the name it is defined on, and it answers with
`NOERROR` and a real address — indistinguishable, to anything that only checks whether the
query succeeded, from the correct answer. There is no distinct error code for "this
resolved, but to the wrong thing on purpose." The failure is a *correct-looking* answer
that happens to be false, which is precisely the kind of failure that survives a design
review, because reviewing DNS config usually means checking that queries succeed.

### The fallthrough, concretely

Take a public zone with the ordinary shape: a general wildcard covering anything
undefined, pointing at the organisation's main public host.

```
; vulnerable zone - do not copy this
$TTL 300
@    IN SOA ns1.example.internal. admin.example.internal. (2024010101 3600 900 604800 300)
     IN NS  ns1.example.internal.
ns1  IN A   192.0.2.1
*    IN A   192.0.2.10      ; the public host - answers for anything undefined
```

Mesh peer names such as `db-1.mesh.example.internal` have no record here at all, because
the mesh resolver is supposed to be the only thing that ever answers for them. The moment
it is not consulted, the query reaches this zone, matches the general wildcard, and returns
`192.0.2.10` — the real public host — as if that were the correct answer for a database
peer. It is a successful resolution to the wrong machine.

### The fix: a more specific wildcard, not a bigger one

A more specific wildcard beats the general catch-all for the subtree it covers. Adding one
scoped to the mesh namespace intercepts exactly the queries that matter, without touching
anything the rest of the zone depends on:

```
*.mesh IN A 192.0.2.0     ; TEST-NET-1: reserved, guaranteed non-routable
```

Point it at an address from a documentation-reserved range (RFC 5737), not at `0.0.0.0` or
a made-up private address — the point is that a connection attempt to it fails
predictably rather than landing on some other real host. A fallthrough now times out
instead of reaching a live machine: slow and obviously broken, rather than fast and
quietly wrong. Fail closed, not sideways.

### DNS alone is not enough

Add a second layer, because a blackhole record only helps if something is watching for the
address to change. Host key checking is what turns "this is a different machine" from an
invisible event into a loud one. Configuration that accepts any host key without complaint
— convenient for bootstrapping new machines — will connect to the wrong host without a
word. What you want accepts a key it has never seen before, so provisioning still works
unattended, but refuses loudly the moment a key *changes* for a name it already trusted.
Those are different situations and deserve different responses.

## The solution

A self-contained BIND9 zone reproduces the fallthrough locally, built from a Debian base so
there is no third-party image tag to trust:

```dockerfile
# Dockerfile
FROM debian:12-slim
RUN apt-get update \
    && apt-get install -y --no-install-recommends bind9 bind9utils \
    && rm -rf /var/lib/apt/lists/*
COPY named.conf.local /etc/bind/named.conf.local
COPY db.example.internal /etc/bind/db.example.internal
EXPOSE 53/udp 53/tcp
CMD ["named", "-g", "-u", "bind"]
```

```
# named.conf.local
zone "example.internal" {
    type master;
    file "/etc/bind/db.example.internal";
};
```

```
; db.example.internal - the fixed zone
$TTL 300
@       IN SOA  ns1.example.internal. admin.example.internal. (
                2024010102 ; serial
                3600       ; refresh
                900        ; retry
                604800     ; expire
                300 )      ; minimum
        IN NS   ns1.example.internal.
ns1     IN A    192.0.2.1

; general catch-all: anything undefined resolves to the public host
*       IN A    192.0.2.10

; more specific than the wildcard above - wins for anything under
; mesh.example.internal, including peer names the mesh resolver would
; normally answer for, and anything ever removed from the mesh
*.mesh  IN A    192.0.2.0
```

```bash
docker build -t dns-fallthrough-demo .
docker run -d --name dns-fallthrough-demo -p 5353:53/udp dns-fallthrough-demo

# a mesh peer name, with the mesh resolver out of the picture
dig @127.0.0.1 -p 5353 db-1.mesh.example.internal A +short
# 192.0.2.0        <- the blackhole address, correctly, not the public host

# anything outside the mesh subtree is unaffected
dig @127.0.0.1 -p 5353 www.example.internal A +short
# 192.0.2.10
```

Remove the `*.mesh` line and rebuild, and the first query returns `192.0.2.10` instead —
the exact fallthrough this record exists to prevent.

The second layer, in an SSH client config, so a changed host key stops a connection rather
than being silently accepted:

```
# ~/.ssh/config.d/mesh
Host *.mesh.example.internal
    StrictHostKeyChecking accept-new
    UserKnownHostsFile ~/.ssh/known_hosts_mesh
```

`accept-new` adds a host's key the first time it is seen, so unattended provisioning of a
fresh machine still works, and refuses the connection outright — rather than silently
overwriting the stored key — the moment an existing name presents a different key.

## Conclusion

**A successful DNS response is not the same claim as a correct one.** `NOERROR` with an
address only means the protocol worked; it says nothing about whether the address is the
one you meant. Any check that stops at the response code is checking the wrong thing.

**Specificity is the tool for narrowing a blast radius you cannot remove entirely.** The
general wildcard has to stay, because the rest of the zone depends on it; a more specific
record scoped to exactly the subtree at risk closes the gap without touching anything else.

**Two independent layers catch what one alone will not.** DNS failing closed stops the
connection from reaching a live host; host key checking stops it from trusting one it does
reach. Either alone leaves a gap the other was built to cover.

Worth it, and not marginally — a stable, encrypted address for every machine regardless of
where it physically sits removes an entire category of network configuration from daily
work. Publish the blackhole record before you need it. It costs one line of DNS and it is
not the kind of thing you want to discover by finding out which machine your automation has
been talking to.
