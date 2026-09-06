---
layout: post
title: "Group-Based Default-Deny Instead of Hand-Maintained Peer Lists"
subtitle: "Replacing per-peer NetBird rules with groups, default-deny, and a check that proves it"
date: 2026-05-26 09:00:00 +0200
tags: [networking, identity]
description: >-
  Per-peer VPN rules accumulate one exception at a time until nobody can say
  who is allowed to reach a given service. This article builds a NetBird
  policy set around groups instead of peer addresses, replaces the default
  allow-all policy with default-deny, and shows a script that checks the
  resulting access matrix against what the policy set claims to allow.
---

## The problem

Ask a straightforward question of most mesh VPNs after a year of organic growth: which
peers can reach the database tier. Answering it means opening every peer's configuration,
checking whether its allow-list contains the database's address, and then checking whether
that peer is still the thing you think it is. There is no single place to read the answer.

The rot looks like this, on a peer that was never a database client:

```text
# WireGuard peer config, added during an incident eight months ago.
# TODO: remove once the migration is done.
[Peer]
PublicKey = <redacted>
AllowedIPs = 10.20.0.0/24, 10.20.4.17/32
```

`10.20.4.17` is the database host. The `TODO` never got actioned, because removing a rule
requires proving nothing else depends on it, and nobody wants to be the person who breaks
a production path to save a line in a config file. Adding a rule costs one line and no
review. Removing one costs an investigation. Given that asymmetry, allow-lists only grow.

Two things make this specific to VPNs and worse than it looks in a firewall audit:

- **Addresses are not identity.** A peer that gets re-provisioned, a laptop that re-enrols,
  a VM that gets replaced by infrastructure-as-code — all of these can end up with a new
  address. A rule written against an address survives the peer it was written for and,
  eventually, starts matching whatever is issued that address next.
- **The failure is silent.** A stale allow-list entry does not error. It sits there working
  correctly for the peer that no longer needs it, and starts working incorrectly the day
  the address it names is reused. Nothing signals that moment.

The question "who can reach the database tier" should be answerable by reading a policy,
not by reconstructing an access-control list from a directory of peer configs that nobody
maintains as a set.

## Working through it

### The address was never the right thing to write into a rule

NetBird already separates a peer's identity (a stable object in its inventory) from its
address (assigned from a CGNAT range, `100.64.0.0/10`, when the peer connects). That
separation is wasted the moment a policy is written against the address instead of the
identity. NetBird's access-control model gives you the identity-shaped primitive to write
rules against instead: the group.

### Groups are computed membership, not a list you type

A NetBird group is a label that can be attached to a peer or to a user, and a peer can
belong to any number of groups at once. The important property is *how* peers end up in a
group — none of the three ways involves a human enumerating peers by address:

- **Setup keys.** A setup key carries an `auto_groups` field. Every peer that enrols with
  that key is placed in those groups automatically, at enrolment. This is how servers and
  other headless infrastructure get grouped: the group is a property of which key
  provisioned the machine, decided once, in code.
- **SSO / identity-provider propagation.** When a user authenticates via SSO — Keycloak,
  in a stack that already runs Keycloak for everything else — their device inherits the
  groups mapped to their identity. Revoke the person's group membership at the identity
  provider and their devices lose the corresponding NetBird group without anyone touching
  a NetBird policy.
- **Manual assignment**, for the rare peer that genuinely needs a one-off exception. Treat
  this the way you'd treat a manually-added firewall rule with no ticket behind it: a thing
  to notice and be suspicious of, not the normal path.

A policy that references `role:db-client` never has to be edited when a client is added,
retired, or re-provisioned. The group's membership changes; the policy does not.

### Turning the default policy off, in the right order

A fresh NetBird account ships with a policy literally named `Default` that allows every
peer to reach every other peer on every protocol. It exists so a new account is usable
immediately, not because it is a reasonable end state. Deleting it without a replacement
in place means every peer immediately loses reachability to every other peer, including
the connection you are managing NetBird over — the same class of self-inflicted outage as
restarting `sshd` before opening the new firewall port.

The safe order is: write and enable the group-scoped policies you actually want, confirm
with a real peer that they grant what you expect, and only then delete `Default`. Confirm
before you cut, never after.

### A naming scheme that makes a policy readable as a sentence

Flat, one-off group names (`db-servers`, `backend-team`, `prod2`) don't compose and don't
sort. A `prefix:value` scheme does both:

- `tier:database` — what a peer *is*.
- `role:db-client` — what a peer is *permitted to be a source of*.
- `env:prod` — which environment a peer belongs to.

Read next to a policy named "db-client to database", `role:db-client → tier:database` on
`5432/tcp` answers the opening question directly, without anyone reconstructing anything.

