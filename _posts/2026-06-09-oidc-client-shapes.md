---
layout: post
title: "OIDC Client Shapes: A Token-Validating API Is Not a Login Client"
subtitle: "Why a token-checking API needs a different client shape than a login screen."
date: 2026-06-09 09:00:00 +0200
tags: [identity, security, api-design]
description: >-
  Configuring every OIDC client the same way conflates "users log in here"
  with "this service validates a token", and the wrong shape accepts flows
  nobody intended. This article covers the three client shapes and gives
  working Keycloak configuration plus a Python JWT validator that checks
  issuer, audience and signature.
---

## The problem

Most OIDC setups I have seen start from a template: create a client, turn on the flows
that "make login work", copy it for the next application. Applied to a backend API with no
browser and no users of its own, that template looks like this:

```json
{
  "clientId": "orders-api",
  "protocol": "openid-connect",
  "publicClient": false,
  "standardFlowEnabled": true,
  "directAccessGrantsEnabled": true,
  "redirectUris": ["https://orders-api.example.com/*"],
  "secret": "a-secret-nobody-rotates"
}
```

This client authenticates requests by checking a bearer token. It never sends a browser
anywhere or asks a user for a password. Yet the configuration above gives it a redirect
URI it will never use, and — more importantly — leaves `directAccessGrantsEnabled` on,
Keycloak's name for the OAuth2 Resource Owner Password Credentials grant.

That single flag means `orders-api` is not just a thing that validates tokens — it can
*mint* them: hand its client ID, secret, and any username and password to the token
endpoint, and it hands back a valid access token for that user. A component whose whole
job was "read the `Authorization` header and say yes or no" can now authenticate
arbitrary users on its own.

Nothing about this shows up in day-to-day testing — the API validates tokens correctly,
login works. The extra capability sits unused until the secret leaks — a log line, a
misconfigured dump, a baked-in environment file — at which point it becomes a second,
undocumented way to authenticate as anyone.

The other half of the mistake sits on the validating side. Checking a token's signature
and issuer is not the same as checking it was issued *for you*. An IdP with several
registered clients issues structurally identical, validly signed tokens for all of them; a
resource server that skips the audience check will happily accept one minted for a
different application.

Both share a root cause: treating "an OIDC client" as one shape with a few optional
toggles, rather than three distinct kinds of thing.

## Working through it

### Naming the three shapes

The three shapes an OIDC client can have follow directly from what the thing does:

1. **Interactive login client.** A human is in front of a browser. It runs the
   authorization code flow and needs `redirectUris`, plus PKCE. Confidential (server-side,
   with a secret) or public (single-page/native, secret-less, PKCE mandatory).

2. **Resource server / token validator.** An API receiving bearer tokens issued for *some
   other* client. It never starts a login flow or redirects a browser — its only IdP
   traffic is fetching signing keys.

3. **Service account / machine-to-machine client.** A backend job with no human involved,
   using the client credentials grant: ID and secret in, token out, scoped to the
   service's own identity rather than a user's.

The template at the top of this article configures shape 1 for something that should be
shape 2. The fix is a different configuration, not a smaller version of the same one,
because the shapes answer different questions: *who is the token for*, and *can this
component obtain one at all*.

### What disabling flows actually buys you

For the resource-server shape, the property that matters is not "does it have a redirect
URI" but "can it obtain a token by any means". A backend that only validates tokens has no
reason to call the token endpoint, and every grant type left enabled is one available to
whoever compromises the backend.

In Keycloak terms: `standardFlowEnabled`, `implicitFlowEnabled`,
`directAccessGrantsEnabled` and `serviceAccountsEnabled` all `false`, no `redirectUris`.
Keycloak ships a `bearerOnly` flag for this, but I set the individual flags too — a single
flag is one misreading away from being toggled back on; four independently-false flags
survive a casual edit better.

The blast radius this buys back: leaked credentials get an attacker nothing from the IdP,
only whatever a stolen *user* token already gave them.

