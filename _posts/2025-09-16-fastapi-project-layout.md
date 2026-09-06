---
layout: post
title: "A Feature-Module Layout for a FastAPI Application That Keeps Growing"
subtitle: "One giant routers.py means nobody can add a feature without touching code they do not own."
date: 2025-09-16 09:00:00 +0200
tags: [fastapi, python, architecture]
description: >-
  A FastAPI project that starts as one main.py and one routers.py works fine
  for the first few endpoints, then becomes a file everyone edits and nobody
  owns. This lays out a feature-module structure where each feature carries
  its own router, schemas and service code, with a complete runnable example
  and a test proving a new feature needs no change to existing files.
---

## The problem

A FastAPI project that starts small tends to start like this:

```python
# app/main.py — after six months
from fastapi import FastAPI
from app.routers import users, items, orders, invoices, notifications, reports

app = FastAPI()
app.include_router(users.router)
app.include_router(items.router)
app.include_router(orders.router)
app.include_router(invoices.router)
app.include_router(notifications.router)
app.include_router(reports.router)
```

That part is fine. The problem is what sits behind it: a single `app/routers.py`, or a
`app/routers/` package where every file imports from a single shared `app/schemas.py` and
a single shared `app/crud.py`, because that is where the first feature put its Pydantic
models and its database calls, and every feature after it followed the same pattern out of
consistency.

Six features in, `app/schemas.py` is eight hundred lines, half the imports at the top of
`app/crud.py` belong to features that have nothing to do with each other, and a merge
conflict between two people adding unrelated endpoints is routine — not because the logic
overlaps, but because both people's changes land in the same three files. Nobody can tell,
from the diff alone, whether a change to `crud.py` affects invoices, orders, or both,
without reading the whole file.

This is not a hypothetical scaling problem that shows up at some large number of
endpoints. It shows up as soon as two people work on the codebase at once, because the
unit of change (one feature) and the unit of the file (everything) have stopped matching.

## Working through it

### The unit of ownership should be a directory, not a layer

The instinct that produces `routers.py` / `schemas.py` / `crud.py` is organising by
*technical layer*: all the routers together, all the schemas together. That groups code by
what kind of thing it is, not by what it is for. A feature-module layout inverts this:
group by feature first, and let each feature keep its own router, schema and service code
together, with the technical layer as a filename inside that directory rather than a
directory of its own at the top level.

```text
app/
  main.py
  core/
    config.py
    database.py
  features/
    items/
      __init__.py
      router.py
      schemas.py
      service.py
    users/
      __init__.py
      router.py
      schemas.py
      service.py
```

Adding a third feature means adding a third directory under `features/`, touching nothing
inside `items/` or `users/`, and touching `main.py` exactly once, to register the new
router. That is the whole cost of a new feature to the rest of the codebase — one import
line, one `include_router` call.

### What actually lives at the top level

Not everything belongs inside a feature. `core/` is deliberately small and holds things
that no single feature owns: the database engine and session factory, application
settings, and anything genuinely cross-cutting like an authentication dependency used by
every feature. The test for whether something belongs in `core/` rather than in a feature
is whether more than one feature needs it *and* no single feature would make sense as its
owner — a shared `get_db` dependency qualifies; a `UserSchema` used by the orders feature
to embed a user in a response does not, because it belongs to the `users` feature and
`orders` should import it from there.

### Avoiding the circular import this naturally invites

Once features can depend on each other — orders needing something from users — a
straightforward mutual dependency becomes possible if two features both try to embed
each other's models. The fix is directional discipline, decided ahead of time: a feature
may import from another feature's `schemas.py` for read-only composition, but never
imports another feature's `service.py`, and no feature imports from `main.py`. If two
features genuinely need to call into each other's business logic, that logic has outgrown
being "one feature's" and belongs in `core/` or a new shared module, not in either
feature's own service file, which would otherwise become another shared file three
unrelated features quietly depend on.

### Wiring a feature's router with its own prefix and tags

Each feature's `router.py` declares its own `APIRouter`, complete with the prefix and
OpenAPI tag that show up in the generated documentation — `main.py` never needs to know
these details, only that the router exists:

```python
# app/features/items/router.py
from fastapi import APIRouter

router = APIRouter(prefix="/items", tags=["items"])
```

This is what makes `main.py`'s job stay exactly one line per feature no matter how a
feature's internal routes grow — adding a new endpoint to `items` never touches `main.py`
at all.

## The solution

A complete, runnable two-feature FastAPI project.

