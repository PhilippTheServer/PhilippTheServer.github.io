---
layout: post
title: "An Exception Hierarchy and One Handler for FastAPI Error Responses"
subtitle: "Bare HTTPExceptions scattered across a codebase produce inconsistent payloads and lose debugging context."
date: 2025-09-23 09:00:00 +0200
tags: [fastapi, python, api-design]
description: >-
  Raising HTTPException directly from inside route and service code gives
  every error path its own idea of what the response body should contain,
  and throws away the specific context that would make the error debuggable.
  This builds a small domain exception hierarchy and a single handler that
  turns any of them into a consistent, informative response.
---

## The problem

`HTTPException` is the obvious tool for signalling an error from inside a FastAPI route,
and it is usually the first thing reached for:

```python
@router.get("/orders/{order_id}")
def get_order(order_id: int):
    order = find_order(order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")
    if order.customer_id != current_customer_id():
        raise HTTPException(status_code=403, detail="Not your order")
    return order
```

This is fine in isolation, and it stays fine for exactly as long as error handling lives
only in routes. It stops being fine the moment business logic that raises errors moves
into a service function called from several different routes — `HTTPException` is an
HTTP-layer concept, so a service function that raises it has to know it is being called
from an HTTP context, which breaks the moment the same service function gets reused from a
background job or a CLI script with no HTTP request in sight.

The more common failure, though, is inconsistency rather than a layering violation.
Different developers, or the same developer on different days, reach for
`HTTPException` with different `detail` shapes — sometimes a string, sometimes a dict,
sometimes a dict with a `code` field and sometimes without:

```python
raise HTTPException(status_code=404, detail="Order not found")
raise HTTPException(status_code=404, detail={"message": "User not found", "code": "USER_404"})
raise HTTPException(status_code=409, detail={"error": "duplicate_email"})
```

A client trying to distinguish "order not found" from "user not found" programmatically,
rather than by string-matching an English sentence, cannot do it reliably, because only
some of these errors carry a machine-readable code at all, and the ones that do disagree
on the field name. Worse, the actual context that would help debug the failure — which
order ID was requested, which customer made the request — dies at the `raise` statement,
because `detail` is whatever string or dict got typed in that one call site, not something
structured the way a log line would be.

## Working through it

### Domain errors should not know about HTTP

The layering fix is to define exceptions that describe what went wrong in domain terms —
`OrderNotFoundError`, `NotOrderOwnerError` — with no reference to status codes at all, and
let something at the edge of the application translate a domain error into an HTTP
response. This is what makes the same service function reusable outside a request
context: a background job that raises `OrderNotFoundError` can catch it and log it without
ever having heard of a 404.

```python
class AppError(Exception):
    """Base for every domain error the application raises on purpose."""

    def __init__(self, message: str, *, context: dict | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.context = context or {}


class NotFoundError(AppError):
    pass


class OrderNotFoundError(NotFoundError):
    def __init__(self, order_id: int) -> None:
        super().__init__(f"Order {order_id} not found", context={"order_id": order_id})


class ForbiddenError(AppError):
    pass
```

The `context` dict is the part `HTTPException` had no place for: structured data about
*this specific failure*, kept separate from the human-readable message, available to a
handler for logging even if it is deliberately not echoed back to the client.

### One handler, one mapping, one place that decides the status code

FastAPI's `add_exception_handler` lets you register a handler for a base class and have
it catch every subclass — this is the single point where "what HTTP status does this kind
of domain error deserve" gets decided, once, instead of once per call site.

```python
_STATUS_BY_ERROR_TYPE: dict[type[AppError], int] = {
    NotFoundError: 404,
    ForbiddenError: 403,
    ConflictError: 409,
}


def _status_for(error: AppError) -> int:
    for error_type, status in _STATUS_BY_ERROR_TYPE.items():
        if isinstance(error, error_type):
            return status
    return 500


async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    status = _status_for(exc)
    logger.warning(
        "domain error: %s", exc.message, extra={"context": exc.context, "path": request.url.path}
    )
    return JSONResponse(
        status_code=status,
        content={
            "success": False,
            "error": {"type": type(exc).__name__, "message": exc.message},
        },
    )
```

Adding a new kind of domain error to an existing category — a second `NotFoundError`
subclass for a different resource — needs no change to this handler at all; it is caught
by `isinstance` against the base it already inherits from. Adding a genuinely new
*category*, with its own status code, is one new line in `_STATUS_BY_ERROR_TYPE`, not a
new `except` block scattered across routes.

### Deciding what a client is allowed to see

`exc.context` deliberately never appears in the response body in the handler above — only
`exc.message` does. This is a considered choice, not an oversight: context can carry
internal identifiers or values that are fine in a log line read by the team but are not
something to hand to an untrusted client, particularly for errors further up the hierarchy
that were not written with a public response in mind. A field-by-field decision about what
crosses that boundary belongs in the handler, in one place, rather than in the judgement
of whoever writes the next `raise` statement.

