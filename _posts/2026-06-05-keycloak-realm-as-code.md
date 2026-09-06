---
layout: post
title: "Realm-as-Code: Reconciling Clients and Roles But Never Users or Signing Keys"
subtitle: "Scoping declarative Keycloak reconciliation to structure, never to users or keys."
date: 2026-06-05 09:00:00 +0200
tags: [identity, gitops, security]
description: >-
  Reconciling a Keycloak realm from a file is safe for clients, roles and role
  mappings, but the same "make live state match this JSON" instinct applied to
  users or signing keys deletes real accounts and invalidates every
  outstanding token. This article draws that boundary precisely, and works
  through a Python script against the Admin REST API that reconciles clients
  and realm roles idempotently against a local Keycloak container, while
  proving a second run changes nothing and a user created out-of-band
  survives every run.
---

## The problem

A Keycloak realm accumulates configuration the way any system does when people click
around an admin console: a client added for a new service, a role renamed, a redirect URI
patched in during an incident. None of it is written down anywhere except the running
server, and a staging realm and a production realm quietly diverge.

The instinct to fix this is config-as-code: export the realm once, commit the JSON, and
reapply it whenever the running realm should match the file. Keycloak even ships a tool
for exactly this — `kc.sh export` and `kc.sh import` — and it feels safe right up until
you read what the import actually does.

```bash
# Broken. Do not run this against anything that matters.
/opt/keycloak/bin/kc.sh import --dir /opt/keycloak/data/import
```

By default this runs with `--override=true`. If the target realm already exists, Keycloak
deletes it and recreates it from the file. Every user who registered since the export was
taken is gone, every active session is gone, and unless the exact key material was
captured and replayed byte-for-byte, the recreated realm mints a fresh set of signing keys
— so every access and refresh token issued by the old realm stops validating the instant
the import finishes. Nobody typed "delete all users" or "rotate the signing key"; they
typed "make the realm match the file", which is a different instruction only if a boundary
was drawn around what "the file" is allowed to contain.

The same failure mode shows up in hand-rolled scripts against the Admin REST API that diff
live state against a JSON file and delete anything not present in it: they are only as
safe as the completeness of the file. A user created through self-registration, a client
added by another team, a realm key rotated during an incident — all of these are "not in
the file" by definition, because they happened after the file was last generated. A
reconciliation loop that treats "not in the file" as "delete it" will, sooner or later,
delete something that was never supposed to be derived from the file at all.

This is easy to get wrong because the failure is silent at apply time. The import
succeeds and the admin console renders fine; the damage shows up later, as a wave of
"I've been logged out" and "my account doesn't exist" reports that look unrelated to the
deployment that caused them.

## Working through it

### Draw the boundary between structure and state

Not everything in a realm is the same kind of thing. Clients, realm roles, client roles,
role mappings and client scopes are structural: they describe what the system looks like,
hold no data specific to an individual end user, and carry no cryptographic material. Two
realms with identical client and role configuration are interchangeable from the
application's point of view — exactly the property that makes something safe to declare
in a file and reconcile against. If it drifts, converging it back loses nothing.

Users, credentials, and signing keys are different in kind, not degree. A user is not
derived from configuration — it is data, created by a real person registering or an admin
onboarding someone, with no canonical source outside the running database. A signing key
is cryptographic material every previously issued token is bound to. Reconciling either
the way you reconcile a client definition means treating live, valuable state as
disposable build output. It isn't.

The rule that follows is simple to state and easy to violate by accident: **a
reconciliation pass may create or update clients, roles, role mappings and client scopes.
It must never delete anything, and it must never touch users, credentials, or keys at
all.**

### Never do a destructive "make it match" pass

The dangerous idiom is always some version of: fetch everything of a type, fetch what the
file says should exist, delete the set difference. It looks correct — it's the natural way
to write a diff. The fix is to remove deletion from the algorithm entirely. A
reconciliation script should only ever look up an object by a stable key — a client's
`clientId`, a role's `name` — and create it if absent or update the fields it owns if
present. Retiring a client is a deliberate, reviewed, named action, never an automatic
consequence of removing a line from a file.

It also means the reconciler should never `PUT` the realm object itself — that resource
is where signing keys and other runtime settings live. A script that only ever touches
`/clients` and `/roles` cannot regenerate or overwrite a key by construction: there is no
code path that would.

### The Terraform equivalent: `lifecycle` and what to never declare

