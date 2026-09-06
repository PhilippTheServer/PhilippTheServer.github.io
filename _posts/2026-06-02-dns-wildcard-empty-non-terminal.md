---
layout: post
title: "DNS Wildcards and Empty Non-Terminals: The Answer That Is Not an Error"
subtitle: "How one leftover ACME TXT record turns a working wildcard into silent NXDOMAIN."
date: 2026-06-02 09:00:00 +0200
tags: [dns, tls, testing]
description: >-
  A stale ACME DNS-01 TXT record can turn part of a domain into an empty
  non-terminal, and RFC 1034's wildcard rule then refuses to cover it — not
  as a bug, but as specified behaviour. This walks through the
  closest-encloser algorithm behind that refusal, reproduces it with a BIND
  container and a small zone file, and gives a script and a record-lifecycle
  pattern that catch the problem before it reaches production.
---

## The problem

`*.example.com` is a wildcard, meant to cover every name not explicitly listed in the
zone. At some point you needed a certificate for exactly one host, `app.example.com`,
not the whole subdomain space, so a one-off DNS-01 validation created
`_acme-challenge.app.example.com` as a TXT record, the CA issued the certificate, and the
job moved on. Whatever was supposed to delete that TXT record never ran — the standalone
renewal was decommissioned before its cleanup fired, or the cleanup depended on state
that no longer existed. The record just stayed in the zone.

Nothing alerts you. The wildcard keeps answering for everything else. Then something
tries to reach `app.example.com` — a host that never had its own record and was always
meant to fall back to the wildcard — and gets nothing:

```bash
$ dig @127.0.0.1 -p 5353 app.example.com A +noall +comments
;; ->>HEADER<<- opcode: QUERY, status: NOERROR, id: 4181
;; flags: qr aa rd; QUERY: 1, ANSWER: 0, AUTHORITY: 1, ADDITIONAL: 1

$ dig @127.0.0.1 -p 5353 www.app.example.com A +noall +comments
;; ->>HEADER<<- opcode: QUERY, status: NXDOMAIN, id: 4182
;; flags: qr aa rd; QUERY: 1, ANSWER: 0, AUTHORITY: 1, ADDITIONAL: 1

$ dig @127.0.0.1 -p 5353 other.example.com A +noall +answer
other.example.com.	300	IN	A	192.0.2.10
```

`other.example.com`, never mentioned anywhere in the zone, gets the wildcard's answer
without complaint. `app.example.com`, which also has no record of its own, gets a
deliberate "this name exists but has nothing of that type" response (NOERROR, empty
answer), not the wildcard's data. Anything below it gets outright NXDOMAIN.

That is what makes the fault easy to miss. Nobody touched the wildcard, delegation,
DNSSEC or TTLs. The only change is one TXT record, three labels deep, that looks
unrelated to an A lookup for a different name — yet it affects it, because wildcard
matching is decided per node in the tree, not per record, precisely as specified in
RFC 1034 and clarified further in RFC 4592.

## Working through it

### Closest encloser first, wildcard second

RFC 1034 §4.3.3 and RFC 4592 §3.3 describe wildcard matching as two steps, not a
one-step substitution:

1. If the queried name (QNAME) matches an existing zone node — regardless of whether it
   carries an RRset of the requested type — the server answers from that node and never
   consults a wildcard. No RRset of that type means NOERROR with an empty answer
   (NODATA), not a synthesized wildcard record.
2. Otherwise, find the *closest encloser*: the longest ancestor of QNAME that exists as a
   node. A wildcard child there (`*.<closest-encloser>`) answers, owner name rewritten to
   QNAME. No wildcard means NXDOMAIN.

The load-bearing detail is step 1: existence of the *name* is checked before the
wildcard is even considered.

### Why existence, not type, is what blocks the wildcard

A tempting alternative: use the node's data for any type it has, and fall back to the
wildcard only for types it lacks. RFC 1034 does not work that way, and the coarser rule
is not an oversight. A name can carry a CNAME, sit at a delegation point, or simply be a
node an operator created with no A record intended. "No A record here, borrow one from
the wildcard" would make the answer depend on which other types happen to exist at that
name — hard to specify consistently and harder to implement identically everywhere. "The
node exists, so the wildcard does not apply" needs no per-type reasoning at all.

