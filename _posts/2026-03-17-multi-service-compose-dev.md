---
layout: post
title: "A Multi-Service docker-compose That a New Contributor Can Actually Start"
subtitle: "Healthchecks, a seeded realm and one smoke test replace sleep-and-hope startup order."
date: 2026-03-17 09:00:00 +0200
tags: [docker, identity, testing]
description: >-
  A backend that needs an identity provider, a database, a cache and a
  time-series store fails unpredictably under a naive docker-compose file
  because none of it waits for the others to actually be ready. This shows
  the healthcheck, seeding and end-to-end test that make `docker compose up`
  a reliable one-command environment.
---

## The problem

A typical backend for a small SaaS product needs four things running before it will even
boot: an identity provider to validate the tokens it receives, a relational database for
its own state, a cache for sessions and rate limits, and a time-series store for metrics.
None of these is complicated on its own. Wiring them together for a new contributor's
laptop is where it goes wrong.

The obvious first attempt is a compose file that lists every container and lets
`docker compose up` start them in parallel:

```yaml
# Broken. Do not copy this.
services:
  postgres:
    image: postgres:16
  redis:
    image: redis:7
  keycloak:
    image: quay.io/keycloak/keycloak:24.0.4
    command: start-dev
  backend:
    build: ./backend
    depends_on:
      - postgres
      - redis
      - keycloak
```

`depends_on` without a condition only orders container *start*, not readiness. Postgres's
container is running long before Postgres is accepting connections — it is still running
`initdb`. Keycloak takes noticeably longer: it boots a JVM, migrates its own schema, and
imports any realm you configured. The backend container starts, tries to connect to all
three, and dies with a connection-refused error on whichever one was slowest that run —
not the same one every time, because startup order isn't deterministic under load.

A new contributor's response is predictable: run `docker compose up` again. Sometimes
that works, because the slow service is warm now. Sometimes it doesn't. The fix nobody
wants to write is a `sleep 30` in the backend's entrypoint, which is slow on a fast
machine and still wrong on a slow one.

There's a second, quieter failure in the same setup. Keycloak with `start-dev` and no
realm configuration comes up as a blank identity provider — no realm, no client, no test
user. The backend connects fine this time, but every login attempt fails because the
realm the app expects does not exist. That failure looks like an application bug, not an
environment problem, and it costs far more time to diagnose than a missing container
does.

## Working through it

### Healthchecks tell you readiness, not process existence

`docker compose ps` and a plain `depends_on` only know whether a process exists. Compose
has a real readiness primitive — `healthcheck` — and attaching one to Postgres and Redis
costs nothing, since the tools already ship in their official images:

```yaml
postgres:
  image: postgres:16
  healthcheck:
    test: ["CMD-SHELL", "pg_isready -U app"]
    interval: 2s
    timeout: 3s
    retries: 15
```

`pg_isready` exits `0` only once the server accepts connections. That is a materially
different question from "is the container running", and it's the one that matters.

### depends_on with a condition, not a list

Compose can block a service's start on another service's healthcheck passing:

```yaml
backend:
  depends_on:
    postgres:
      condition: service_healthy
    redis:
      condition: service_healthy
    keycloak:
      condition: service_healthy
```

This is the change that removes the non-determinism. The backend container isn't
created until the things it needs have proven, via their own healthcheck, that they are
ready — not merely started.

### Keycloak's healthcheck has to check the right thing

Keycloak's slim image ships neither `curl` nor `wget`, and its readiness probe needs to
hit its own management port, not just a process check. A TCP probe against that port is
the option available without adding tooling to the image, but it's weaker than an HTTP
check — a JVM can accept a socket before its web layer is serving — so give it a generous
`start_period` and enough retries rather than trusting the first success:

```yaml
keycloak:
  image: quay.io/keycloak/keycloak:24.0.4
  healthcheck:
    test: ["CMD-SHELL", "exec 3<>/dev/tcp/127.0.0.1/9000 && printf 'GET /health/ready HTTP/1.1\\r\\nHost: localhost\\r\\nConnection: close\\r\\n\\r\\n' >&3 && grep -q 'UP' <&3"]
    interval: 3s
    timeout: 3s
    retries: 30
    start_period: 20s
```

