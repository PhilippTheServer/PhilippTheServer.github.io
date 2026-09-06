---
layout: post
title: "A Confidential OIDC Client That Exists Only to Introspect Tokens"
subtitle: "The client that issued a token is the wrong client to ask if it is still valid"
date: 2026-02-13 09:00:00 +0200
tags: [identity, security]
description: >-
  An API that validates access tokens by calling the OAuth2 introspection endpoint has
  to authenticate to that endpoint itself, and a public client such as a single-page
  application has no secret to do it with. This article sets up a second, confidential
  client whose only job is introspection, and gives the full provider configuration and
  the calls that use it.
---

## The problem

A single-page application authenticates against an OIDC provider using the Authorization
Code flow with PKCE. It is registered as a *public* client, because it runs entirely in
the browser and cannot keep a secret — anything shipped in JavaScript is visible to
whoever opens the network tab. That is correct and by design.

The backend API that receives the resulting access token has to decide whether it is
still valid. If the token is opaque, or you do not want to trust a locally verified
signature over revocation, the correct mechanism is RFC 7662 token introspection: POST
the token to the provider's `/introspect` endpoint and read back `active: true` or
`active: false`.

Try it against a public client and it fails immediately:

```bash
curl -s -X POST http://localhost:8080/realms/demo/protocol/openid-connect/token/introspect \
  -d "client_id=app-spa" \
  -d "token=$SPA_ACCESS_TOKEN"
```

```json
{"error":"invalid_client","error_description":"Parameter client_secret is missing"}
```

The provider is not wrong to refuse this. RFC 7662 requires the introspection endpoint
to authenticate the caller, precisely so that anyone cannot scan arbitrary tokens for
validity. `app-spa` has no secret because it must not have one.

The failure mode I have seen twice now is someone "fixing" this by ticking *client
authentication* on in the SPA's own client configuration and baking the resulting secret
into the frontend build. It works — the curl call above succeeds — and it is a genuine
vulnerability shipped to production, because that secret is now readable by anyone who
loads the page. It does not show up in a functional test. It shows up in a security
review, if you are lucky enough to have one before someone else finds it.

The actual problem is a category error: the client that a user authenticated as, and the
service that checks whether a token is still good, are different actors in OAuth2's own
model. Conflating them is what forces a public client to acquire a secret it should
never have.

## Working through it

### Separating "who issued this" from "who is asking"