### Empty non-terminals are the same rule, not a special case

An empty non-terminal (ENT) is a name with no RRset of its own that exists in the tree
only because some name below it has data. A zone containing
`_acme-challenge.app.example.com` but nothing at `app.example.com` itself makes
`app.example.com` an ENT: it exists as a node purely so the tree has somewhere to hang
the child from, though it carries nothing itself.

RFC 4592 is explicit that an ENT counts as "existing" for closest-encloser purposes
exactly like a populated node — no separate rule needed. The stale TXT record does not
just fail to help `app.example.com`; it manufactures a node there that blocks the
wildcard for that name and everything beneath it.

Working the algorithm through predicts every output from the problem section:
`other.example.com` has no closer node than `example.com`, so it gets the wildcard.
`app.example.com` exists (as an ENT) with no A record, so it gets NOERROR/empty.
`www.app.example.com`'s closest encloser is `app.example.com`, which has no wildcard
child, so NXDOMAIN. `_acme-challenge.app.example.com` answers exactly as before — which
is why it looks fine on inspection.

## The solution

### Reproducing it locally

```yaml
# docker-compose.yml
services:
  ns1:
    image: internetsystemsconsortium/bind9:9.20
    container_name: wildcard-ent-ns1
    ports:
      - "127.0.0.1:5353:53/udp"
      - "127.0.0.1:5353:53/tcp"
    volumes:
      - ./bind:/etc/bind:ro
```

```ini
# bind/named.conf
options {
    directory "/var/cache/bind";
    listen-on { any; };
    listen-on-v6 { none; };
    recursion no;
    allow-query { any; };
    allow-transfer { none; };
};

zone "example.com" {
    type master;
    file "/etc/bind/zones/db.example.com";
};
```

```ini
# bind/zones/db.example.com  (broken: leftover ACME record present)
$TTL 300
@       IN  SOA     ns1.example.com. hostmaster.example.com. (
                        2026060200  ; serial
                        3600        ; refresh
                        900         ; retry
                        604800      ; expire
                        300 )       ; negative cache TTL

        IN  NS      ns1.example.com.
ns1     IN  A       192.0.2.53

*       IN  A       192.0.2.10   ; catch-all for anything not listed here

; leftover DNS-01 record from a past single-host cert, never cleaned up
_acme-challenge.app IN TXT "9jK3f0qX2p7z1m5v8nQe6t4bY7wS0hL2cRdZaU1oXk8"
```

```bash
docker compose up -d
dig @127.0.0.1 -p 5353 other.example.com A +noall +answer
dig @127.0.0.1 -p 5353 app.example.com A +noall +comments
dig @127.0.0.1 -p 5353 www.app.example.com A +noall +comments
dig @127.0.0.1 -p 5353 _acme-challenge.app.example.com TXT +noall +answer
```

This reproduces the outputs worked out above. The fix removes the stale record; nothing
else changes:

```ini
# bind/zones/db.example.com  (fixed — stale record removed, serial bumped)
$TTL 300
@       IN  SOA     ns1.example.com. hostmaster.example.com. (
                        2026060201 3600 900 604800 300 )

        IN  NS      ns1.example.com.
ns1     IN  A       192.0.2.53
*       IN  A       192.0.2.10
```

```bash
docker exec wildcard-ent-ns1 rndc reload example.com
dig @127.0.0.1 -p 5353 app.example.com A +noall +answer
# app.example.com.        300     IN      A       192.0.2.10
dig @127.0.0.1 -p 5353 www.app.example.com A +noall +answer
# www.app.example.com.    300     IN      A       192.0.2.10
```

With the ENT gone, `app.example.com` no longer exists as a node, closest encloser reverts
to `example.com`, and the wildcard covers both names again.

### Catching it before it ships

The failure is entirely static: findable by reading the zone file, no running server
needed. Any record whose owner name is a strict descendant of a name that also carries a
wildcard is a candidate for shadowing it.