The same boundary applies with Terraform, using either the community `keycloak/keycloak`
provider or its predecessor `mrparkers/keycloak`. Terraform converges every declared
resource back to its configuration on every apply — exactly the problem for anything
stateful.

```hcl
terraform {
  required_providers {
    keycloak = {
      source  = "keycloak/keycloak"
      version = "5.0.0"
    }
  }
}
```

Clients and roles are safe to model as first-class resources — they hold no state
Terraform doesn't already know about; the full example is below. Signing keys have a
corresponding resource type (`keycloak_realm_keystore_rsa`), and it is tempting to bring
key rotation under Terraform for consistency. Don't: if a key rotates out-of-band (a
schedule, an incident response), Terraform sees drift and the next `apply` reverts it,
invalidating every token issued since on a schedule nobody chose. Leave key resources out
of the configuration entirely.

Users are the same story with a sharper edge: `terraform state rm`, a dropped resource
block, or an `apply` after someone else's manual change all end the same way for a
declared `keycloak_user` resource — deletion. The fix isn't a `lifecycle` block, it's
simpler: never declare a `keycloak_user` resource at all. Onboarding a person is not a
Terraform operation.

Client secrets need the same protection. If a secret is rotated by a process Terraform
doesn't know about — a break-glass rotation, a secrets-manager sync — the state file still
holds the old value, and the next `apply` proposes to reset it, quietly breaking every
application that already picked up the new one. `lifecycle { ignore_changes }` stops that
field from ever appearing in a plan:

```hcl
resource "keycloak_openid_client" "billing_service" {
  realm_id                     = keycloak_realm.example.id
  client_id                    = "billing-service"
  access_type                  = "CONFIDENTIAL"
  service_accounts_enabled     = true
  standard_flow_enabled        = false
  direct_access_grants_enabled = false

  lifecycle {
    ignore_changes = [client_secret]
  }
}
```

### Authenticate reconciliation as itself, not as an admin

Run the reconciliation job as a dedicated service-account client scoped to
`manage-clients` and `manage-realm`, via client-credentials grant — not the realm
administrator's password. This bounds a reconciler bug to the objects it touches, and the
audit log shows which automation made a change.

## The solution

This runs end to end against a disposable local Keycloak. Start it:

```bash
docker run -d --name keycloak-dev \
  -p 8080:8080 \
  -e KEYCLOAK_ADMIN=admin \
  -e KEYCLOAK_ADMIN_PASSWORD=admin \
  quay.io/keycloak/keycloak:25.0.6 \
  start-dev
```

`reconcile.py` declares the desired clients and roles inline for readability; in practice
this would load from a YAML file under version control.

```python
#!/usr/bin/env python3
"""
reconcile.py - declarative client + realm-role reconciliation for Keycloak.

Reconciles ONLY: the realm (created if entirely absent), clients (matched by
clientId), and realm roles (matched by name).

Never touches: users, credentials, realm signing keys, or anything that
exists on the server but is absent from this file.
"""
import requests

KEYCLOAK_URL = "http://localhost:8080"
REALM = "example"
ADMIN_USER = "admin"
ADMIN_PASSWORD = "admin"

DESIRED_CLIENTS = [
    {
        "clientId": "billing-service",
        "protocol": "openid-connect",
        "publicClient": False,
        "standardFlowEnabled": False,
        "serviceAccountsEnabled": True,
        "directAccessGrantsEnabled": False,
    },
]

DESIRED_REALM_ROLES = [
    {"name": "billing-read", "description": "Read-only access to billing data"},
    {"name": "billing-write", "description": "Create and modify billing records"},
]


def get_admin_token():
    resp = requests.post(
        f"{KEYCLOAK_URL}/realms/master/protocol/openid-connect/token",
        data={
            "grant_type": "password",
            "client_id": "admin-cli",
            "username": ADMIN_USER,
            "password": ADMIN_PASSWORD,
        },
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def ensure_realm_exists(session):
    resp = session.get(f"{KEYCLOAK_URL}/admin/realms/{REALM}")
    if resp.status_code == 404:
        create = session.post(
            f"{KEYCLOAK_URL}/admin/realms",
            json={"realm": REALM, "enabled": True},
        )
        create.raise_for_status()
        print(f"created realm {REALM}")
        return True
    resp.raise_for_status()
    return False  # existing realm: never PUT back, keys stay untouched


def reconcile_clients(session, base):
    changed = False
    existing = session.get(f"{base}/clients").json()
    by_client_id = {c["clientId"]: c for c in existing}

    for desired in DESIRED_CLIENTS:
        current = by_client_id.get(desired["clientId"])
        if current is None:
            resp = session.post(f"{base}/clients", json=desired)
            resp.raise_for_status()
            print(f"created client {desired['clientId']}")
            changed = True
            continue

        if any(current.get(k) != v for k, v in desired.items()):
            merged = {**current, **desired}
            resp = session.put(f"{base}/clients/{current['id']}", json=merged)
            resp.raise_for_status()
            print(f"updated client {desired['clientId']}")
            changed = True
    return changed


def reconcile_realm_roles(session, base):
    changed = False
    existing = session.get(f"{base}/roles").json()
    by_name = {r["name"]: r for r in existing}

    for desired in DESIRED_REALM_ROLES:
        current = by_name.get(desired["name"])
        if current is None:
            resp = session.post(f"{base}/roles", json=desired)
            resp.raise_for_status()
            print(f"created role {desired['name']}")
            changed = True
            continue

        if current.get("description") != desired.get("description"):
            resp = session.put(
                f"{base}/roles/{desired['name']}",
                json={**current, **desired},
            )
            resp.raise_for_status()
            print(f"updated role {desired['name']}")
            changed = True
    return changed


def main():
    token = get_admin_token()
    session = requests.Session()
    session.headers["Authorization"] = f"Bearer {token}"
    session.headers["Content-Type"] = "application/json"

    ensure_realm_exists(session)
    base = f"{KEYCLOAK_URL}/admin/realms/{REALM}"

    changed = False
    changed |= reconcile_clients(session, base)
    changed |= reconcile_realm_roles(session, base)

    if not changed:
        print("no changes")


if __name__ == "__main__":
    main()
```

