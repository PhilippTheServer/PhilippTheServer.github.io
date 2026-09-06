---
layout: post
title: "Idempotent Payment Webhooks with a Deduplication Table"
subtitle: "Making redelivery safe instead of trying to prevent it."
date: 2026-01-06 09:00:00 +0200
tags: [python, reliability, api-design]
description: >-
  Payment providers deliver webhooks at least once and retry on anything but a
  2xx response, so a handler that is not explicitly idempotent will eventually
  process the same payment twice. This covers signature verification, a
  deduplication table with a unique constraint, and a transactional handler
  that makes redelivery a no-op, with a complete example and a test that
  proves it.
---

## The problem

A payment provider's webhook contract is usually stated plainly in its documentation and
ignored in the first version of most integrations: events are delivered **at least once**,
and any response that is not a 2xx is treated as failure and retried, on a schedule, for
some providers over several days.

A handler that looks correct in isolation breaks under this contract:

```python
# Naive. Will double-process on redelivery.
@app.route("/webhooks/payments", methods=["POST"])
def handle_webhook():
    event = request.get_json()
    if event["type"] == "payment.succeeded":
        order_id = event["data"]["order_id"]
        mark_order_paid(order_id)
        send_confirmation_email(order_id)
    return "", 200
```

This works the first time. It also works, identically, the second time the same event
arrives — and it will arrive a second time, because "at least once" is not a rare edge
case, it is the documented normal behaviour. A slow database write that makes the response
take eleven seconds, a load balancer that times a connection out at ten, a brief network
blip on the provider's side: any of these is enough to make the provider believe the first
delivery failed and try again, even though `mark_order_paid` already ran to completion.

The result is not a crash, which would at least be visible. It is a customer who gets a
confirmation email twice, or — worse, if the handler also credits a wallet or triggers a
payout — a payment processed twice for the same event. This is easy to miss in testing
because a local test harness delivers each webhook exactly once, and easy to miss in
production because a webhook is by design an asynchronous message with no caller waiting
on the specific consequence of a duplicate — nobody is watching the response the way they
would watch a synchronous API call.

## Working through it

### Verify before trusting anything in the payload

Before deduplication, the handler has to be sure the request came from the provider at
all. Providers sign the raw request body with a shared secret; the correct check compares
against the raw bytes, not against a re-serialised version of the parsed JSON, because
re-serialisation can change whitespace and byte order in ways that invalidate the
signature even for a genuine request.

```python
import hashlib
import hmac


def verify_signature(payload: bytes, signature_header: str, secret: str) -> bool:
    expected = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header)
```

`hmac.compare_digest` matters here specifically because it runs in constant time; a
naive `==` comparison leaks timing information about how many leading bytes matched,
which is a real attack surface for anything comparing secrets or signatures.

### Deduplicate on the event's own identity, not on your own logic

The natural instinct is to make the business logic itself idempotent — check if the order
is already marked paid before marking it paid again. That works for this one field and
breaks down the moment the handler does two things, because the second delivery can
re-run the side effect (the email) even though the guard on the first (the order status)
correctly skipped it.

The provider gives every event a unique ID for exactly this reason. Deduplicating on that
ID, once, before any business logic runs, means the guard covers everything the handler
does, not just whichever field someone remembered to check.

```sql
CREATE TABLE processed_webhook_events (
    event_id   TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    received_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
```

The primary key is what does the work. Two concurrent redeliveries both trying to insert
the same `event_id` is a race, and the database — not the application — is the right place
to resolve it: exactly one insert succeeds, the other fails a uniqueness constraint, and
that failure is the "already processed" signal.

### Make the insert and the side effect one transaction

If the deduplication insert and `mark_order_paid` are two separate statements, there is a
window between them where a crash leaves the event marked processed without the order
actually being marked paid — a duplicate is now prevented from ever being retried
correctly. The fix is to make the record-of-processing and the effect happen in the same
database transaction, so either both happen or neither does.

```python
def process_event(conn, event: dict) -> bool:
    """Returns True if this call actually processed the event."""
    with conn:  # sqlite3: commits on success, rolls back on exception
        try:
            conn.execute(
                "INSERT INTO processed_webhook_events (event_id, event_type) VALUES (?, ?)",
                (event["id"], event["type"]),
            )
        except sqlite3.IntegrityError:
            return False  # already processed; this is the redelivery case

        if event["type"] == "payment.succeeded":
            order_id = event["data"]["order_id"]
            conn.execute(
                "UPDATE orders SET status = 'paid' WHERE id = ?", (order_id,)
            )
        return True
```

The email send is deliberately kept outside this transaction in the example below — a
transaction should not hold open across a network call to a third-party mail service, and
`process_event` returning `True` exactly once is what makes sending the email exactly once
safe, regardless of when it happens relative to the commit.

### Respond 2xx only once you have durably recorded the attempt

The insert must complete and commit *before* the handler returns 200. If the response is
sent first and the insert happens afterwards in a background task, a redelivery that
arrives in that window sees no record yet and processes the event a second time — the
exact failure this design exists to prevent, just moved a few milliseconds later.

## The solution

A complete, runnable Flask application, using SQLite so it needs nothing beyond Python
itself, plus a test proving a duplicate event is only processed once.

