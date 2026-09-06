---
layout: post
title: "Forward Auth: Putting Real Authentication in Front of Software That Has None"
subtitle: "Delegating the auth decision to an external service on every proxied request."
date: 2026-06-16 09:00:00 +0200
tags: [identity, networking, security]
description: >-
  Legacy admin panels, monitoring dashboards and device UIs frequently have no
  login of their own, yet they are exactly the systems you least want exposed.
  Forward auth lets a reverse proxy ask an external service, on every request,
  whether the caller may proceed, without changing a line of the backend. Here
  is the subrequest protocol, the header-spoofing mistake that undermines it,
  and a complete Traefik plus Keycloak stack that enforces it.
---

## The problem

The services you most want protected are often the ones that cannot speak modern auth at
all. An old admin UI shipped by a vendor who stopped patching it three years ago. A
monitoring dashboard that predates the company's identity provider. A device management
interface whose entire security model is "don't expose port 8080".

None of these will ever gain OIDC support. You cannot edit their source, and even if you
could, bolting a login screen onto a vendor appliance is not a reasonable use of anyone's
week. What you can do is put something in front of them that refuses to forward a request
until it has been authenticated — the application itself never finds out this happened.

A naive attempt looks like this:

```yaml
# Broken. Do not copy this.
http:
  routers:
    legacy:
      rule: "Host(`legacy.example.com`)"
      service: legacy-service
      # No middleware. The proxy is a pass-through, not a gate.
```

Traefik, nginx or Caddy will happily route every request straight through, and the
backend has no way to refuse one — it was never written to. The result is not a bug
report, because nothing crashes. It is a machine with no login screen, reachable by
anyone who can route a packet to it, and it stays that way until someone thinks to check.

Two things make this easy to miss. First, it works — the dashboard loads, the demo goes
fine, nobody types a URL that fails. Second, the fix that people reach for is usually
"put it behind the VPN" or "restrict it to the office subnet", which narrows the blast
radius without adding a single fact about who is making the request. Forward auth adds
that fact, in front of software that was never built to ask for it.

## Working through it

### The subrequest protocol

The pattern is the same across proxies even though the names differ: nginx calls it
`auth_request`, Traefik calls it a `ForwardAuth` middleware, Caddy calls it
`forward_auth`. On every incoming request, before the proxy forwards it to the real
backend, it fires a second request — a subrequest — at an auth service, carrying the
original request's method, path, headers and cookies (never the body).

The auth service inspects that subrequest and returns a bare status code:

- **2xx** means allow. The proxy continues to the real backend. Any headers the auth
  service put on its response can be copied onto the forwarded request — this is how the
  backend learns who the caller is, via something like `X-Auth-Request-User`, without
  ever handling a login itself.
- **401 or 403** means deny. The proxy returns that status (or redirects to a login page)
  and the real backend never sees the request at all.

The backend's own code does not change. It still has no concept of a session or a login
form. It is simply never reached unless the auth service has already said yes.

### The header-spoofing pitfall

The subrequest carries the original request's headers, and that is exactly where this
goes wrong. If a client can set `X-Auth-Request-User: admin` on its own request, and the
proxy does not strip that header before firing the subrequest, then the header the
backend is supposed to trust as proxy-authoritative arrives already carrying whatever the
client wanted it to say.

This is worse than it sounds because the failure mode is quiet. Consider an auth service
written during a migration from an older header-based trust model, with a fallback left
in for convenience:

```python
# auth-service/main.py — broken excerpt. Do not copy this.
@app.get("/verify")
def verify(request: Request):
    user = request.headers.get("x-auth-request-user")  # "just in case an upstream already set it"
    if not user:
        user = verify_session_cookie(request.cookies.get("session"))
    if not user:
        raise HTTPException(status_code=401)
    return Response(status_code=200, headers={"X-Auth-Request-User": user})
```

Reasonable-looking code, and it passes every test that logs in properly. But the proxy
forwards *all* of the client's original headers into the subrequest by default. If the
Traefik chain does not strip `X-Auth-Request-User` before the `ForwardAuth` middleware
runs, an attacker who can reach the proxy directly needs no cookie at all:

```bash
curl -H 'X-Auth-Request-User: admin' http://legacy.example.com/
# 200 OK — authenticated as admin, without ever authenticating
```

The fix has two independent parts, and both matter because either one failing alone
still leaves you exposed:

1. The auth service must never treat an inbound header as pre-validated identity. It
   decides identity itself, from the cookie or token, full stop.