OAuth2 already distinguishes the client (which requests tokens on a user's behalf) from
the resource server (which accepts them). Introspection is a resource-server operation.
Nothing requires the resource server to authenticate *as* the client whose token it is
checking — it only has to authenticate as *something* the provider trusts to make
introspection calls. That something can be a client that never issues a token to a
single user in its life.

### A client scoped to nothing but introspection

Create a second client on the provider, confidential, with every other capability turned
off:

- `publicClient: false` — it has a secret, stored on the API side only.
- `standardFlowEnabled: false` — it never runs a login redirect.
- `directAccessGrantsEnabled: false` — it never exchanges a password for a token.
- `serviceAccountsEnabled: false` — it does not need its own service-account token either,
  since introspection only needs client authentication, not a token of its own.

Its entire capability is "prove you are this client" via HTTP Basic auth on the token
endpoint's introspection path. It cannot be used to log a user in, so leaking its secret
into a server-side config file (never into a frontend bundle) is a far smaller blast
radius than leaking a client secret that can mint user sessions.

### The API's introspection call, with the right credentials

```bash
curl -s -X POST http://localhost:8080/realms/demo/protocol/openid-connect/token/introspect \
  -u "introspection-client:$INTROSPECTION_CLIENT_SECRET" \
  -d "token=$SPA_ACCESS_TOKEN"
```

```json
{"active":true,"exp":1771000000,"client_id":"app-spa","sub":"a1b2c3d4","scope":"openid profile"}
```

Note the response: `client_id` in the body is `app-spa`, the token's original owner. The
credentials on the wire belong to `introspection-client`. Those are two different
identities, on purpose, and that is the whole fix.

### What this changes about a compromise

If the API's introspection secret leaks, an attacker can find out whether a given token
is still valid — mildly useful reconnaissance, nothing more. If `app-spa`'s configuration
leaks, which it does routinely because it ships to every browser, there is nothing to
steal: it never had a secret. The blast radius of each credential now matches the
capability it grants, which was not true when the SPA's client carried the secret.

## The solution

A complete, runnable setup: Keycloak in dev mode, a public SPA client, a confidential
introspection-only client, and the calls that exercise both.

```yaml
# docker-compose.yml
services:
  keycloak:
    image: quay.io/keycloak/keycloak:25.0.6
    command: start-dev
    environment:
      KEYCLOAK_ADMIN: admin
      KEYCLOAK_ADMIN_PASSWORD: admin
    ports:
      - "8080:8080"
```

```bash
#!/usr/bin/env bash
# setup.sh — requires: docker compose up -d, curl, jq
set -euo pipefail

BASE=http://localhost:8080
REALM=demo

admin_token() {
  curl -s -X POST "$BASE/realms/master/protocol/openid-connect/token" \
    -d "client_id=admin-cli" -d "username=admin" -d "password=admin" \
    -d "grant_type=password" | jq -r .access_token
}

TOKEN=$(admin_token)
auth=(-H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json")

# 1. Realm
curl -s -X POST "$BASE/admin/realms" "${auth[@]}" \
  -d "{\"realm\":\"$REALM\",\"enabled\":true}"

# 2. Public client — the one the SPA uses to log a user in
curl -s -X POST "$BASE/admin/realms/$REALM/clients" "${auth[@]}" -d '{
  "clientId": "app-spa",
  "publicClient": true,
  "standardFlowEnabled": true,
  "directAccessGrantsEnabled": true,
  "redirectUris": ["http://localhost:4200/*"]
}'

# 3. Confidential client — exists only to authenticate introspection calls
curl -s -X POST "$BASE/admin/realms/$REALM/clients" "${auth[@]}" -d '{
  "clientId": "introspection-client",
  "publicClient": false,
  "standardFlowEnabled": false,
  "directAccessGrantsEnabled": false,
  "serviceAccountsEnabled": false
}'

# 4. Test user, so we have something to obtain a real token for
curl -s -X POST "$BASE/admin/realms/$REALM/users" "${auth[@]}" -d '{
  "username": "demo-user", "enabled": true,
  "credentials": [{"type":"password","value":"demo-pass","temporary":false}]
}'

echo "Realm, both clients and the test user are set up in realm '$REALM'."
```

```bash
#!/usr/bin/env bash
# demo.sh — obtain a token as the SPA would, then introspect it as the API would
set -euo pipefail
BASE=http://localhost:8080
REALM=demo

CID=$(curl -s "$BASE/admin/realms/$REALM/clients?clientId=introspection-client" \
  -H "Authorization: Bearer $(curl -s -X POST "$BASE/realms/master/protocol/openid-connect/token" \
      -d client_id=admin-cli -d username=admin -d password=admin -d grant_type=password | jq -r .access_token)" \
  | jq -r '.[0].id')

# fetch the introspection client's own secret (an operator would read this once, store it, and stop)
ADMIN_TOKEN=$(curl -s -X POST "$BASE/realms/master/protocol/openid-connect/token" \
  -d client_id=admin-cli -d username=admin -d password=admin -d grant_type=password | jq -r .access_token)
SECRET=$(curl -s "$BASE/admin/realms/$REALM/clients/$CID/client-secret" \
  -H "Authorization: Bearer $ADMIN_TOKEN" | jq -r .value)

# the SPA's login, using direct access grants purely to script this without a browser
SPA_TOKEN=$(curl -s -X POST "$BASE/realms/$REALM/protocol/openid-connect/token" \
  -d client_id=app-spa -d username=demo-user -d password=demo-pass \
  -d grant_type=password | jq -r .access_token)

echo "--- Introspecting as the confidential client ---"
curl -s -X POST "$BASE/realms/$REALM/protocol/openid-connect/token/introspect" \
  -u "introspection-client:$SECRET" -d "token=$SPA_TOKEN" | jq .
```

Running `./setup.sh` then `./demo.sh` prints an introspection response with
`"active": true` and `"client_id": "app-spa"` — proof that the credentials on the wire
and the token's origin are two different clients, checked independently by the provider.

## Conclusion

**A resource server is not the client it validates tokens for.** OAuth2 already has the
vocabulary for this distinction; the fix is to stop collapsing it in configuration.

**Scope every client's capabilities to what it actually does.** An introspection-only
client that cannot run a login flow or issue tokens is not a smaller version of a real
client — it is the correct shape for what it is.

**A secret's blast radius should match what it is a secret for.** A leaked introspection
secret tells an attacker whether a token is valid. A leaked client secret that can also
run the password grant hands them a way to mint sessions. Those are not the same
incident, and they should not share a credential.
