---
layout: post
title: "Automating Order Processing Before the Product Catalogue"
subtitle: "A shop earns its money in what happens after the customer clicks buy, so OpenTaberna automated that first and left the product pages for later."
date: 2026-09-25 00:20:00 +0200
tags: [automation, architecture, reliability, python]
description: >-
  Why OpenTaberna automated order processing first: webhook-confirmed payment,
  reserved stock, outbox-driven DHL labels, and two human steps per order.
---

## The problem

Every shop system I have looked at leads with its catalogue. The demo shows variants,
galleries, reviews, cross-selling and a search box that forgives typos, and it is very
good at getting a customer to the checkout. Then the demo ends, just before the part that
takes most of the work.

For a small shop, the work starts when an order comes in. Someone has to confirm that the
payment really arrived, and not just that the browser was redirected to a success page.
They have to make sure the item is still in stock, preferably before selling it twice.
Then they print a label, copy a tracking number into an email, pack the parcel, hand it
over, and keep the documents somewhere the tax office can find them later. When the parcel
comes back, they do most of it again in reverse.

None of this looks good in a demo, and all of it happens for every single order. A
beautiful product page saves you nothing per order. A manual step costs you something on
every order, forever. And a manual step done at eleven at night, after a full day of
packing, is where the wrong label ends up on the wrong parcel.

So when Malte and I started [OpenTaberna](/posts/opentaberna-headless-open-source-shop/),
we reversed the usual order. The catalogue in the first version is deliberately plain:
items, prices, images, stock. The effort went into the part the customer never sees.

## Working through it

### Count the hands, not the features

The useful question about any step is who performs it. A feature list says the shop
"supports DHL". The question that matters is whether a person has to *do* something for
each order, and what goes wrong when that person is tired.

An order in OpenTaberna has seven states and nine permitted transitions. The code enforces
them in one table, `_ALLOWED_TRANSITIONS`, and I went through each edge and wrote down who
moves an order across it:

- the **customer**, by starting a checkout or cancelling a draft
- **Stripe**, by sending a webhook that says a payment succeeded, failed or was refunded
- a **human** in the back office

The goal was that the human column should contain only the things that genuinely need
hands: putting a mug in a box, for example. Nothing should require a human to *know*
something the system could have known first.

### The webhook is the truth, the redirect is a rumour

A customer returning to the "thank you" page proves only that a browser followed a link.
Browsers close, networks drop, and people press back. The payment provider's webhook is the
authoritative signal, so it is the only thing that marks an order `PAID`.

Webhooks come with their own problem: they are delivered at least once, which in practice
means occasionally twice. Each Stripe event ID is written to a `webhook_events` table with
a unique constraint, in the same transaction as the state change. A redelivered event finds
its row already there and does nothing. Nobody has to notice that a payment was booked
twice, because it cannot be. [The full mechanism has its own article.](/posts/stripe-webhook-idempotency/)

### Reserve the stock at checkout, not at payment

Stock is reserved when checkout starts and committed when the payment webhook arrives. A
reservation that is never paid expires. A worker job sweeps expired reservations every
five minutes and puts the stock back. The last unit cannot be sold twice, because
[a check constraint in PostgreSQL refuses to let `reserved` exceed `on_hand`](/posts/stock-reservation-check-constraint/).
The database says no, so nobody has to count shelves after an argument with a customer.

### Labels through an outbox, not through hope

Creating a DHL label means calling someone else's API, and someone else's API will
eventually be down. If the request handler called DHL directly, a timeout would leave an
order that is paid and marked for shipping but has no label, and nobody would be told.

So the handler calls nothing. It writes an outbox row in the same transaction as the
shipment, and a worker picks the row up, calls the carrier, stores the label, and retries
with backoff if the carrier is having a bad day. This is the code that writes the row, from
the API repository:

```python
async def enqueue_label_job(
    session: AsyncSession,
    shipment_id: UUID,
    order_id: UUID,
    label_format: str,
) -> OutboxEventDB:
    payload = json.dumps(
        {
            "shipment_id": str(shipment_id),
            "order_id": str(order_id),
            "label_format": label_format,
        }
    )

    event = OutboxEventDB(
        event_type=CREATE_LABEL_EVENT,
        payload=payload,
        status=OutboxStatus.PENDING.value,
        attempts=0,
    )
    session.add(event)
    await session.flush()
    return event
```

It does not commit, and it does not touch Redis. The caller owns the transaction, so the
shipment and the promise to label it are written together or not at all. If the process
crashes after the commit, the outbox sweep finds the row and enqueues it again. The
[transactional outbox article](/posts/transactional-outbox/) explains why this is the only
arrangement that survives a crash at every point.