2. The proxy must strip any header the auth service is trusted to set, from the
   *client's* original request, before the subrequest is made — so that even a future
   change to the auth service's code does not silently reopen the hole.

In Traefik this is a `headers` middleware placed before `forwardAuth` in the chain,
setting the header to an empty string, which removes it:

```yaml
http:
  middlewares:
    strip-spoofable-headers:
      headers:
        customRequestHeaders:
          X-Auth-Request-User: ""
```

nginx's equivalent is `proxy_set_header X-Auth-Request-User "";` in the block that
issues the `auth_request` subrequest — issued before the subrequest, not only before the
final proxy_pass, since it is the subrequest's headers that reach the auth service.

### Wiring in Keycloak

A hand-rolled auth service is fine for understanding the mechanism, but a real deployment
should not reimplement session and token handling. [oauth2-proxy](https://oauth2-proxy.github.io/oauth2-proxy/)
is the usual choice: it speaks OIDC to Keycloak, manages the login redirect and session
cookie, and exposes an `/oauth2/auth` endpoint built exactly for this purpose — a
subrequest to it returns 202 (or 200, depending on version) when the session cookie is
valid, 401 otherwise, with the verified identity on response headers such as
`X-Auth-Request-User` and `X-Auth-Request-Email`.

Pointing Traefik at it instead of a custom service is a one-line change to the
`forwardAuth` address:

```yaml
http:
  middlewares:
    forward-auth:
      forwardAuth:
        address: "http://oauth2-proxy:4180/oauth2/auth"
        authResponseHeaders:
          - X-Auth-Request-User
          - X-Auth-Request-Email
```

with oauth2-proxy itself configured for the Keycloak realm:

```
OAUTH2_PROXY_PROVIDER=keycloak-oidc
OAUTH2_PROXY_OIDC_ISSUER_URL=https://auth.example.com/realms/example
OAUTH2_PROXY_CLIENT_ID=forward-auth
OAUTH2_PROXY_CLIENT_SECRET=<from Keycloak client credentials>
OAUTH2_PROXY_COOKIE_SECRET=<32-byte random value, base64>
OAUTH2_PROXY_EMAIL_DOMAINS=*
OAUTH2_PROXY_UPSTREAMS=static://202
```

On the Keycloak side this is an ordinary confidential client with a redirect URI of
`https://legacy.example.com/oauth2/callback` and nothing backend-specific — Keycloak has
no idea the application behind it cannot speak OIDC, because it never talks to the
application at all. The header-stripping rule above still applies unchanged: strip
`X-Auth-Request-User` and `X-Auth-Request-Email` from the client's request before the
subrequest reaches oauth2-proxy, for the same reason.

## The solution

The stack below is runnable end to end with `docker compose up`. It uses a small FastAPI
service instead of oauth2-proxy so the whole flow — including the header-spoofing
check — is provable with plain `curl` and no browser. The Keycloak wiring above is a
drop-in replacement for the `auth-service` container once you have a realm to point it
at.

```yaml
# docker-compose.yml
services:
  traefik:
    image: traefik:v3.1.4
    command:
      - --providers.file.directory=/etc/traefik/dynamic
      - --providers.file.watch=true
      - --entrypoints.web.address=:80
      - --log.level=INFO
    ports:
      - "80:80"
    volumes:
      - ./traefik/dynamic.yml:/etc/traefik/dynamic/dynamic.yml:ro
    networks: [edge]

  auth-service:
    build: ./auth-service
    environment:
      SESSION_SECRET: "demo-secret-change-me"
      DEMO_USERNAME: "alice"
      DEMO_PASSWORD: "correct-horse-battery-staple"
    networks: [edge]

  legacy-backend:
    image: nginx:1.27.2-alpine
    volumes:
      - ./legacy-backend/index.html:/usr/share/nginx/html/index.html:ro
    networks: [edge]

networks:
  edge: {}
```

{% raw %}
```yaml
# traefik/dynamic.yml
http:
  middlewares:
    strip-spoofable-headers:
      headers:
        customRequestHeaders:
          X-Auth-Request-User: ""

    forward-auth:
      forwardAuth:
        address: "http://auth-service:8000/verify"
        authResponseHeaders:
          - X-Auth-Request-User

  routers:
    auth:
      rule: "Host(`auth.example.com`)"
      service: auth-service
      entryPoints: [web]

    legacy:
      rule: "Host(`legacy.example.com`)"
      service: legacy-service
      entryPoints: [web]
      middlewares:
        - strip-spoofable-headers
        - forward-auth

  services:
    auth-service:
      loadBalancer:
        servers:
          - url: "http://auth-service:8000"

    legacy-service:
      loadBalancer:
        servers:
          - url: "http://legacy-backend:80"
```
{% endraw %}

```dockerfile
# auth-service/Dockerfile
FROM python:3.12.7-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY main.py .
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
```

```
# auth-service/requirements.txt
fastapi==0.115.0
uvicorn==0.30.6
itsdangerous==2.2.0
```

```python
# auth-service/main.py
import os
from fastapi import FastAPI, Request, Response, HTTPException
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

app = FastAPI()
serializer = URLSafeTimedSerializer(os.environ["SESSION_SECRET"])
USERNAME = os.environ["DEMO_USERNAME"]
PASSWORD = os.environ["DEMO_PASSWORD"]


def read_session(cookie: str | None) -> str | None:
    if not cookie:
        return None
    try:
        return serializer.loads(cookie, max_age=3600)
    except (BadSignature, SignatureExpired):
        return None


@app.post("/login")
async def login(request: Request):
    form = await request.form()
    if form.get("username") != USERNAME or form.get("password") != PASSWORD:
        raise HTTPException(status_code=401, detail="invalid credentials")
    token = serializer.dumps(USERNAME)
    response = Response(status_code=204)
    response.set_cookie(
        "session", token, domain="example.com", httponly=True, samesite="lax"
    )
    return response


@app.get("/verify")
def verify(request: Request):
    # Identity comes only from the signed cookie. An inbound
    # X-Auth-Request-User header, spoofed or not, is never consulted here —
    # the stripping middleware upstream is defence in depth, not the only line.
    user = read_session(request.cookies.get("session"))
    if not user:
        raise HTTPException(status_code=401, detail="not authenticated")
    return Response(status_code=200, headers={"X-Auth-Request-User": user})
```

```html
<!-- legacy-backend/index.html -->
<!doctype html>
<html><body><h1>Legacy admin panel — no login of its own</h1></body></html>
```

Bring it up and exercise it:

```bash
docker compose up -d --build

# No cookie at all: denied before the legacy backend is ever reached.
curl --resolve legacy.example.com:80:127.0.0.1 -i http://legacy.example.com/
# HTTP/1.1 401 Unauthorized

# A spoofed identity header, still no valid cookie: denied the same way,
# because it was stripped before the /verify subrequest even saw it.
curl --resolve legacy.example.com:80:127.0.0.1 \
     -H 'X-Auth-Request-User: admin' -i http://legacy.example.com/
# HTTP/1.1 401 Unauthorized

# Log in against the auth service directly and keep the cookie.
curl --resolve auth.example.com:80:127.0.0.1 -c cookies.txt \
     -d 'username=alice&password=correct-horse-battery-staple' \
     -i http://auth.example.com/login
# HTTP/1.1 204 No Content

# With the cookie, Traefik's subrequest succeeds and the legacy backend
# is finally reached.
curl --resolve legacy.example.com:80:127.0.0.1 -b cookies.txt -i http://legacy.example.com/
# HTTP/1.1 200 OK
# <h1>Legacy admin panel — no login of its own</h1>
```

The legacy backend's own configuration never mentions authentication. Everything that
decides whether a request reaches it lives in `dynamic.yml` and `auth-service`, both of
which can be replaced — swap in oauth2-proxy and Keycloak as shown above — without
touching the backend at all.

## Conclusion

Forward auth is attractive precisely because it needs nothing from the thing it
protects, and that is also its limit: it decides whether a request is *let through*, not
what the application does with it once inside. An admin UI with a stored-XSS bug is
still an admin UI with a stored-XSS bug once someone is authenticated — forward auth
narrows who reaches that bug, it does not fix it.

Treat any header the auth layer sets as a security boundary that must be enforced on
every path into the service, not just the intended one. If the legacy backend is ever
reachable by a route that skips the middleware chain — a second router, a debug port
left open, a load balancer rule added in a hurry — the whole scheme is void, because
"trusted" only means "this specific proxy stripped and verified it here".

The pattern generalises past legacy UIs. Any service that speaks HTTP and does not need
to know about your identity provider — an internal metrics endpoint, a build artifact
store, a bare Prometheus without its own auth — is a candidate for exactly this shape:
proxy, subrequest, strip-then-verify, and a backend that stays exactly as unaware of
authentication as it was before you put it behind one.