One subtlety worth knowing before you rely on it: a rule's `sources` (and `destinations`)
field takes a *list* of group IDs, and a peer matches if it is in *any* of them — it is a
union, not an intersection. Listing both `role:db-client` and `env:prod` as sources does
not mean "db-clients that are also in prod"; it means "db-clients, plus every other prod
peer regardless of role." If you need the intersection of two dimensions, the group that
represents it has to exist on its own — commonly by running separate NetBird accounts or
networks per environment, so a `staging` peer can never appear in the same policy universe
as a `prod` one, rather than trying to intersect environment and role inside one rule.

### Writing a rule that says only what it means

A policy rule has an action (only `accept` exists — NetBird is allow-list only, so there
is no ordering or priority to reason about, and nothing not explicitly allowed is
reachable), a protocol, a port or port range, a list of source groups, a list of
destination groups, and a `bidirectional` flag. Leave `bidirectional` false unless the
destination genuinely needs to *initiate* back to the source on the same protocol and
port — a client that only ever calls out to a database does not need it, and setting it
anyway quietly doubles the rule's meaning.

### Testing that the policy set only allows what it claims

A group-based policy set can still drift: a peer lands in `role:db-client` by mistake, a
rule gets created bidirectional when it shouldn't be, a manual per-peer group survives as
a backdoor. The fix is the same one you'd apply to any other piece of infrastructure —
declare the intended state as data, then check the live state against it.

Concretely: write the access matrix you intend (`source group → destination group,
protocol, ports`) as a small YAML file, fetch the actual groups and policies from the
NetBird API, expand every enabled `accept` rule into concrete `(source, destination,
protocol, port)` pairs, and diff the two sets. Anything live and not declared is undeclared
access; anything declared and not live is a policy that stopped doing its job.

## The solution

Three files. The first provisions groups, setup keys and the policy via the NetBird REST
API (works against NetBird Cloud or a self-hosted management server — set `NETBIRD_API`
accordingly and export a personal access token as `NETBIRD_TOKEN`). The second declares
the intended access matrix. The third checks the live policy set against it.

```bash
#!/usr/bin/env bash
# bootstrap-netbird-policy.sh
# Requires: curl, jq. NETBIRD_TOKEN must be a NetBird personal access token.
set -euo pipefail

NETBIRD_API="${NETBIRD_API:-https://api.netbird.io}"
NETBIRD_TOKEN="${NETBIRD_TOKEN:?Set NETBIRD_TOKEN to a NetBird personal access token}"

api() {
  local method="$1" path="$2" body="${3:-}"
  curl -sS -X "$method" "${NETBIRD_API}${path}" \
    -H "Authorization: Token ${NETBIRD_TOKEN}" \
    -H "Content-Type: application/json" \
    ${body:+-d "$body"}
}

create_group() {
  local name="$1" existing
  existing=$(api GET "/api/groups" | jq -r --arg n "$name" '.[] | select(.name==$n) | .id')
  if [ -n "$existing" ]; then echo "$existing"; return; fi
  api POST "/api/groups" "$(jq -n --arg n "$name" '{name: $n, peers: []}')" | jq -r '.id'
}

echo "Creating groups..." >&2
GROUP_DB=$(create_group "tier:database")
GROUP_DBCLIENT=$(create_group "role:db-client")
GROUP_PROD=$(create_group "env:prod")
echo "tier:database  = $GROUP_DB"       >&2
echo "role:db-client = $GROUP_DBCLIENT" >&2
echo "env:prod       = $GROUP_PROD"     >&2

echo "Creating a reusable setup key that enrols database peers into tier:database and env:prod..." >&2
api POST "/api/setup-keys" "$(jq -n \
  --arg name "db-server-enrollment" --arg g1 "$GROUP_DB" --arg g2 "$GROUP_PROD" \
  '{name: $name, type: "reusable", expires_in: 2592000, usage_limit: 0,
    auto_groups: [$g1, $g2], ephemeral: false}')" \
  | jq -r '"  setup key: " + .key'

echo "Creating a reusable setup key that enrols client peers into role:db-client and env:prod..." >&2
api POST "/api/setup-keys" "$(jq -n \
  --arg name "db-client-enrollment" --arg g1 "$GROUP_DBCLIENT" --arg g2 "$GROUP_PROD" \
  '{name: $name, type: "reusable", expires_in: 2592000, usage_limit: 0,
    auto_groups: [$g1, $g2], ephemeral: false}')" \
  | jq -r '"  setup key: " + .key'

echo "Creating the policy: role:db-client -> tier:database on 5432/tcp..." >&2
api POST "/api/policies" "$(jq -n \
  --arg src "$GROUP_DBCLIENT" --arg dst "$GROUP_DB" \
  '{name: "db-client to database",
    description: "Only role:db-client peers may reach tier:database on 5432/tcp",
    enabled: true,
    rules: [{
      name: "postgres",
      enabled: true,
      action: "accept",
      protocol: "tcp",
      ports: ["5432"],
      bidirectional: false,
      sources: [$src],
      destinations: [$dst]
    }]
  }')" | jq -r '"  policy id: " + .id'

cat <<'EOF' >&2

Next: enrol a real peer with each setup key above and confirm reachability matches
expectations (see verify-netbird-policy.py). Only after that, disable or delete the
account's "Default" policy — deleting it first blocks all peer traffic, including your
own connection to NetBird.
EOF
```

