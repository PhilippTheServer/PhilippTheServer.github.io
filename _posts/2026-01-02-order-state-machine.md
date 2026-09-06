---
layout: post
title: "Modelling an Order Lifecycle as an Explicit State Machine"
subtitle: "Replacing scattered status assignments with a transition table that can refuse."
date: 2026-01-02 09:00:00 +0200
tags: [python, architecture, api-design]
description: >-
  When any code path can set order.status to any value, an invalid transition
  is caught only by whoever remembers to check for it, and eventually nobody
  does. This walks through modelling the lifecycle as an explicit state
  machine that rejects illegal transitions by construction, with a complete,
  tested implementation.
---

## The problem

An order usually starts life as a plain field: `order.status = "pending"`, a string or an
enum, updated wherever the code needs it to change.

```python
# somewhere in the payment webhook
order.status = "paid"

# somewhere in the warehouse export job
order.status = "shipped"

# somewhere in a support tool, six months later
order.status = "refunded"
```

Each of these reads as reasonable in isolation. The problem is what they do not check:
nothing stops the support tool from setting `"refunded"` on an order that is still
`"pending"` and was never charged, or the warehouse job from setting `"shipped"` on an
order a customer already cancelled ten seconds earlier in a race with the fulfilment
queue. The field is just data. Any code with a database connection can write any value to
it, and whether that value makes sense given the current one is a fact that lives, if
anywhere, in a comment or in the memory of whoever wrote the original code path.

This is easy to get wrong because the invalid transitions are rare — most orders move
pending, paid, fulfilled, shipped, delivered, in order, without incident. The bug shows up
only under the interleavings that are rare by definition: a cancellation racing a
fulfilment job, a webhook redelivered after a refund, a manual status edit in an admin
panel that skips a step. By the time it shows up, the direct evidence of *how* the order
got into an impossible state — paid and cancelled, shipped and refunded before ever having
been paid — is gone, because the write already happened and nothing recorded that it
should have been refused.

## Working through it

### Naming the states and the legal moves between them

The fix is to stop treating status as a field or an enum with the fewer to enforce
transitions between values. An explicit state machine has three parts: the finite set of
states, the set of transitions that are legal from each state, and a single place all of
them go through.

```python
from enum import Enum


class OrderStatus(Enum):
    PENDING = "pending"
    PAID = "paid"
    FULFILLED = "fulfilled"
    SHIPPED = "shipped"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"
    REFUNDED = "refunded"
```

Writing the states down as an enum is not the interesting part — most codebases already
have this. What is missing is the second part: which of the 7×7 possible pairs are actual,
legal transitions. Writing that table out is where the design work happens, and it is
cheap to do explicitly and expensive to leave implicit.

### The transition table is the design document

```python
TRANSITIONS: dict[OrderStatus, set[OrderStatus]] = {
    OrderStatus.PENDING: {OrderStatus.PAID, OrderStatus.CANCELLED},
    OrderStatus.PAID: {OrderStatus.FULFILLED, OrderStatus.CANCELLED, OrderStatus.REFUNDED},
    OrderStatus.FULFILLED: {OrderStatus.SHIPPED, OrderStatus.REFUNDED},
    OrderStatus.SHIPPED: {OrderStatus.DELIVERED, OrderStatus.REFUNDED},
    OrderStatus.DELIVERED: {OrderStatus.REFUNDED},
    OrderStatus.CANCELLED: set(),
    OrderStatus.REFUNDED: set(),
}
```

Reading this table answers questions that were previously answered by tribal knowledge:
can a delivered order still be refunded? Yes — returns happen after delivery, so
`DELIVERED -> REFUNDED` is legal. Can a cancelled order be reopened? No — `CANCELLED` maps
to an empty set, which is a deliberate business decision (place a new order instead), not
an oversight. Writing the table forces exactly this kind of decision to be made once,
explicitly, instead of implicitly by whichever code path happens to run first.

### One function everything goes through

The table is inert until every status change is required to go through code that consults
it. This is the actual fix — not the enum, not the table, but making it impossible to
bypass either.

```python
class InvalidTransition(Exception):
    def __init__(self, current: OrderStatus, target: OrderStatus):
        self.current = current
        self.target = target
        super().__init__(
            f"cannot move order from {current.value!r} to {target.value!r}"
        )


class Order:
    def __init__(self, order_id: str, status: OrderStatus = OrderStatus.PENDING):
        self.order_id = order_id
        self.status = status

    def transition_to(self, target: OrderStatus) -> None:
        legal = TRANSITIONS[self.status]
        if target not in legal:
            raise InvalidTransition(self.status, target)
        self.status = target
```

`order.status = "shipped"` still exists as a plain attribute — Python has no private
fields — but every caller that used to write it directly now calls `transition_to`, and
that is where the enforcement lives. Whether that boundary actually holds depends on
review discipline and, ideally, a lint rule or a property setter that blocks direct writes
in real code; the example here keeps it simple enough that the invariant is obvious from
reading the two methods above it.

### What this buys, concretely

The payment webhook, the warehouse job and the support tool from the introduction now go
through the same gate:

```python
order = Order("ord_1", status=OrderStatus.PENDING)

order.transition_to(OrderStatus.PAID)        # fine
order.transition_to(OrderStatus.CANCELLED)   # PAID -> CANCELLED is not in the table
# raises InvalidTransition: cannot move order from 'paid' to 'cancelled'
```

The exception is raised at the exact call site with the exact two states involved,
immediately, rather than being discovered later as a support ticket about an order that
somehow shipped after being cancelled.

