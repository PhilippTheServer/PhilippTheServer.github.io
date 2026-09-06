---
layout: post
title: "The Transactional Outbox: Never Losing a Job You Already Committed"
subtitle: "Writing the intent to enqueue in the same transaction as the state change it follows."
date: 2026-03-24 09:00:00 +0200
tags: [python, architecture, reliability, databases]
description: >-
  Committing a database change and then enqueueing a job as two separate
  steps leaves a gap in which the change is permanent but nothing ever runs
  the job and nothing notices. This works through the transactional outbox
  pattern and gives a complete, runnable Postgres, Redis Streams and Python
  implementation that survives a crash at any point in that gap.
---

## The problem

A handler that creates an order and then queues a confirmation email looks correct on
the page:

```python
# Broken. Do not copy this.
@app.post("/orders")
def create_order(body: CreateOrder):
    with SessionLocal() as session:
        order = Order(customer_email=body.customer_email, amount=body.amount)
        session.add(order)
        session.commit()

    queue.enqueue("send_confirmation_email", order_id=str(order.id))
    return {"order_id": str(order.id)}
```

The database commit and the `enqueue` call are two independent operations against two
independent systems, and there is no way to make both succeed or both fail together. If
the process is killed, the broker is briefly unreachable, or the network blips between
those two lines, the order exists — permanently, the customer was charged, the commit
already returned — and the job that was supposed to email them never gets created. No
exception is raised on the order path; nothing retries; nothing even records that
anything was supposed to happen. The only trace is a customer who never got an email and
no error anywhere to explain why.

Reordering doesn't fix it, it just moves the gap: enqueue first and commit second, and a
worker can now pick up and run the job for an order the database transaction later rolls
back — sending a confirmation for a purchase that, as far as the database is concerned,
never happened. Wrapping the enqueue call in a retry loop doesn't fix it either, because
a retry loop protects against the broker call failing; it does nothing for the process
being killed between the two lines, which is the case that actually happens in
production, during a deploy or an OOM kill, at the worst possible moment.

This is a two-phase-commit problem in miniature: two systems, one all-or-nothing
guarantee wanted, no distributed transaction coordinator in play. It is also easy to
miss in testing, because a local run with a warm broker and no crashes never exercises
the gap at all.

## Working through it

### Turn two systems into one

The gap exists because the state change (the order) and the delivery intent (the job)
live in different systems that commit independently. The fix is to stop treating them as
different systems: write the delivery intent as a row in the *same* database, in the
*same* transaction as the state change. An `outbox_events` table, written by the same
`session.commit()` that writes the order, is atomic with the order by construction —
Postgres already guarantees that a transaction either commits both rows or neither.

```python
order = Order(customer_email=body.customer_email, amount=body.amount)
session.add(order)
session.flush()

event = OutboxEvent(
    event_type="order_created",
    payload=json.dumps({"order_id": str(order.id), ...}),
)
session.add(event)
session.commit()
```

There is no longer a gap between "the order exists" and "the intent to notify someone is
recorded", because both facts are the same commit.

### A relay moves intent into a real queue, asynchronously

The outbox row isn't a job a worker can consume directly — it's a durable record that a
job *should* be created. A separate, simple process — the relay — polls the table for
undispatched rows and publishes them to the real broker:

```python
rows = (
    session.query(OutboxEvent)
    .filter(OutboxEvent.dispatched_at.is_(None))
    .order_by(OutboxEvent.created_at)
    .limit(20)
    .with_for_update(skip_locked=True)
    .all()
)
```

`FOR UPDATE SKIP LOCKED` matters the moment you run more than one relay instance: two
relays polling concurrently would otherwise both read the same undispatched row and both
publish it. `SKIP LOCKED` makes each relay instance take a disjoint batch instead of
blocking on the other's lock, so scaling the relay out is safe by default rather than by
careful configuration.

If the relay process dies mid-batch, nothing is lost: the rows it hadn't yet marked
`dispatched_at` are still sitting in Postgres, unchanged, waiting for the next poll —
by this process or another one. This is the property the pattern is named for: the
outbox is durable, so a crash anywhere between "the state changed" and "the job ran" is
recoverable, because the one fact that must never disappear — that the job was owed —
lives in the same transaction as the state change itself.