```yaml
# expected-access.yaml
# The access matrix this policy set is supposed to enforce.
# Anything reachable in the live account that is not listed here has drifted.
rules:
  - source: "role:db-client"
    destination: "tier:database"
    protocol: "tcp"
    ports: ["5432"]
```

```python
#!/usr/bin/env python3
# verify-netbird-policy.py
# pip install requests pyyaml
"""Diff NetBird's live, group-scoped access rules against a declared expectation."""
import os
import sys

import requests
import yaml

API = os.environ.get("NETBIRD_API", "https://api.netbird.io")
TOKEN = os.environ["NETBIRD_TOKEN"]
HEADERS = {"Authorization": f"Token {TOKEN}"}


def get(path):
    resp = requests.get(f"{API}{path}", headers=HEADERS, timeout=15)
    resp.raise_for_status()
    return resp.json()


def load_expected(path):
    with open(path, encoding="utf-8") as fh:
        doc = yaml.safe_load(fh)
    expected = set()
    for rule in doc["rules"]:
        for port in rule["ports"]:
            expected.add((rule["source"], rule["destination"], rule["protocol"], str(port)))
    return expected


def live_access_pairs():
    groups_by_id = {g["id"]: g["name"] for g in get("/api/groups")}
    pairs = set()
    unresolved = []
    for policy in get("/api/policies"):
        if not policy.get("enabled"):
            continue
        for rule in policy.get("rules", []):
            if not rule.get("enabled") or rule.get("action") != "accept":
                continue
            sources = rule.get("sources") or []
            destinations = rule.get("destinations") or []
            ports = rule.get("ports") or ["*"]
            for src_id in sources:
                for dst_id in destinations:
                    src = groups_by_id.get(src_id)
                    dst = groups_by_id.get(dst_id)
                    if src is None or dst is None:
                        unresolved.append((policy["name"], src_id, dst_id))
                        continue
                    for port in ports:
                        pairs.add((src, dst, rule["protocol"], str(port)))
                        if rule.get("bidirectional"):
                            pairs.add((dst, src, rule["protocol"], str(port)))
    return pairs, unresolved


def main():
    expected = load_expected("expected-access.yaml")
    actual, unresolved = live_access_pairs()

    extra = actual - expected
    missing = expected - actual

    if unresolved:
        print("WARNING: rules reference a group this token cannot resolve "
              "(built-in 'All' group, or a group outside this account's visibility):")
        for policy_name, src_id, dst_id in unresolved:
            print(f"  policy {policy_name!r}: {src_id} -> {dst_id}")

    if not extra and not missing and not unresolved:
        print(f"OK: live policy set matches expected-access.yaml exactly "
              f"({len(expected)} allowed path(s)).")
        return 0

    if extra:
        print("UNDECLARED access (live but not in expected-access.yaml):")
        for src, dst, proto, port in sorted(extra):
            print(f"  {src} -> {dst}  {proto}/{port}")
    if missing:
        print("MISSING access (declared but not enforced):")
        for src, dst, proto, port in sorted(missing):
            print(f"  {src} -> {dst}  {proto}/{port}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
```

Running `NETBIRD_TOKEN=... python3 verify-netbird-policy.py` against the account the
bootstrap script configured, with the `Default` policy already removed, prints:

```text
OK: live policy set matches expected-access.yaml exactly (1 allowed path(s)).
```

Add a peer to `role:db-client` by hand, or leave `Default` in place, and the same run
prints the extra path instead of exiting clean — which is the point: the check fails the
moment reality says more than the policy file claims.

## Conclusion

Three things here are not specific to NetBird:

**Write rules against identity, not against the address that identity currently holds.**
Kubernetes `NetworkPolicy` selectors, AWS security groups referencing other security
groups instead of CIDRs, and NetBird groups are the same idea applied to three different
layers — the address is going to change, so it cannot be the thing a rule remembers.

**Turning on default-deny is a cutover, not a toggle.** It has exactly one safe order:
prove the replacement rules grant what you need, then remove the blanket allow — never the
reverse, because the reverse means finding out what broke by losing the ability to reach
the thing you'd use to fix it.

**A policy set is only as trustworthy as the check that runs against it.** "I read the
rules and they look right" degrades every time someone else edits them. A declared
expectation and a script that diffs live state against it turns that judgement call into
something CI can enforce.

**A naming taxonomy is a policy artefact, not decoration.** `role:db-client` is
self-documenting only as long as everyone who creates a group uses the same scheme — that
discipline is worth enforcing in the same review that would catch a bad Terraform diff.
