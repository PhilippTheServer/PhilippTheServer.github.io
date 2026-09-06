---
layout: post
title: "Enforcing Client Identity Alongside Realm Roles"
subtitle: "Why an OIDC realm role must be checked against the client that requested the token."
date: 2026-06-12 09:00:00 +0200
tags: [identity, security, fastapi]
description: >-
  A Keycloak realm role lives on the user, not on the client, so a token minted
  for a low-trust public application can carry the same role claim as one
  minted for a trusted backend. This shows how to close that gap in a FastAPI
  dependency by checking the azp claim against an allow-list alongside the
  usual signature, issuer and role checks, with a self-contained pytest suite.
---

## The problem

A realm in Keycloak is shared by every client registered in it: a public single-page
app, an admin console, a batch service, whatever else has been added over the years.
Realm roles — `admin`, `billing-operator`, `support` — are assigned to the *user*, once,
for the whole realm. They are not scoped to a client.

That is by design and it is usually fine. The trouble starts when a backend decides
whether to trust a request by looking only at the role claim:

```python
roles = claims.get("realm_access", {}).get("roles", [])
if "admin" not in roles:
    raise HTTPException(403)
```

Decode a token issued to a public SPA for a user who happens to hold `admin` in the
realm, and the payload looks like this:

```json
{
  "iss": "https://auth.example.internal/realms/<your-realm>",
  "aud": "account",
  "azp": "public-spa",
  "sub": "3f1c2b7a-...",
  "realm_access": { "roles": ["admin", "offline_access"] }
}
```

The role is there. Nothing in `realm_access.roles` says which client this token was
issued to, or whether that client was ever meant to talk to the admin API. If
`public-spa` is a client the same realm also issues tokens to — for a marketing site,
a status page, anything less trusted than the admin console — then any code path that
can obtain a token for that user through `public-spa` produces a token that clears the
role-only check just as well as a token from the client the API was actually built for.

This is easy to miss because it never shows up in normal testing. The admin console
calls the API with a token from the admin console's own client, the check passes, and
everything looks correct. The gap only matters once a second client exists in the same
realm for the same user population — which is precisely the situation a growing
Keycloak deployment ends up in, one client added per application, all authenticating
against the same set of realm roles.

## Working through it

### Where `azp` and `aud` actually come from

Keycloak populates two claims that describe the client side of the transaction. `azp`
(authorized party) is set to the `client_id` that requested the token — this happens
automatically, for every client, with no configuration required. `aud` (audience) is a
different story: Keycloak's default access token has `aud` set to `account` (or, with
multiple audiences, a list that may or may not include the API you care about) unless
someone adds an audience mapper to a client scope that maps a specific value into it. In
other words, `azp` is a client identity you get for free; `aud` is a client identity you
have to remember to configure, per client, and it is exactly the kind of setup step that
gets forgotten on the third client you add and never revisited on the first two.

That asymmetry is the reason `azp` is the claim worth building the check around, and
`aud` a useful addition rather than a substitute. Requiring the right audience checks
that a specific mapper was configured correctly; requiring the right `azp` checks the
one fact Keycloak asserts regardless of anyone's mapper configuration.

### Why the role check alone is not authorization

A role answers "is this user allowed to do admin things, in principle". It says nothing
about which application is asking on the user's behalf, and OAuth2/OIDC never promised
that it would — a realm role and a client are orthogonal concepts in the model.
Authorization for a specific API needs both: the user must hold the role, *and* the
token must have been issued to a client the API's owner has decided to trust with that
role. Dropping either half leaves a check that is necessary but not sufficient.

The asymmetry between clients matters here. A public SPA typically runs with no client
secret and the authorization code flow plus PKCE — a reasonable choice for a browser
application, but it also means the SPA's tokens are reachable from browser-side code,
extensions, and anything else running in that origin. A backend-to-backend client using
the client-credentials or confidential authorization-code flow is a different trust
tier. Keycloak treats both as ordinary clients in the same realm; nothing stops a user
who holds `admin` from also being a normal user of the public SPA. The backend is the
only place left to encode "role X is only meaningful when it arrived via client Y or Z".