### The audience is not optional

An IdP with issuer `https://idp.example.internal/realms/example` and two registered
clients — a web app and an orders API — will, by default, issue tokens whose `aud` claim
reflects only the client the token was requested *for*, not every client in the realm. Without an
explicit audience mapper, a token issued to the web app during login does not carry the
orders API as an audience at all.

The failure I have seen run the other way: a team adds the orders API as an audience via a
mapper (correct, and necessary), then finds some other client's tokens are also being
accepted, and "fixes" it by loosening the validator to accept any token from the trusted
issuer instead of tightening the mapper. That trades a five-minute diagnosis for a standing
hole: from then on, *any* client of that IdP can produce an accepted token.

`aud` can be a string or an array of strings (RFC 7519 §4.1.3); a correct validator checks
the expected audience is a member of that set, not that it equals the whole claim. Getting
this comparison subtly wrong — string equality against a sometimes-list claim — is its own
way of silently disabling the check.

The mapper that makes `orders-api` appear in `aud` lives on the *web app* client, or on
the realm as a shared mapper — not on the resource server itself:

```json
{
  "name": "orders-api-audience",
  "protocol": "openid-connect",
  "protocolMapper": "oidc-audience-mapper",
  "config": {
    "included.client.audience": "orders-api",
    "id.token.claim": "false",
    "access.token.claim": "true"
  }
}
```

Without it, a strict validator rejects every token, including the ones it should accept —
and the "fix" under deadline pressure is loosening the check, not adding this mapper.

## The solution

### The three client shapes in Keycloak

Interactive confidential client, for a web app that logs users in:

```json
{
  "clientId": "webapp",
  "protocol": "openid-connect",
  "publicClient": false,
  "standardFlowEnabled": true,
  "implicitFlowEnabled": false,
  "directAccessGrantsEnabled": false,
  "serviceAccountsEnabled": false,
  "redirectUris": ["https://app.example.com/callback"],
  "webOrigins": ["https://app.example.com"],
  "attributes": {
    "pkce.code.challenge.method": "S256"
  }
}
```

A single-page app uses the same shape with `publicClient: true` and no secret — PKCE
replaces the secret, not an optional extra alongside it.

Resource server, validating tokens issued for it only:

```json
{
  "clientId": "orders-api",
  "protocol": "openid-connect",
  "publicClient": false,
  "bearerOnly": true,
  "standardFlowEnabled": false,
  "implicitFlowEnabled": false,
  "directAccessGrantsEnabled": false,
  "serviceAccountsEnabled": false,
  "authorizationServicesEnabled": false,
  "redirectUris": []
}
```

Service account for a machine-to-machine job:

```json
{
  "clientId": "invoice-worker",
  "protocol": "openid-connect",
  "publicClient": false,
  "standardFlowEnabled": false,
  "directAccessGrantsEnabled": false,
  "serviceAccountsEnabled": true,
  "redirectUris": []
}
```

Creating all three with `kcadm.sh` (JSON files saved locally):

```bash
kcadm.sh config credentials \
  --server https://idp.example.internal --realm master --user admin

kcadm.sh create clients -r <your-realm> -f webapp-client.json
kcadm.sh create clients -r <your-realm> -f orders-api-client.json
kcadm.sh create clients -r <your-realm> -f invoice-worker-client.json

WEBAPP_ID=$(kcadm.sh get clients -r <your-realm> -q clientId=webapp \
  --fields id --format csv --noquotes)
kcadm.sh create clients/$WEBAPP_ID/protocol-mappers/models \
  -r <your-realm> -f orders-api-audience-mapper.json
```

### Validating a token correctly

The script below generates a throwaway RSA key pair to stand in for the IdP's signing key,
issues two test tokens, and validates them the way `orders-api` should — no network
access required.