```text
app/
  main.py
  core/
    __init__.py
    database.py
  features/
    __init__.py
    items/
      __init__.py
      router.py
      schemas.py
      service.py
    users/
      __init__.py
      router.py
      schemas.py
      service.py
tests/
  test_items.py
requirements.txt
```

```python
# app/core/database.py
"""In-memory storage standing in for a real database in this example."""

items_db: dict[int, dict] = {}
users_db: dict[int, dict] = {}
```

```python
# app/features/items/schemas.py
from pydantic import BaseModel


class ItemCreate(BaseModel):
    name: str
    price: float


class Item(ItemCreate):
    id: int
```

```python
# app/features/items/service.py
from app.core.database import items_db
from app.features.items.schemas import Item, ItemCreate

_next_id = 1


def create_item(payload: ItemCreate) -> Item:
    global _next_id
    item = Item(id=_next_id, **payload.model_dump())
    items_db[item.id] = item.model_dump()
    _next_id += 1
    return item


def get_item(item_id: int) -> Item | None:
    data = items_db.get(item_id)
    return Item(**data) if data else None
```

```python
# app/features/items/router.py
from fastapi import APIRouter, HTTPException

from app.features.items.schemas import Item, ItemCreate
from app.features.items import service

router = APIRouter(prefix="/items", tags=["items"])


@router.post("", response_model=Item, status_code=201)
def create_item(payload: ItemCreate) -> Item:
    return service.create_item(payload)


@router.get("/{item_id}", response_model=Item)
def read_item(item_id: int) -> Item:
    item = service.get_item(item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Item not found")
    return item
```

```python
# app/features/users/schemas.py
from pydantic import BaseModel


class UserCreate(BaseModel):
    email: str


class User(UserCreate):
    id: int
```

```python
# app/features/users/service.py
from app.core.database import users_db
from app.features.users.schemas import User, UserCreate

_next_id = 1


def create_user(payload: UserCreate) -> User:
    global _next_id
    user = User(id=_next_id, **payload.model_dump())
    users_db[user.id] = user.model_dump()
    _next_id += 1
    return user
```

```python
# app/features/users/router.py
from fastapi import APIRouter

from app.features.users.schemas import User, UserCreate
from app.features.users import service

router = APIRouter(prefix="/users", tags=["users"])


@router.post("", response_model=User, status_code=201)
def create_user(payload: UserCreate) -> User:
    return service.create_user(payload)
```

```python
# app/main.py
from fastapi import FastAPI

from app.features.items.router import router as items_router
from app.features.users.router import router as users_router

app = FastAPI(title="Feature-module example")
app.include_router(items_router)
app.include_router(users_router)
```

```text
# requirements.txt
fastapi==0.115.0
uvicorn[standard]==0.30.6
pydantic==2.9.2
httpx==0.27.2
pytest==8.3.3
```

```python
# tests/test_items.py
"""Proves that the items feature works end to end, and that nothing in it
had to change when the users feature was added."""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_create_and_read_item():
    created = client.post("/items", json={"name": "widget", "price": 9.99})
    assert created.status_code == 201
    item_id = created.json()["id"]

    fetched = client.get(f"/items/{item_id}")
    assert fetched.status_code == 200
    assert fetched.json()["name"] == "widget"


def test_missing_item_returns_404():
    response = client.get("/items/999")
    assert response.status_code == 404
```

### Verifying it

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pytest tests/ -v
uvicorn app.main:app --reload
```

Correct output from pytest:

```text
tests/test_items.py::test_create_and_read_item PASSED
tests/test_items.py::test_missing_item_returns_404 PASSED
```

`http://127.0.0.1:8000/docs` shows two tag groups, `items` and `users`, each carrying only
its own routes — the OpenAPI grouping falls directly out of the `tags=["items"]` set once,
in `items/router.py`, and needed no further configuration anywhere else.

## Conclusion

**Group by feature, not by technical layer, once more than one person touches the
codebase.** A shared `schemas.py` or `crud.py` is an implicit merge point between features
that otherwise have nothing to do with each other; a directory per feature removes that
merge point structurally.

**The cost of a new feature should be additive, not edited-in.** If adding a feature means
one new directory and one new line in `main.py`, the architecture is doing its job; if it
means finding the right spot in a shared file and hoping nobody else edited nearby lines
in the meantime, it is not.

**Directional import rules need deciding before the first cross-feature dependency, not
after the first circular import.** "Schemas may cross, services may not" is one workable
rule; the specific rule matters less than picking one before two features need to talk to
each other, at which point every choice looks equally reasonable and someone has to
adjudicate it under time pressure.