### `HTTPException` still has a place

This hierarchy is for *domain* errors — things the business logic itself decides are
wrong. Errors that are inherently about the HTTP layer and nothing else — a malformed
`Authorization` header, a request that violates a rate limit before it reaches any
business logic — are reasonably still raised as `HTTPException` directly from
dependencies, because there is no domain concept underneath them to model. The rule that
keeps this from sliding back into inconsistency is scope, decided once: `HTTPException`
is for the transport layer, `AppError` and its subclasses are for everything a service
function decides.

## The solution

```text
app/
  main.py
  errors.py
  orders/
    router.py
    service.py
tests/
  test_errors.py
requirements.txt
```

```python
# app/errors.py
from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger("app.errors")


class AppError(Exception):
    def __init__(self, message: str, *, context: dict | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.context = context or {}


class NotFoundError(AppError):
    pass


class ForbiddenError(AppError):
    pass


class ConflictError(AppError):
    pass


class OrderNotFoundError(NotFoundError):
    def __init__(self, order_id: int) -> None:
        super().__init__(f"Order {order_id} not found", context={"order_id": order_id})


class NotOrderOwnerError(ForbiddenError):
    def __init__(self, order_id: int, customer_id: str) -> None:
        super().__init__(
            "This order does not belong to the requesting customer",
            context={"order_id": order_id, "customer_id": customer_id},
        )


_STATUS_BY_ERROR_TYPE: dict[type[AppError], int] = {
    NotFoundError: 404,
    ForbiddenError: 403,
    ConflictError: 409,
}


def _status_for(error: AppError) -> int:
    for error_type, status in _STATUS_BY_ERROR_TYPE.items():
        if isinstance(error, error_type):
            return status
    return 500


async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    status = _status_for(exc)
    logger.warning(
        "domain error: %s", exc.message, extra={"context": exc.context, "path": request.url.path}
    )
    return JSONResponse(
        status_code=status,
        content={
            "success": False,
            "error": {"type": type(exc).__name__, "message": exc.message},
        },
    )


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, app_error_handler)
```

```python
# app/orders/service.py
from app.errors import NotOrderOwnerError, OrderNotFoundError

_orders = {
    1: {"id": 1, "customer_id": "cust-1", "total": 42.0},
}


def get_order_for_customer(order_id: int, customer_id: str) -> dict:
    order = _orders.get(order_id)
    if order is None:
        raise OrderNotFoundError(order_id)
    if order["customer_id"] != customer_id:
        raise NotOrderOwnerError(order_id, customer_id)
    return order
```

```python
# app/orders/router.py
from fastapi import APIRouter, Header

from app.orders.service import get_order_for_customer

router = APIRouter(prefix="/orders", tags=["orders"])


@router.get("/{order_id}")
def get_order(order_id: int, x_customer_id: str = Header(...)) -> dict:
    return get_order_for_customer(order_id, x_customer_id)
```

```python
# app/main.py
from fastapi import FastAPI

from app.errors import register_error_handlers
from app.orders.router import router as orders_router

app = FastAPI(title="Exception hierarchy example")
register_error_handlers(app)
app.include_router(orders_router)
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
# tests/test_errors.py
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_order_not_found_returns_404_with_typed_error():
    response = client.get("/orders/999", headers={"x-customer-id": "cust-1"})
    assert response.status_code == 404
    body = response.json()
    assert body["success"] is False
    assert body["error"]["type"] == "OrderNotFoundError"


def test_wrong_customer_returns_403():
    response = client.get("/orders/1", headers={"x-customer-id": "cust-2"})
    assert response.status_code == 403
    assert response.json()["error"]["type"] == "NotOrderOwnerError"


def test_owner_gets_the_order():
    response = client.get("/orders/1", headers={"x-customer-id": "cust-1"})
    assert response.status_code == 200
    assert response.json()["id"] == 1
```

### Verifying it

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pytest tests/ -v
```

Correct output:

```text
tests/test_errors.py::test_order_not_found_returns_404_with_typed_error PASSED
tests/test_errors.py::test_wrong_customer_returns_403 PASSED
tests/test_errors.py::test_owner_gets_the_order PASSED
```

Both error cases return distinct, consistently-shaped bodies, and neither `orders/service.py`
nor `orders/router.py` contains a status code or a `JSONResponse` anywhere — that decision
lives entirely in `errors.py`.

## Conclusion

**A domain exception should not know it will eventually become an HTTP response.** Keeping
`AppError` free of status codes is what lets the same service function be called from a
route, a background task, or a test, and mean the same thing in all three.

**Structured context and the client-facing message are different things, and conflating
them either loses debugging information or leaks it.** Keep both on the exception, and
decide field by field, in the single handler, what a client is allowed to see.

**A category-to-status mapping in one dictionary scales better than a status code at every
`raise` site.** New errors within an existing category require no handler change at all;
new categories require exactly one new line, in exactly one file, rather than an audit of
every route that might need to raise the new kind of error.