The carrier is behind a `CarrierAdapter` interface. DHL is one implementation, and a
manual carrier, where you type in the tracking number yourself, is another. Adding a
carrier means writing a new class, not editing the order code.

## The solution

Here is the whole lifecycle as a runnable script. The transition table is copied from
`order_validation.py`. The `ACTOR` map is the "count the hands" exercise written as data,
with assertions so that it cannot quietly drift from the table:

```python
from enum import StrEnum


class OrderStatus(StrEnum):
    DRAFT = "draft"
    PENDING_PAYMENT = "pending_payment"
    PAID = "paid"
    READY_TO_SHIP = "ready_to_ship"
    SHIPPED = "shipped"
    CANCELLED = "cancelled"
    REFUNDED = "refunded"


S = OrderStatus
ALLOWED: dict[S, set[S]] = {
    S.DRAFT: {S.PENDING_PAYMENT, S.CANCELLED},
    S.PENDING_PAYMENT: {S.PAID, S.CANCELLED},
    S.PAID: {S.READY_TO_SHIP, S.REFUNDED},
    S.READY_TO_SHIP: {S.SHIPPED, S.REFUNDED},
    S.SHIPPED: {S.REFUNDED},
    S.CANCELLED: set(),
    S.REFUNDED: set(),
}

# Who moves the order across each edge. Everything not "human" happens
# without anyone opening the back office.
ACTOR = {
    (S.DRAFT, S.PENDING_PAYMENT): "customer (checkout)",
    (S.DRAFT, S.CANCELLED): "customer",
    (S.PENDING_PAYMENT, S.PAID): "Stripe webhook",
    (S.PENDING_PAYMENT, S.CANCELLED): "Stripe webhook",
    (S.PAID, S.READY_TO_SHIP): "human: create shipment",
    (S.PAID, S.REFUNDED): "Stripe webhook",
    (S.READY_TO_SHIP, S.SHIPPED): "human: parcel handed over",
    (S.READY_TO_SHIP, S.REFUNDED): "Stripe webhook",
    (S.SHIPPED, S.REFUNDED): "Stripe webhook",
}


def move(current: S, target: S) -> S:
    if target not in ALLOWED[current]:
        raise ValueError(f"{current} -> {target} is not a transition")
    return target


edges = [(a, b) for a, targets in ALLOWED.items() for b in targets]
assert set(edges) == set(ACTOR), "every edge needs an owner"
assert move(S.PAID, S.READY_TO_SHIP) is S.READY_TO_SHIP
for bad in [(S.SHIPPED, S.PAID), (S.CANCELLED, S.PAID), (S.DRAFT, S.SHIPPED)]:
    try:
        move(*bad)
    except ValueError:
        pass
    else:
        raise AssertionError(bad)

human = [e for e in edges if ACTOR[e].startswith("human")]
print(f"{len(edges)} transitions, {len(human)} need a human:")
for a, b in human:
    print(f"  {a} -> {b}: {ACTOR[(a, b)]}")
```

```bash
python3 order_flow.py
# 9 transitions, 2 need a human:
#   paid -> ready_to_ship: human: create shipment
#   ready_to_ship -> shipped: human: parcel handed over
```

Two of the nine transitions need a person, and both are about a physical box. Everything
around them is the API's job:

- **Creating the shipment** puts the order on the pick list and makes its packing slip
  available. One call to `POST /v1/admin/orders/{id}/label` writes the outbox row, and the
  worker fetches the DHL label and tracking number, then stores the label for printing.
- **Marking it shipped** sends the customer their tracking email from the same request.
  Nobody copies a tracking number by hand.
- **A refund in Stripe** moves a paid, packed or shipped order to `REFUNDED` by webhook.
- **Returns** have their own small state machine and admin endpoints, so a parcel coming
  back is a status change and not a spreadsheet row.
- **Documents** go to Paperless-ngx through the admin API, which gives you OCR and search
  over everything the tax office might ask about. The Paperless credentials never reach
  the browser.

This is also why 47 of the API's 71 operations sit under `/v1/admin`. The back office is
not an afterthought bolted onto a storefront. It is most of the product.

## Conclusion

**Automate the step that repeats, not the page that impresses.** A product page is built
once. Order handling runs for every order the shop will ever take, so that is where the
engineering effort goes furthest.

**Count hands per edge.** Writing the order lifecycle as a table of transitions, then
naming who moves each one, turns "we should automate more" into a finite list. Ours has two
entries left, and both involve tape.

**Make the unreliable parts asynchronous and idempotent.** Payment providers redeliver and
carriers time out. A deduplication table and an outbox let both happen without anyone
noticing, which is the only acceptable amount of noticing at eleven at night.

The catalogue will get better. It is simply second, and the
[next article](/posts/opentaberna-storefront-against-the-api/) explains why that matters
less than it sounds: when the API is finished, the storefront is the easy part.