### At-least-once delivery means the consumer must be idempotent

Moving the reliability problem into Postgres doesn't make it disappear on the broker
side. A relay that marks a row dispatched only *after* a successful publish, and a
consumer that acknowledges only *after* successful processing, together give
at-least-once delivery — not exactly-once. A relay that publishes and then crashes
before recording `dispatched_at` will republish the same event on restart. A worker that
processes a job and crashes before acknowledging will see it redelivered.

The honest trade-off is that duplicate delivery is not eliminated, only made safe: the
consumer has to be idempotent. Redis Streams make this practical because every message
carries a durable ID and unacknowledged messages remain claimable, but the deduplication
itself has to happen in application logic — a `processed_events` table keyed by event ID,
written with `ON CONFLICT DO NOTHING` before the side effect runs, so a redelivered event
is a no-op rather than a second email.

### The outbox table needs a cleanup policy

An outbox table that only grows is a slow leak, not a bug that shows up on day one. Once
a row's event is dispatched and acknowledged, nothing needs it — a scheduled job deleting
rows with `dispatched_at` older than a few days keeps the table small enough that the
relay's poll query stays fast indefinitely.

## The solution

```
.
├── docker-compose.yml
├── requirements.txt
├── db.py
├── models.py
├── app.py
├── relay.py
└── worker.py
```

```
# requirements.txt
fastapi==0.115.0
uvicorn==0.30.6
sqlalchemy==2.0.35
psycopg[binary]==3.2.1
pydantic==2.9.2
redis==5.0.8
```

```python
# db.py
import os

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

engine = create_engine(os.environ["DATABASE_URL"], future=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)
Base = declarative_base()
```

```python
# models.py
import uuid
from datetime import datetime

from sqlalchemy import Column, DateTime, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID

from db import Base


class Order(Base):
    __tablename__ = "orders"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    customer_email = Column(String, nullable=False)
    amount = Column(Numeric(10, 2), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class OutboxEvent(Base):
    __tablename__ = "outbox_events"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_type = Column(String, nullable=False)
    payload = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    dispatched_at = Column(DateTime, nullable=True)


class ProcessedEvent(Base):
    __tablename__ = "processed_events"
    event_id = Column(String, primary_key=True)
    processed_at = Column(DateTime, default=datetime.utcnow)
```

```python
# app.py
import json
from decimal import Decimal

from fastapi import FastAPI
from pydantic import BaseModel

from db import Base, SessionLocal, engine
from models import Order, OutboxEvent

Base.metadata.create_all(engine)
app = FastAPI()


class CreateOrder(BaseModel):
    customer_email: str
    amount: Decimal


@app.post("/orders")
def create_order(body: CreateOrder):
    with SessionLocal() as session:
        order = Order(customer_email=body.customer_email, amount=body.amount)
        session.add(order)
        session.flush()

        event = OutboxEvent(
            event_type="order_created",
            payload=json.dumps(
                {
                    "order_id": str(order.id),
                    "customer_email": order.customer_email,
                    "amount": str(order.amount),
                }
            ),
        )
        session.add(event)
        session.commit()
        order_id = str(order.id)

    return {"order_id": order_id}
```

```python
# relay.py
import time
from datetime import datetime

import redis

from db import SessionLocal
from models import OutboxEvent

STREAM_KEY = "jobs"
BATCH_SIZE = 20
POLL_INTERVAL_SECONDS = 0.5

r = redis.Redis(host="redis", port=6379, decode_responses=True)


def relay_once():
    with SessionLocal() as session:
        rows = (
            session.query(OutboxEvent)
            .filter(OutboxEvent.dispatched_at.is_(None))
            .order_by(OutboxEvent.created_at)
            .limit(BATCH_SIZE)
            .with_for_update(skip_locked=True)
            .all()
        )
        for event in rows:
            r.xadd(
                STREAM_KEY,
                {
                    "event_id": str(event.id),
                    "event_type": event.event_type,
                    "payload": event.payload,
                },
            )
            event.dispatched_at = datetime.utcnow()
        session.commit()
        return len(rows)


def main():
    print("relay started, polling outbox table", flush=True)
    while True:
        if relay_once() == 0:
            time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
```