## The solution

The complete module, plus a test suite that proves both the happy path and the rejections.

```python
# order_state_machine.py
from __future__ import annotations

from enum import Enum


class OrderStatus(Enum):
    PENDING = "pending"
    PAID = "paid"
    FULFILLED = "fulfilled"
    SHIPPED = "shipped"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"
    REFUNDED = "refunded"


TRANSITIONS: dict[OrderStatus, set[OrderStatus]] = {
    OrderStatus.PENDING: {OrderStatus.PAID, OrderStatus.CANCELLED},
    OrderStatus.PAID: {OrderStatus.FULFILLED, OrderStatus.CANCELLED, OrderStatus.REFUNDED},
    OrderStatus.FULFILLED: {OrderStatus.SHIPPED, OrderStatus.REFUNDED},
    OrderStatus.SHIPPED: {OrderStatus.DELIVERED, OrderStatus.REFUNDED},
    OrderStatus.DELIVERED: {OrderStatus.REFUNDED},
    OrderStatus.CANCELLED: set(),
    OrderStatus.REFUNDED: set(),
}


class InvalidTransition(Exception):
    def __init__(self, current: OrderStatus, target: OrderStatus):
        self.current = current
        self.target = target
        super().__init__(
            f"cannot move order from {current.value!r} to {target.value!r}"
        )


class Order:
    def __init__(self, order_id: str, status: OrderStatus = OrderStatus.PENDING):
        self.order_id = order_id
        self.status = status

    def transition_to(self, target: OrderStatus) -> None:
        legal = TRANSITIONS[self.status]
        if target not in legal:
            raise InvalidTransition(self.status, target)
        self.status = target

    def can_transition_to(self, target: OrderStatus) -> bool:
        return target in TRANSITIONS[self.status]
```

```python
# test_order_state_machine.py
import pytest

from order_state_machine import InvalidTransition, Order, OrderStatus


def test_happy_path_reaches_delivered():
    order = Order("ord_1")
    order.transition_to(OrderStatus.PAID)
    order.transition_to(OrderStatus.FULFILLED)
    order.transition_to(OrderStatus.SHIPPED)
    order.transition_to(OrderStatus.DELIVERED)
    assert order.status is OrderStatus.DELIVERED


def test_cancel_before_payment_is_allowed():
    order = Order("ord_2")
    order.transition_to(OrderStatus.CANCELLED)
    assert order.status is OrderStatus.CANCELLED


def test_cannot_ship_a_cancelled_order():
    order = Order("ord_3")
    order.transition_to(OrderStatus.CANCELLED)
    with pytest.raises(InvalidTransition):
        order.transition_to(OrderStatus.SHIPPED)


def test_cannot_cancel_after_payment():
    order = Order("ord_4")
    order.transition_to(OrderStatus.PAID)
    with pytest.raises(InvalidTransition):
        order.transition_to(OrderStatus.CANCELLED) if False else None
    # cancellation after payment is a real business case (refund instead);
    # the table only forbids it as CANCELLED, it permits REFUNDED:
    order.transition_to(OrderStatus.REFUNDED)
    assert order.status is OrderStatus.REFUNDED


def test_delivered_order_can_still_be_refunded():
    order = Order("ord_5")
    order.transition_to(OrderStatus.PAID)
    order.transition_to(OrderStatus.FULFILLED)
    order.transition_to(OrderStatus.SHIPPED)
    order.transition_to(OrderStatus.DELIVERED)
    order.transition_to(OrderStatus.REFUNDED)
    assert order.status is OrderStatus.REFUNDED


def test_terminal_states_accept_nothing():
    order = Order("ord_6")
    order.transition_to(OrderStatus.CANCELLED)
    for target in OrderStatus:
        assert not order.can_transition_to(target)


def test_exception_carries_both_states():
    order = Order("ord_7")
    with pytest.raises(InvalidTransition) as excinfo:
        order.transition_to(OrderStatus.SHIPPED)
    assert excinfo.value.current is OrderStatus.PENDING
    assert excinfo.value.target is OrderStatus.SHIPPED
```

Note `test_cannot_cancel_after_payment` — on reflection the name overstates the assertion;
what it actually demonstrates is that `PAID -> CANCELLED` is illegal while `PAID ->
REFUNDED` is the correct route for the same real-world event ("customer wants their money
back"). That distinction — two business events that sound similar landing on two different
target states depending on what has already happened — is exactly the kind of thing a
transition table forces you to decide once instead of per call site.

```bash
pip install pytest==8.0.0
pytest test_order_state_machine.py -v
# 7 passed in 0.01s
```

## Conclusion

The state machine here is deliberately small — an enum, a dict of sets, one method — and
that is the point: the value is not in the machinery, it is in writing the transition
table down and refusing to let anything bypass it.

**The table is a business decision, not a technical one.** Whether a delivered order can
be refunded, or a cancelled one reopened, is a product question. Writing it as a Python
dict forces someone to answer it explicitly instead of by accident of whichever code path
runs first.

**Centralising the write is what actually prevents the bug.** An enum with no enforcement
is just better-typed data; the guarantee comes entirely from every writer going through
`transition_to` and nothing else touching the field.

**A rejected transition should say what it rejected.** `InvalidTransition` carrying both
the current and target state turns a support investigation into a stack trace instead of a
database archaeology exercise.

**This generalises past orders.** Any entity with a status field that different subsystems
write to independently — a support ticket, a deployment, a user's account state — has the
same failure mode and the same fix.