Role mappings and client scopes extend the same shape: fetch by name, create or update,
never delete what the file didn't mention.

### Verifying idempotence

```bash
python3 -m venv .venv && . .venv/bin/activate && pip install requests

python3 reconcile.py
# created realm example
# created client billing-service
# created role billing-read
# created role billing-write

python3 reconcile.py
# no changes
```

The second run printing `no changes` is the test. If it prints an update every run, the
comparison is picking up fields Keycloak normalises or adds server-side, and the fix is to
narrow the comparison to the fields the file actually declares.

### Verifying users survive

```bash
TOKEN=$(curl -s -X POST http://localhost:8080/realms/master/protocol/openid-connect/token \
  -d grant_type=password -d client_id=admin-cli \
  -d username=admin -d password=admin | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')

# Create a user the way it would happen in reality: through the console or
# the API directly, never through reconcile.py.
curl -s -X POST http://localhost:8080/admin/realms/example/users \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"username": "jane.doe", "enabled": true}'

python3 reconcile.py
# no changes

curl -s http://localhost:8080/admin/realms/example/users \
  -H "Authorization: Bearer $TOKEN" | python3 -c 'import sys,json;print([u["username"] for u in json.load(sys.stdin)])'
# ['jane.doe']
```

`jane.doe` exists before and after, because nothing in `reconcile.py` ever queries,
compares, or deletes anything under `/users`. The same argument covers the realm's active
signing key: `curl .../example/keys` returns the identical key ID before and after,
because `ensure_realm_exists` never issues a `PUT` to an existing realm.

## Conclusion

The mistake in a naive realm-as-code setup is not automation, it is failing to separate
what a file may author from what it can only observe. Three points generalise past
Keycloak:

**Reconciliation needs an explicit allowlist of what it may create, update and delete —
not an implicit one derived from "everything the file mentions".** Once a script's scope
is defined by absence rather than presence, deletion becomes possible by omission, and
omissions are the normal state of a file last edited yesterday.

**Structural configuration and stateful data look identical in a JSON export, and only
the reconciler enforces the difference.** A client and a user are both objects with an ID
and some fields; nothing in the response format says one is safe to overwrite and the
other isn't. That judgement has to be made once, explicitly, when the tool is built — not
re-derived under pressure at 2 a.m.

**Idempotence and non-destructiveness are separate properties and need separate proof.**
A tool can converge to the same state on every run and still be dangerous, if what it
converges is the wrong scope. Test both: a second run that changes nothing, and a piece of
out-of-band state the tool was never supposed to touch, still there afterwards. The same
question — which parts of current state have no representation in desired state, by design
rather than oversight — applies just as much to Kubernetes custom resources, DNS zone
files, and firewall rulesets.
