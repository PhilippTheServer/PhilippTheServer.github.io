---
layout: post
title: "When Microservice Decomposition Is the Wrong Default"
subtitle: "Counting the coordination cost of a network boundary before drawing one."
date: 2026-08-21 09:00:00 +0200
tags: [architecture, docker]
description: >-
  Splitting a system into microservices before there is an actual scaling,
  ownership or isolation reason costs real coordination: versioned
  contracts, retries, and version-skew windows that a single process never
  has. This article builds the same three responsibilities as a modular
  monolith with an enforced internal boundary, so a later real split is
  mechanical rather than a rewrite.
---

## The problem

A small system with three clear responsibilities — say, users, billing, and notifications —
gets split into three services early, often before any of them has a distinct scaling
profile, a separate team, or a reason to fail independently of the others. The reasoning is
rarely examined: this is what a modern system is supposed to look like.

What that split actually buys, immediately, is a set of costs that were free a moment ago.
A function call between two responsibilities becomes a network call, with its own timeout,
retry policy, and failure mode that a same-process call never had. The shape of the data
passed between them has to become a published contract — an OpenAPI schema, a message
format — with its own versioning and deprecation policy, because the two sides no longer
deploy together. And because they no longer deploy together, there is now a window, however
short, where one side has shipped a change the other doesn't know about yet: a new field
the billing service expects that the users service hasn't started sending, or a field it
stopped sending that billing still reads. That kind of mismatch would have been a
compile-time or import-time failure inside one process. Across a network boundary it's a
live incident, discovered by whichever request happens to hit it during the rollout window.

## Working through it

### Name the reason before naming the boundary

A network boundary is justified by one of a few concrete things: a genuine, order-of-
magnitude difference in scaling needs between two parts of the system; teams that are
organisationally separate and cannot coordinate a shared release; or a real requirement
that one part's failure must not take down another. If none of those hold for a given pair
of responsibilities, the boundary being proposed is aesthetic, and aesthetics don't justify
the cost that follows.

### Count the coordination cost being taken on

Concretely, a network boundary means: a released API and a policy for changing it without
breaking existing callers; retry, timeout, and circuit-breaking logic for a call that used
to just be a function invocation; distributed tracing to see a request's path across the
boundary at all; and, during any migration that touches both sides, two deployments to keep
compatible with each other instead of one atomic commit that the type checker or import
graph already validated.

### Keep the boundary in the code, not on the network

A modular monolith gets the same internal discipline — components with a defined public
interface that other components may depend on, and internals they may not reach into —
enforced by code review and, more durably, by a linter that fails the build the moment one
module imports another's private implementation. Calls between modules stay function calls.
Deployment stays one artefact. A shape mismatch between two modules is a type error caught
before merge, not a live version-skew incident discovered in production.

### Split later, along the seam already drawn

If a genuine reason to split eventually arrives — one module needs to scale far beyond the
others, or a separate team now owns it — the extraction is mechanical, because the module
boundary in the code already is the future service boundary: its public interface becomes
the API, and its internals were already off-limits to everyone else. Splitting a monolith
that never had internal boundaries at all is not a smaller version of this; it's a
redesign.

## The solution

A modular monolith with the same three responsibilities, each with a private storage detail
and a public interface:

```python
# app/users/_storage.py
"""Internal storage detail. Nothing outside app.users may import this."""
from dataclasses import dataclass


@dataclass(frozen=True)
class User:
    id: int
    email: str


_USERS = {1: User(id=1, email="a@example.com")}


def find(user_id: int) -> User | None:
    return _USERS.get(user_id)
```

```python
# app/users/service.py
"""Public interface of the users module."""
from app.users._storage import User, find


def get_user(user_id: int) -> User | None:
    return find(user_id)
```

```python
# app/billing/service.py
"""Billing depends on users only through its public function, not its
storage. That is the boundary a network call would otherwise enforce."""
from app.users.service import get_user


class UnknownUserError(ValueError):
    pass


def charge(user_id: int, amount_cents: int) -> str:
    user = get_user(user_id)
    if user is None:
        raise UnknownUserError(f"no such user: {user_id}")
    return f"charged {amount_cents} cents to {user.email}"
```

```python
# app/notifications/service.py
def notify(email: str, message: str) -> str:
    return f"sent to {email}: {message}"
```

```python
# app/main.py
from fastapi import FastAPI, HTTPException

from app.billing.service import UnknownUserError, charge
from app.notifications.service import notify
from app.users.service import get_user

app = FastAPI()


@app.post("/charge/{user_id}")
def charge_user(user_id: int, amount_cents: int):
    try:
        result = charge(user_id, amount_cents)
    except UnknownUserError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    user = get_user(user_id)
    notify(user.email, "your card was charged")
    return {"result": result}
```

The boundary enforced as a lint rule rather than a network hop:

```ini
# .importlinter
[importlinter]
root_package = app

[importlinter:contract:1]
name = billing may only use the public users interface
type = forbidden
source_modules =
    app.billing
forbidden_modules =
    app.users._storage
```

```dockerfile
# Dockerfile
FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir fastapi==0.115.0 uvicorn==0.30.6
COPY app ./app
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

```yaml
# docker-compose.yml
services:
  monolith:
    build: .
    ports:
      - "8000:8000"
```

```bash
docker compose up --build -d
curl -s -X POST "http://localhost:8000/charge/1?amount_cents=500"
```

```
{"result":"charged 500 cents to a@example.com"}
```

```bash
pip install import-linter==2.1
lint-imports
```

```
Contracts: 1 kept, 0 broken.
```

Changing `app/billing/service.py` to import `find` directly from `app.users._storage`
instead of going through `get_user` makes `lint-imports` fail immediately, before the code
ever reaches review — which is the same discipline a network boundary would have enforced,
at the cost of a lint rule instead of an API contract and a deploy pipeline.

## Conclusion

A network boundary earns its cost when it follows an actual scaling, ownership, or
isolation need; drawn without one, it adds coordination overhead — versioned contracts,
retries, timeouts, skew windows — with nothing on the other side of the ledger to pay for
it.

A module boundary enforced by an import rule delivers most of the discipline microservices
are chosen for — a defined interface, hidden internals — at the cost of a linter instead of
a network stack.

When a real reason to split eventually shows up, extraction from a clean module boundary is
close to mechanical; extraction from a monolith that never had one is a rewrite wearing a
migration's clothes.

The honest cost of the network version is not hypothetical: retries and timeouts for calls
that used to just return, a schema to version, and a window during every rollout where the
two sides can disagree about the shape of the data between them. None of that is required
until something specific — real scale, real separate ownership, real failure isolation —
actually demands it.