### Building the check as an allow-list, not a blocklist

An allow-list of trusted `azp` values inverts the failure mode: a newly registered
client is untrusted by default, and someone has to deliberately add it before its
tokens are accepted for a privileged route. A blocklist has the opposite failure mode —
a new client is trusted until someone remembers to exclude it — and that is the wrong
default for anything gating an admin capability.

The check itself is small: decode and verify the token as normal (signature, issuer,
audience, expiry), then compare `claims["azp"]` against a fixed set, and only after that
look at the role. Order matters for the error message but not for security — both
conditions are required, so either check failing means the same "no".

### Where this lives

All of this belongs in one FastAPI dependency so every route that needs it declares
`Depends(require_role("admin"))` and gets signature verification, audience/issuer
checks, the `azp` allow-list and the role check in one place. Duplicating any part of
this per-route is how one route quietly ends up checking role only.

## The solution

`requirements.txt`:

```ini
fastapi==0.115.6
uvicorn==0.32.1
PyJWT==2.10.1
cryptography==43.0.3
pytest==8.3.4
httpx==0.28.1
```

`main.py`:

```python
"""FastAPI backend that authorizes on Keycloak realm role AND authorized party (azp).

A realm role alone is not enough: it is attached to the user, not to the client,
so any client in the realm that can obtain a token for a privileged user produces
a token carrying that same role. This dependency also checks which client the
token was issued for (azp) against an explicit allow-list before trusting the role.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Callable

import jwt
from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWKClient

REALM_ISSUER = os.environ.get(
    "OIDC_ISSUER", "https://auth.example.internal/realms/<your-realm>"
)
JWKS_URL = os.environ.get(
    "OIDC_JWKS_URL", f"{REALM_ISSUER}/protocol/openid-connect/certs"
)
# Requires an audience mapper on the client scope(s) used by trusted clients;
# Keycloak's default access token audience ("account") will not match this.
API_AUDIENCE = os.environ.get("OIDC_AUDIENCE", "admin-console-api")

# Clients whose tokens are trusted to carry privileged realm roles for this API.
# A client not on this list is rejected even if the user holds the required role.
TRUSTED_AZP = {"admin-console", "internal-batch-service"}
REQUIRED_ROLE = "admin"

bearer_scheme = HTTPBearer(auto_error=True)


@dataclass
class TokenVerifier:
    issuer: str
    audience: str
    trusted_azp: set[str]
    get_signing_key: Callable[[str], object]
    algorithms: tuple[str, ...] = ("RS256",)

    def verify(self, token: str) -> dict:
        try:
            signing_key = self.get_signing_key(token)
        except Exception as exc:  # JWKS lookup or key-id mismatch
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED, "Could not resolve signing key"
            ) from exc

        key = signing_key.key if hasattr(signing_key, "key") else signing_key

        try:
            claims = jwt.decode(
                token,
                key=key,
                algorithms=list(self.algorithms),
                issuer=self.issuer,
                audience=self.audience,
                options={"require": ["exp", "iat", "iss", "azp"]},
            )
        except jwt.PyJWTError as exc:
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED, f"Invalid token: {exc}"
            ) from exc

        azp = claims.get("azp")
        if azp not in self.trusted_azp:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"Client '{azp}' is not authorized to call this API",
            )

        return claims


def _default_signing_key(token: str):
    # PyJWKClient caches the JWKS response internally; no per-request fetch.
    jwks_client = PyJWKClient(JWKS_URL)
    return jwks_client.get_signing_key_from_jwt(token)


verifier = TokenVerifier(
    issuer=REALM_ISSUER,
    audience=API_AUDIENCE,
    trusted_azp=TRUSTED_AZP,
    get_signing_key=_default_signing_key,
)


def get_verifier() -> TokenVerifier:
    return verifier


def require_role(role: str):
    def dependency(
        credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
        token_verifier: TokenVerifier = Depends(get_verifier),
    ) -> dict:
        claims = token_verifier.verify(credentials.credentials)
        roles = claims.get("realm_access", {}).get("roles", [])
        if role not in roles:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, f"Missing required role '{role}'"
            )
        return claims

    return dependency


app = FastAPI(title="admin-console-api")


@app.get("/admin/ping")
def admin_ping(claims: dict = Depends(require_role(REQUIRED_ROLE))) -> dict:
    return {"status": "ok", "subject": claims["sub"], "azp": claims["azp"]}
```