```python
# app.py
import hashlib
import hmac
import sqlite3

from flask import Flask, request

WEBHOOK_SECRET = "whsec_replace_with_your_own_secret"  # placeholder — do not hardcode in real code
DB_PATH = "orders.db"

app = Flask(__name__)


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS processed_webhook_events (
            event_id TEXT PRIMARY KEY,
            event_type TEXT NOT NULL,
            received_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS orders (
            id TEXT PRIMARY KEY,
            status TEXT NOT NULL DEFAULT 'pending'
        )"""
    )
    return conn


def verify_signature(payload: bytes, signature_header: str, secret: str) -> bool:
    expected = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header or "")


def process_event(conn: sqlite3.Connection, event: dict) -> bool:
    with conn:
        try:
            conn.execute(
                "INSERT INTO processed_webhook_events (event_id, event_type) VALUES (?, ?)",
                (event["id"], event["type"]),
            )
        except sqlite3.IntegrityError:
            return False

        if event["type"] == "payment.succeeded":
            order_id = event["data"]["order_id"]
            conn.execute(
                "INSERT INTO orders (id, status) VALUES (?, 'paid') "
                "ON CONFLICT(id) DO UPDATE SET status = 'paid'",
                (order_id,),
            )
        return True


@app.route("/webhooks/payments", methods=["POST"])
def handle_webhook():
    raw_body = request.get_data()
    signature = request.headers.get("X-Webhook-Signature", "")

    if not verify_signature(raw_body, signature, WEBHOOK_SECRET):
        return {"error": "invalid signature"}, 400

    event = request.get_json()
    conn = get_conn()
    try:
        was_processed_now = process_event(conn, event)
    finally:
        conn.close()

    if was_processed_now:
        print(f"processed {event['id']} ({event['type']}) — side effects run once")
    else:
        print(f"ignored duplicate delivery of {event['id']}")

    return {"received": True}, 200


if __name__ == "__main__":
    app.run(port=5000)
```

```bash
pip install flask==3.0.2
python app.py
```

Sign and send the same event twice, exactly as a provider retry would:

```bash
python3 - <<'PY'
import hashlib, hmac, json

secret = "whsec_replace_with_your_own_secret"
body = json.dumps({
    "id": "evt_1",
    "type": "payment.succeeded",
    "data": {"order_id": "ord_100"},
}).encode()
sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
print(body.decode())
print(sig)
PY
```

```bash
BODY='{"id": "evt_1", "type": "payment.succeeded", "data": {"order_id": "ord_100"}}'
SIG="<paste the signature printed above>"

curl -s -X POST localhost:5000/webhooks/payments \
  -H "Content-Type: application/json" -H "X-Webhook-Signature: $SIG" -d "$BODY"
curl -s -X POST localhost:5000/webhooks/payments \
  -H "Content-Type: application/json" -H "X-Webhook-Signature: $SIG" -d "$BODY"
```

Server output — the second call is accepted (still 200, so the provider stops retrying)
but does no work:

```
processed evt_1 (payment.succeeded) — side effects run once
ignored duplicate delivery of evt_1
```

```python
# test_app.py
import hashlib
import hmac
import json
import sqlite3

import pytest

from app import get_conn, process_event, verify_signature

SECRET = "whsec_replace_with_your_own_secret"


@pytest.fixture()
def conn(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setattr("app.DB_PATH", str(db_path))
    c = get_conn()
    yield c
    c.close()


def test_signature_rejects_tampered_payload():
    body = b'{"id": "evt_1"}'
    sig = hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    assert verify_signature(body, sig, SECRET)
    assert not verify_signature(b'{"id": "evt_2"}', sig, SECRET)


def test_duplicate_event_processed_exactly_once(conn):
    event = {"id": "evt_1", "type": "payment.succeeded", "data": {"order_id": "ord_1"}}

    first = process_event(conn, event)
    second = process_event(conn, event)

    assert first is True
    assert second is False

    row = conn.execute("SELECT status FROM orders WHERE id = ?", ("ord_1",)).fetchone()
    assert row == ("paid",)

    count = conn.execute(
        "SELECT COUNT(*) FROM processed_webhook_events WHERE event_id = ?", ("evt_1",)
    ).fetchone()[0]
    assert count == 1
```

```bash
pip install pytest==8.0.0
pytest test_app.py -v
# 2 passed in 0.02s
```

## Conclusion

Idempotency here is not a defensive afterthought bolted onto the handler; it is the actual
contract the provider is asking the integration to honour, since "at least once" is stated
up front as the delivery guarantee, not a fault condition.

**Deduplicate on the provider's event ID, before any business logic, not on your own
derived state.** Checking "is this order already paid?" only guards that one field; a
separate identity-based guard covers every side effect the handler has, including the ones
added later.

**Let the database enforce the uniqueness, not the application.** A unique constraint
resolves a race between two concurrent redeliveries correctly by construction; an
application-level check-then-insert has a gap that a database constraint does not.

**The record of processing and the effect must commit together.** Split across two
transactions, a crash between them creates exactly the inconsistent state idempotency was
supposed to prevent.

**This generalises to any at-least-once delivery system** — SQS, Pub/Sub, Kafka with
manual offset commits, retried background jobs — not just payment webhooks. Anywhere a
message can be delivered more than once, the fix is the same shape: an identity, a unique
constraint, and one transaction around both the record and the effect.