Port 9000 is Keycloak's management interface, which exposes `/health/ready` once
`KC_HEALTH_ENABLED` is set. Getting this right matters because a shallow check — "is
anything listening on 8080" — reports healthy before the realm import the next step
relies on has finished, and you're back to the second failure mode, just one layer down.

### Seed the realm at boot, not by hand

A realm, a client and a test user should live in the repository as a file, not as a
checklist someone follows once and never writes down:

```yaml
keycloak:
  volumes:
    - ./keycloak/realm-export.json:/opt/keycloak/data/import/realm-export.json:ro
  command: ["start-dev", "--import-realm"]
```

`--import-realm` imports every file under that directory on boot, and it's idempotent —
re-running with the same file doesn't duplicate the realm. This turns "ask a colleague
for a login" into "read the file in the repository".

### Prove it with one command, not a manual click-through

A healthcheck proves a dependency is reachable. It doesn't prove the backend can actually
complete the round trip: get a token, use it, hit the database. Write that as an actual
test that runs against the stack compose just built, as its own service, so it's part of
`docker compose up` rather than a separate ritual:

```yaml
smoke-test:
  build: ./backend
  command: ["python", "-m", "pytest", "tests/test_smoke.py", "-v"]
  depends_on:
    backend:
      condition: service_healthy
```

## The solution

Directory layout:

```
.
├── docker-compose.yml
├── keycloak/
│   └── realm-export.json
└── backend/
    ├── Dockerfile
    ├── requirements.txt
    ├── app.py
    └── tests/
        └── test_smoke.py
```

```yaml
# docker-compose.yml
services:
  postgres:
    image: postgres:16
    environment:
      POSTGRES_USER: app
      POSTGRES_PASSWORD: app
      POSTGRES_DB: app
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U app"]
      interval: 2s
      timeout: 3s
      retries: 15

  redis:
    image: redis:7
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 2s
      timeout: 3s
      retries: 15

  influxdb:
    image: influxdb:2.7
    environment:
      DOCKER_INFLUXDB_INIT_MODE: setup
      DOCKER_INFLUXDB_INIT_USERNAME: app
      DOCKER_INFLUXDB_INIT_PASSWORD: app12345
      DOCKER_INFLUXDB_INIT_ORG: example-org
      DOCKER_INFLUXDB_INIT_BUCKET: metrics
      DOCKER_INFLUXDB_INIT_ADMIN_TOKEN: dev-only-token
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8086/health"]
      interval: 2s
      timeout: 3s
      retries: 15

  keycloak:
    image: quay.io/keycloak/keycloak:24.0.4
    environment:
      KEYCLOAK_ADMIN: admin
      KEYCLOAK_ADMIN_PASSWORD: admin
      KC_HEALTH_ENABLED: "true"
    volumes:
      - ./keycloak/realm-export.json:/opt/keycloak/data/import/realm-export.json:ro
    command: ["start-dev", "--import-realm"]
    ports:
      - "8080:8080"
    healthcheck:
      test: ["CMD-SHELL", "exec 3<>/dev/tcp/127.0.0.1/9000 && printf 'GET /health/ready HTTP/1.1\\r\\nHost: localhost\\r\\nConnection: close\\r\\n\\r\\n' >&3 && grep -q 'UP' <&3"]
      interval: 3s
      timeout: 3s
      retries: 30
      start_period: 20s

  backend:
    build: ./backend
    environment:
      DATABASE_URL: postgresql://app:app@postgres:5432/app
      REDIS_URL: redis://redis:6379/0
      INFLUXDB_URL: http://influxdb:8086
      INFLUXDB_TOKEN: dev-only-token
      KEYCLOAK_URL: http://keycloak:8080
      KEYCLOAK_REALM: example
    ports:
      - "8000:8000"
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
      influxdb:
        condition: service_healthy
      keycloak:
        condition: service_healthy
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/healthz"]
      interval: 3s
      timeout: 3s
      retries: 10

  smoke-test:
    build: ./backend
    environment:
      KEYCLOAK_URL: http://keycloak:8080
      KEYCLOAK_REALM: example
      BACKEND_URL: http://backend:8000
    command: ["python", "-m", "pytest", "tests/test_smoke.py", "-v"]
    depends_on:
      backend:
        condition: service_healthy
```