`test_main.py`:

```python
"""Unit tests for the azp + realm-role authorization dependency.

No live Keycloak instance is required: a throwaway RSA key pair signs test
tokens locally, and the app's JWKS lookup is replaced with a dependency
override that hands back the matching public key directly.
"""
from __future__ import annotations

import time

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from main import API_AUDIENCE, REALM_ISSUER, TokenVerifier, app, get_verifier

_private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_public_pem = _private_key.public_key().public_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PublicFormat.SubjectPublicKeyInfo,
).decode()
_private_pem = _private_key.private_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PrivateFormat.PKCS8,
    encryption_algorithm=serialization.NoEncryption(),
).decode()


def make_token(azp: str, roles: list[str]) -> str:
    now = int(time.time())
    claims = {
        "iss": REALM_ISSUER,
        "aud": API_AUDIENCE,
        "azp": azp,
        "sub": "3f1c2b7a-0000-0000-0000-000000000000",
        "iat": now,
        "exp": now + 300,
        "realm_access": {"roles": roles},
    }
    return jwt.encode(claims, _private_pem, algorithm="RS256")


@pytest.fixture()
def client():
    test_verifier = TokenVerifier(
        issuer=REALM_ISSUER,
        audience=API_AUDIENCE,
        trusted_azp={"admin-console", "internal-batch-service"},
        get_signing_key=lambda token: _public_pem,
    )
    app.dependency_overrides[get_verifier] = lambda: test_verifier
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_right_role_wrong_azp_is_rejected(client: TestClient) -> None:
    token = make_token(azp="public-spa", roles=["admin"])
    response = client.get("/admin/ping", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 403
    assert "not authorized" in response.json()["detail"]


def test_right_role_and_right_azp_is_accepted(client: TestClient) -> None:
    token = make_token(azp="admin-console", roles=["admin"])
    response = client.get("/admin/ping", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    body = response.json()
    assert body["azp"] == "admin-console"


def test_right_azp_wrong_role_is_rejected(client: TestClient) -> None:
    token = make_token(azp="admin-console", roles=["support"])
    response = client.get("/admin/ping", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 403
    assert "Missing required role" in response.json()["detail"]
```

Run it with:

```bash
pip install -r requirements.txt
pytest -v test_main.py
```

The two required cases are `test_right_role_wrong_azp_is_rejected` and
`test_right_role_and_right_azp_is_accepted`; the third is there because a check that
only ever tests the `azp` allow-list would happily pass with the role check silently
missing.

## Conclusion

**A claim being present is not the same as a claim meaning what a single route needs it
to mean.** `realm_access.roles` is a correct, faithfully-issued claim about the user; the
bug is entirely in assuming it also answers a question about the client, which it was
never scoped to answer.

**Prefer the claim the identity provider sets automatically over the one that needs
manual configuration, and use both when you can.** `azp` costs nothing to get right
because Keycloak sets it regardless of anyone's client-scope configuration; `aud` is
worth checking too, but only as a second signal, never as the sole gate on a capability
whose correctness depends on someone having added a mapper.

**An allow-list changes what "forgetting to configure something" costs you.** With a
default-deny list of trusted `azp` values, a new client that nobody has vetted yet is
locked out of privileged routes by default. With a blocklist, or with no client check at
all, the same oversight silently grants access.

**Test the negative case as deliberately as the positive one.** It is easy to write a
test that proves a valid admin token is accepted and stop there; the case worth having
in the suite is the token that is valid, current, and role-correct in every respect
except the one claim this whole exercise exists to check.