```python
#!/usr/bin/env python3
# check-wildcard-shadow.py — pip install dnspython==2.8.0
# Any existing zone node blocks a wildcard above it (RFC 4592 closest encloser).
import sys
import dns.name
import dns.zone


def main(zone_path: str, origin: str) -> int:
    origin_name = dns.name.from_text(origin)
    zone = dns.zone.from_file(zone_path, origin=origin_name, relativize=False)
    names = set(zone.nodes.keys())
    wildcard_parents = {dns.name.Name(n.labels[1:]) for n in names if n.labels[0] == b"*"}

    problems = set()
    for parent in wildcard_parents:
        wildcard = dns.name.Name((b"*",) + parent.labels)
        for name in names - {parent, wildcard}:
            if name.is_subdomain(parent):
                problems.add((name, parent))

    if not problems:
        print(f"OK: no records shadow the wildcard(s) under {origin}")
        return 0
    print(f"Wildcard coverage under {origin} is shadowed by:")
    for name, parent in sorted(problems):
        print(f"  {name}  blocks *.{parent} for this name and below")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
```

```bash
$ python3 check-wildcard-shadow.py bind/zones/db.example.com example.com
Wildcard coverage under example.com is shadowed by:
  _acme-challenge.app.example.com.  blocks *.example.com. for this name and below
```

Against the fixed zone it prints `OK`. Run it as a pre-deploy check on every zone that
mixes a wildcard with automation-managed records — a non-zero exit is exactly the signal
a CI pipeline needs before a zone file reaches the nameserver.

### Structuring the ACME lifecycle so this cannot accumulate

The zone-level fix is one deleted line; the lasting fix is in the DNS-01 hook. Verify
creation and deletion the same way, not just the API's response code — success does not
mean the record is visible yet, or that a delete actually removed it:

```bash
#!/usr/bin/env bash
set -euo pipefail
record="_acme-challenge.${1}"
value="${2}"

case "${MODE:-}" in
  add)
    dns_api_upsert_txt "${record}" "${value}"
    until dig +short TXT "${record}" | grep -qF "${value}"; do sleep 5; done
    ;;
  cleanup)
    dns_api_delete_txt "${record}" "${value}"
    until ! dig +short TXT "${record}" | grep -qF "${value}"; do sleep 5; done
    ;;
esac
```

Run cleanup on every exit path, including a failed validation, with `trap` rather than
trusting the ACME client's own error handling — a half-finished issuance is exactly the
case most likely to skip cleanup.

Never provision a DNS-01 record for a name only ever meant to be covered by a wildcard.
If `app.example.com` should resolve through `*.example.com`, issue the certificate for
the wildcard, not for `app.example.com` alone — a single-host certificate is what creates
the ENT risk. Where one genuinely is required, delegate the churny namespace out of the
production zone entirely:

```ini
_acme-challenge.app.example.com. IN CNAME _acme-challenge.app.example.com.acme.example.internal.
```

`acme.example.internal` is a zone dedicated to DNS-01 automation with no wildcard. A
record left behind there is inert — nothing to shadow — so production wildcard behaviour
stops depending on what a renewal script forgot to delete.

## Conclusion

**Wildcard matching is a question about the name tree, not about record types.**
Anything that makes a name exist — an A record, a TXT record, or an implicit empty
non-terminal created as a side effect of some other name — removes it from wildcard
coverage, regardless of what type was queried.

**NODATA or NXDOMAIN from an empty non-terminal is not a bug report.** The server
implements the specification correctly; the zone's content has drifted from what was
intended. The fix belongs in the data lifecycle, not in doubting the resolver.

**Anything that creates a name in a zone should be just as rigorous about deleting it**,
with a verified read-back on both ends. A create step that only checks an API response,
with no matching verified delete, is the same mistake as changing a config without ever
proving the new state took hold.

**Isolate automation-owned namespace from anything that depends on absence.** A wildcard
depends on the *absence* of a name; anything that creates names automatically —
certificate validation, service discovery, dynamic DNS — is in tension with that, and is
safest kept in a zone the wildcard never has to reason about.