```json
// keycloak/realm-export.json (trimmed to the essentials)
{
  "realm": "example",
  "enabled": true,
  "clients": [
    {
      "clientId": "example-app",
      "publicClient": true,
      "directAccessGrantsEnabled": true,
      "redirectUris": ["*"],
      "enabled": true
    }
  ],
  "users": [
    {
      "username": "dev",
      "enabled": true,
      "credentials": [
        { "type": "password", "value": "dev", "temporary": false }
      ]
    }
  ]
}
```

```dockerfile
# backend/Dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
```

```
# backend/requirements.txt
fastapi==0.115.0
uvicorn==0.30.6
psycopg[binary]==3.2.1
redis==5.0.8
influxdb-client==1.45.0
requests==2.32.3
pytest==8.3.2
```

```python
# backend/app.py
import os

import psycopg
import redis
from fastapi import FastAPI
from influxdb_client import InfluxDBClient

app = FastAPI()


@app.get("/healthz")
def healthz():
    with psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=2):
        pass
    redis.Redis.from_url(os.environ["REDIS_URL"]).ping()
    client = InfluxDBClient(
        url=os.environ["INFLUXDB_URL"], token=os.environ["INFLUXDB_TOKEN"], org="example-org"
    )
    client.ping()
    return {"status": "ok"}
```

```python
# backend/tests/test_smoke.py
import os

import requests


def test_backend_reports_healthy():
    resp = requests.get(f"{os.environ['BACKEND_URL']}/healthz", timeout=5)
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_keycloak_issues_a_token_for_the_seeded_user():
    url = f"{os.environ['KEYCLOAK_URL']}/realms/{os.environ['KEYCLOAK_REALM']}/protocol/openid-connect/token"
    resp = requests.post(
        url,
        data={
            "grant_type": "password",
            "client_id": "example-app",
            "username": "dev",
            "password": "dev",
        },
        timeout=5,
    )
    assert resp.status_code == 200
    assert "access_token" in resp.json()
```

Running it:

```bash
docker compose up --build --abort-on-container-exit --exit-code-from smoke-test
```

Correct output ends with:

```
smoke-test-1  | tests/test_smoke.py::test_backend_reports_healthy PASSED
smoke-test-1  | tests/test_smoke.py::test_keycloak_issues_a_token_for_the_seeded_user PASSED
smoke-test-1 exited with code 0
```

`--exit-code-from smoke-test` makes the whole invocation exit non-zero if the test
service fails, which is what makes this usable in CI as well as on a laptop.

## Conclusion

A `docker compose up` that "usually works" is worse than one that reliably fails fast,
because the first one costs every new contributor an hour of independently rediscovering
the same race.

Three things generalise past this specific stack:

**A healthcheck is a readiness claim, not a liveness ping.** `service_healthy`
conditions convert an unordered set of containers into a dependency graph compose will
actually respect, and the value scales with the number of services you have — this stops
being optional past two or three.

**Seed data belongs in the repository as a file Docker mounts, not as a step in a
document a person carries out.** A realm export committed alongside the compose file is
version-controlled, reviewable, and cannot go stale relative to what the code expects.

**The stack isn't "working" until something outside the containers says so.** A
healthcheck proves the pieces are up; only an end-to-end test — get a token, call the
API — proves they can talk to each other the way the application actually needs. Wire
that test into `docker compose up` itself, not into a separate manual step, or it will be
the first thing skipped under time pressure.
</content>