```python
# validate_token.py
"""
Validate an OIDC access token: signature, issuer and audience.

Self-contained: it plays the identity provider by generating its own signing
key. In production, swap `generate_keypair`/the hardcoded public key for
`jwt.PyJWKClient(jwks_uri)`, keyed by the token's `kid` header.
"""
from __future__ import annotations

import time

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

ISSUER = "https://idp.example.internal/realms/example"
EXPECTED_AUDIENCE = "orders-api"


def generate_keypair() -> tuple[str, str]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    public_pem = key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    return private_pem, public_pem


def issue_token(private_pem: str, audience: str, subject: str = "user-123") -> str:
    now = int(time.time())
    claims = {
        "iss": ISSUER,
        "sub": subject,
        "aud": audience,
        "iat": now,
        "exp": now + 300,
        "azp": audience,
    }
    return jwt.encode(claims, private_pem, algorithm="RS256")


def validate_token(token: str, public_pem: str) -> dict:
    """Check signature, issuer and audience. Raises a jwt exception on failure."""
    return jwt.decode(
        token,
        public_pem,
        algorithms=["RS256"],
        issuer=ISSUER,
        audience=EXPECTED_AUDIENCE,
        options={"require": ["exp", "iss", "aud", "sub"]},
    )


def _demo() -> None:
    private_pem, public_pem = generate_keypair()

    good_token = issue_token(private_pem, audience=EXPECTED_AUDIENCE)
    claims = validate_token(good_token, public_pem)
    print("accepted token for subject:", claims["sub"])

    wrong_audience_token = issue_token(private_pem, audience="webapp")
    try:
        validate_token(wrong_audience_token, public_pem)
    except jwt.InvalidAudienceError:
        print("correctly rejected token issued for a different client")
    else:
        raise AssertionError("token with the wrong audience was accepted")


if __name__ == "__main__":
    _demo()
```

```python
# test_validate_token.py
import jwt
import pytest

from validate_token import (
    EXPECTED_AUDIENCE,
    generate_keypair,
    issue_token,
    validate_token,
)


def test_correct_audience_is_accepted():
    private_pem, public_pem = generate_keypair()
    token = issue_token(private_pem, audience=EXPECTED_AUDIENCE)
    claims = validate_token(token, public_pem)
    assert claims["aud"] == EXPECTED_AUDIENCE


def test_wrong_audience_is_rejected():
    private_pem, public_pem = generate_keypair()
    token = issue_token(private_pem, audience="webapp")
    with pytest.raises(jwt.InvalidAudienceError):
        validate_token(token, public_pem)
```

```
# requirements.txt
PyJWT==2.9.0
cryptography==43.0.1
pytest==8.3.2
```

Running it:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python validate_token.py
# accepted token for subject: user-123
# correctly rejected token issued for a different client
pytest -q  # 2 passed
```

`test_wrong_audience_is_rejected` is the one that matters here: the token is validly
signed, from the correct issuer, not expired — and still rejected, because it was never
issued for this API.

## Conclusion

An OIDC client is not a single thing with optional settings; it is one of a small number
of distinct roles, and the role determines which flows may be enabled at all.

A few points generalise past Keycloak:

**Every enabled grant is a capability, not a convenience.** Ask what a component's
credentials let it do if they leak, not just what it does normally. A resource server
whose only job is validation should be structurally unable to obtain a token.

**"Signed by someone I trust" is weaker than "signed for me".** The two collapse into the
same check only when an IdP has exactly one client, which is never true in practice.

**When a strict check breaks something, the fix is almost never to loosen the check.** A
rejected token over a missing audience is a configuration gap upstream, not evidence the
validator was too strict. Loosening it fixes the symptom for every client at once,
including ones you have not thought about yet.

**This is a general access-control pattern, not an OIDC quirk.** It shows up wherever one
system hands another a contextually scoped credential: a Vault token with a policy, a
Kubernetes service account tied to a namespace, an API key scoped to one tenant. The
question is always the same — can this credential do only what it was issued for, and can
the thing holding it get a broader one on its own.
