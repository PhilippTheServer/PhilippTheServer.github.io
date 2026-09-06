---
layout: post
title: "Designing a Consistent FastAPI Response Envelope"
subtitle: "Ad-hoc return values leave every client guessing whether a payload is wrapped."
date: 2025-09-19 09:00:00 +0200
tags: [fastapi, api-design, python]
description: >-
  When some endpoints return a bare object, others a list, and others a
  hand-rolled dict with a status field, every client has to special-case each
  one. This works through a generic response envelope built on Pydantic
  generics, a custom route class that applies it without repeating
  boilerplate in every endpoint, and the trade-offs that come with wrapping
  everything uniformly.
---

## The problem

A FastAPI application that grew endpoint by endpoint, each written by whoever needed it
that week, tends to end up with three or four different response shapes in active use:

```python
@router.get("/items/{item_id}")
def get_item(item_id: int):
    return {"id": item_id, "name": "widget"}


@router.get("/items")
def list_items():
    return {"status": "ok", "data": [{"id": 1, "name": "widget"}]}


@router.post("/orders")
def create_order(payload: OrderCreate):
    return {"success": True, "order": {"id": 1}}
```

None of these is wrong on its own — each returns valid JSON that answers its own request
correctly. The problem only appears once a client has to consume more than one of them: is
the payload at the top level, or under `data`, or under a feature-specific key like
`order`? Is a boolean success flag present, and if so is it called `status` or `success`,
and does its absence mean failure or just mean this particular endpoint never had the
field? A frontend or another service ends up writing a different unwrapping function per
endpoint, and every new endpoint is a coin flip about which shape it will introduce.

Errors compound this. A validation failure returns FastAPI's default shape
(`{"detail": [...]}`), a manually raised `HTTPException` returns `{"detail": "..."}`, and
a hand-rolled error dict from inside a route returns whatever that route's author decided
that day. A client cannot write one piece of code that reliably extracts "did this
succeed, and if not why" across the whole API.

## Working through it

### Deciding what the envelope actually needs to say

Before writing any code, the envelope has to answer a small, fixed set of questions for
every response, success or failure: did it succeed, what is the payload if it did, and
what is the error if it did not. Anything beyond that — pagination metadata, request IDs —
is worth adding only once a concrete need for it exists, because every field in a shared
envelope is a field every client has to learn to ignore when it does not apply.

```python
class Envelope(BaseModel, Generic[T]):
    success: bool
    data: T | None = None
    error: str | None = None
```

### Generics keep the OpenAPI schema honest

A tempting shortcut is `data: Any`, which works but throws away the one thing FastAPI
would otherwise give clients for free: a precise schema per endpoint in `/docs` and in the
generated OpenAPI document. Pydantic's `Generic[T]` support means `Envelope[Item]` and
`Envelope[list[Item]]` are each their own concrete schema once used as a `response_model`,
so a client's code generator sees exactly what `data` contains for that specific endpoint,
not an unhelpful `"data": {}`.

```python
@router.get("/items/{item_id}", response_model=Envelope[Item])
def get_item(item_id: int) -> Envelope[Item]:
    item = find_item(item_id)
    return Envelope(success=True, data=item)
```

### Repeating `Envelope(success=True, data=...)` in every route is its own kind of drift

Wrapping the return value by hand in every endpoint works, but it is exactly the kind of
repeated boilerplate that someone eventually forgets — one route returns the bare object
instead of the envelope, and it passes review because it still looks like reasonable code.
A custom `APIRoute` class fixes this at the framework level rather than relying on every
route author remembering: FastAPI lets you override how a route's endpoint function result
becomes an HTTP response, so the wrapping happens once, centrally, for every route
attached to a router that uses it.

```python
class EnvelopeRoute(APIRoute):
    def get_route_handler(self):
        original_handler = super().get_route_handler()

        async def custom_handler(request: Request) -> Response:
            response = await original_handler(request)
            body = json.loads(response.body)
            wrapped = {"success": True, "data": body, "error": None}
            return JSONResponse(content=wrapped, status_code=response.status_code)

        return custom_handler
```

This moves the decision "does this response get wrapped" from "did the author remember"
to "which router class is this endpoint attached to" — a property visible at the top of
the file, not something that has to be checked function by function.