```python
# worker.py
import json

import redis
from sqlalchemy.dialects.postgresql import insert as pg_insert

from db import SessionLocal
from models import ProcessedEvent

STREAM_KEY = "jobs"
GROUP = "workers"
CONSUMER = "worker-1"

r = redis.Redis(host="redis", port=6379, decode_responses=True)

try:
    r.xgroup_create(STREAM_KEY, GROUP, id="0", mkstream=True)
except redis.ResponseError as exc:
    if "BUSYGROUP" not in str(exc):
        raise


def handle(event_id, fields):
    with SessionLocal() as session:
        stmt = pg_insert(ProcessedEvent).values(event_id=event_id).on_conflict_do_nothing()
        result = session.execute(stmt)
        if result.rowcount == 0:
            print(f"event {event_id} already processed, skipping", flush=True)
            session.commit()
            return
        payload = json.loads(fields["payload"])
        print(
            f"sending confirmation email for order {payload['order_id']} "
            f"to {payload['customer_email']}",
            flush=True,
        )
        session.commit()


def main():
    print("worker started, reading from stream", flush=True)
    while True:
        resp = r.xreadgroup(GROUP, CONSUMER, {STREAM_KEY: ">"}, count=10, block=5000)
        if not resp:
            continue
        for _, messages in resp:
            for msg_id, fields in messages:
                handle(fields["event_id"], fields)
                r.xack(STREAM_KEY, GROUP, msg_id)


if __name__ == "__main__":
    main()
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
      retries: 15

  redis:
    image: redis:7
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 2s
      retries: 15

  api:
    build: .
    command: ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
    environment:
      DATABASE_URL: postgresql://app:app@postgres:5432/app
    depends_on:
      postgres:
        condition: service_healthy
    ports: ["8000:8000"]

  relay:
    build: .
    command: ["python", "relay.py"]
    environment:
      DATABASE_URL: postgresql://app:app@postgres:5432/app
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy

  worker:
    build: .
    command: ["python", "worker.py"]
    environment:
      DATABASE_URL: postgresql://app:app@postgres:5432/app
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
```

```dockerfile
# Dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
```

Proving it works, including the case that matters — the relay being down when the order
is placed:

```bash
docker compose up --build -d postgres redis api worker
docker compose stop relay 2>/dev/null || true  # relay never started yet

curl -s -X POST localhost:8000/orders \
  -H 'content-type: application/json' \
  -d '{"customer_email": "reader@example.com", "amount": "19.99"}'
# {"order_id":"..."}

docker compose logs worker --tail 5
# (nothing yet — no relay running, so the event is sitting in outbox_events)

docker compose up -d relay
docker compose logs -f worker
```

Correct output, appearing after the relay starts, however long that was delayed by:

```
worker-1  | sending confirmation email for order <order_id> to reader@example.com
```

The order was committed the moment the `curl` returned; the email was sent only once the
relay came up, however much later that was — and nothing about the order record itself
had to be touched to make that happen, because the intent to send it was already durable.

## Conclusion

The failure this pattern removes is not "the queue is unreliable" — it's that a state
change and a message about that state change were never atomic in the first place. Two
independent commits can't be made into one by retrying either half harder.

**Reduce a two-system atomicity problem to a one-system one.** The outbox row and the
state it describes commit together because they are one transaction in one database;
everything after that point is an asynchronous delivery problem, which is a much easier
class of problem to retry your way out of.

**At-least-once is the honest ceiling, so design the consumer for duplicates rather than
pretending they won't happen.** A `processed_events` table with a unique key and an
`ON CONFLICT DO NOTHING` insert before the side effect is a few lines, and it's the
difference between "redelivered" and "double-charged".

**This costs a table, a relay process and an operational habit of watching outbox
size.** It is not free, and for a job that's tolerable to lose occasionally — a
best-effort analytics ping, say — it is very likely more machinery than the job is worth.
It earns its keep specifically where losing the job silently, while the state change it
was tied to survives, is the outcome you cannot accept.
</content>