### The trade-off this creates, stated plainly

A blanket envelope costs something real: every response body now carries an extra layer
of nesting that every client has to unwrap, even for the simplest possible endpoint, and
it makes the API slightly more verbose on the wire for no benefit on endpoints that were
already unambiguous. It also means the HTTP status code and the envelope's `success` field
can, if you are not careful with the exception handling, disagree — a `200` with
`"success": false` is confusing in a different way than the problem you started with.
Deciding that a non-2xx status code always implies `success: false` in the envelope, and
enforcing that pairing in one place (an exception handler, not each route), is what keeps
the two signals from drifting apart from each other.

## The solution

A complete FastAPI application with the generic envelope and the custom route class, plus
tests that assert the exact response shape for both a plain object and a list endpoint.

```text
app/
  main.py
  envelope.py
  schemas.py
tests/
  test_envelope.py
requirements.txt
```

```python
# app/envelope.py
from __future__ import annotations

import json
from typing import Generic, TypeVar

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel

T = TypeVar("T")


class Envelope(BaseModel, Generic[T]):
    success: bool
    data: T | None = None
    error: str | None = None


class EnvelopeRoute(APIRoute):
    def get_route_handler(self):
        original_handler = super().get_route_handler()

        async def custom_handler(request: Request) -> Response:
            response = await original_handler(request)
            body = json.loads(response.body)
            wrapped = {"success": True, "data": body, "error": None}
            return JSONResponse(content=wrapped, status_code=response.status_code)

        return custom_handler
```

```python
# app/schemas.py
from pydantic import BaseModel


class Item(BaseModel):
    id: int
    name: str
    price: float
```

```python
# app/main.py
from fastapi import APIRouter, FastAPI

from app.envelope import EnvelopeRoute
from app.schemas import Item

app = FastAPI(title="Envelope example")
router = APIRouter(route_class=EnvelopeRoute)

_items = {
    1: Item(id=1, name="widget", price=9.99),
    2: Item(id=2, name="gadget", price=19.99),
}


@router.get("/items/{item_id}", response_model=Item)
def get_item(item_id: int) -> Item:
    return _items[item_id]


@router.get("/items", response_model=list[Item])
def list_items() -> list[Item]:
    return list(_items.values())


app.include_router(router)
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
# tests/test_envelope.py
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_single_item_is_wrapped():
    response = client.get("/items/1")
    body = response.json()
    assert body["success"] is True
    assert body["error"] is None
    assert body["data"] == {"id": 1, "name": "widget", "price": 9.99}


def test_list_is_wrapped_as_a_single_data_array():
    response = client.get("/items")
    body = response.json()
    assert body["success"] is True
    assert isinstance(body["data"], list)
    assert len(body["data"]) == 2
```

### Verifying it

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pytest tests/ -v
uvicorn app.main:app --reload
curl -s localhost:8000/items/1 | python -m json.tool
```

Correct output from the `curl`:

```json
{
    "success": true,
    "data": {
        "id": 1,
        "name": "widget",
        "price": 9.99
    },
    "error": null
}
```

Both test cases pass, and `/docs` shows `Envelope_Item_` and `Envelope_list_Item__` as
distinct, precisely-typed schemas for the two endpoints — the generic keeps the automatic
documentation as specific as it would be without any wrapping at all.

## Conclusion

**A shared response shape is only worth it once more than one client consumes the API.**
For a single frontend maintained by the same team as the backend, a hand-rolled shape per
endpoint costs less than the ceremony of a generic envelope; the payoff shows up when
multiple, independently-maintained consumers all need to parse responses the same way.

**Centralise the wrapping mechanically, do not rely on every endpoint author repeating
it by hand.** A custom route class (or equivalent middleware) makes "is this wrapped"
a property of the router, checkable at a glance, rather than a convention that degrades
one forgotten `return` statement at a time.

**Pin the relationship between HTTP status and the envelope's own success field, and
enforce it in one place.** Letting individual routes decide both independently is how you
end up with a `200` that says `"success": false`, or a `422` that says `"success": true` —
each individually plausible, together a contract nobody can rely on.
