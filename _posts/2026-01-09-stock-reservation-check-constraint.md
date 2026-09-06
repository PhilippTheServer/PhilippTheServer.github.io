---
layout: post
title: "Preventing Overselling with a Database CHECK Constraint"
subtitle: "A row lock and a CHECK constraint are enough to stop two buyers taking the last unit."
date: 2026-01-09 09:00:00 +0200
tags: [databases, performance]
description: >-
  Code that reads the remaining stock, decides there is enough, and then
  writes the new total is a race that two concurrent customers can both win,
  each believing they got the last unit. This article works through why
  check-then-decrement logic fails under a database's default isolation
  level, and builds a schema where a single atomic UPDATE together with a
  CHECK constraint makes overselling structurally impossible rather than
  merely unlikely, with a concurrency test to prove it.
---

## The problem

An online shop has one unit left of an item. Two customers click "buy" within the same
second. Somewhere in the checkout code there is logic that looks roughly like this:

```sql
-- Application code, roughly, as three separate round trips:
-- 1. Read the current stock
SELECT stock FROM products WHERE id = 1;

-- 2. In the application: if stock >= quantity requested, proceed

-- 3. Write the new stock
UPDATE products SET stock = stock - 1 WHERE id = 1;
```

Each of those is a separate statement, and nothing stops another connection from running
its own step 1 between this connection's step 1 and step 3. Run it once, by hand, and it
works: read 1, check `1 >= 1`, write 0. Run it under real concurrency and it does not
reliably work, because "read the stock" and "write the new stock" are not one operation —
they are two, with an arbitrary amount of application logic in between: fraud checks,
payment authorisation, building an order record. Any of that gives a second request time
to run its own read against the same, still-unchanged, row.

The result is two confirmed orders against one unit of stock. Nothing in the code above
raised an error, because nothing in it, or in the schema, said stock could not go
negative. This is easy to miss in testing, because the failure needs two requests to land
inside the same short window, which mostly does not happen with a single tester clicking
a button by hand. It shows up in production, under real traffic, and by the time anyone
notices, the shop owes an apology to whichever customer arrives second in the fulfilment
queue.

The fix is not "add more validation in the application". It is to stop treating the check
and the decrement as two decisions, and make them one.

## Working through it

### Why the interleaving happens even inside a transaction

Wrapping the three steps in `BEGIN` / `COMMIT` looks like it should fix this — a
transaction is supposed to be the unit of atomicity. It does not, for a specific reason:
PostgreSQL's default isolation level, `READ COMMITTED`, gives each *statement* inside a
transaction its own snapshot of committed data, not the whole transaction. A `SELECT`
early in the transaction and an `UPDATE` later in the same transaction can legitimately
see two different states of the row, because a `COMMIT` from somewhere else can land in
between.

Two sessions, interleaved:

| Session A | Session B |
|---|---|
| `BEGIN;` | |
| `SELECT stock ...` returns 1 | |
| | `BEGIN;` |
| | `SELECT stock ...` returns 1 |
| checks `1 >= 1`, decides to sell | checks `1 >= 1`, decides to sell |
| `UPDATE ... SET stock = stock - 1;` | |
| `COMMIT;` — stock is now 0 | |
| | `UPDATE ... SET stock = stock - 1;` |
| | `COMMIT;` — stock is now -1 |

Both sessions read the same value because neither had committed yet when the other read.
Both decided, independently and correctly given what they saw, that the sale was fine.
The transaction boundary did nothing here, because the decision was made on data the
transaction never protected from being read, and possibly acted on, by someone else.

### Isolation levels change where it breaks, not whether it breaks

Raising the isolation level to `REPEATABLE READ` or `SERIALIZABLE` does not sidestep this
so much as move the failure to a place where the database can tell you about it. Under
either of those, the second session's `UPDATE` would fail at commit time with a
serialisation error, because PostgreSQL detects that the transaction acted on data another
transaction has since changed. That is a real improvement — nothing is silently
oversold — but it comes with a cost the application has to carry: every write path that
touches contended rows now needs a retry loop for serialisation failures, and those
failures become more frequent as concurrency on the same row increases, which is exactly
when you can least afford wasted work.

`SELECT ... FOR UPDATE` is the other classic answer: take a row lock on read, so a second
`SELECT ... FOR UPDATE` against the same row blocks until the first transaction commits or
rolls back, then reads the value the first transaction left behind. This does prevent the
race, correctly used. The problem is "correctly used" has to hold on every code path that
touches the row, forever — an admin script, a bulk import job, or a later refactor that
adds a second way to adjust stock only has to skip the `FOR UPDATE` once to reopen the
hole, and nothing in the schema will tell you it happened.

### Making the decision and the write the same operation

The more direct fix is to stop asking "is there enough stock" and then separately telling
the database to decrement it. Ask the database to decrement it *only if* there is enough
stock, as a single statement:

```sql
UPDATE products
SET stock = stock - :qty
WHERE id = :id AND stock >= :qty;
```

This is atomic for a concrete, mechanical reason: PostgreSQL takes a row-level lock for
the duration of an `UPDATE`. A second, concurrent `UPDATE` against the same row does not
race the first one — it waits for the first to finish, and then evaluates its own `WHERE`
clause against whatever the first transaction left behind. Under plain `READ COMMITTED`,
with no elevated isolation level and no explicit locking clause, the two updates are
serialised by the row lock alone. Whichever one commits first sees the original stock;
whichever one runs second sees the already-decremented value, and its `WHERE` clause
either still matches or it does not.

The row count the `UPDATE` reports is now the answer to "did the sale succeed": one row
changed means yes, zero rows changed means no. That is a plain number the application
checks, not an exception it has to catch.

### A CHECK constraint as the backstop, not the mechanism

The atomic `UPDATE` is the mechanism that makes the *correct* code path safe. It does
nothing for a careless one. A manual fix run by hand, a bulk import, or an admin panel
that updates stock without the `WHERE stock >= qty` clause can still push the value
negative, and nothing about the atomic `UPDATE` pattern stops a different piece of code
from ignoring it.

A `CHECK` constraint closes that gap at the level where it cannot be skipped:

```sql
CONSTRAINT stock_not_negative CHECK (stock >= 0)
```

This is enforced by PostgreSQL itself, on every statement that touches the row, regardless
of which code path wrote it. The atomic `UPDATE` and the constraint are doing different
jobs. The `UPDATE ... WHERE` picks the correct outcome when two legitimate requests
compete for the same stock. The `CHECK` constraint guarantees the invariant — stock never
goes negative — even when something did not use the safe pattern at all. Neither one
replaces the other.

## The solution

### Schema

```sql
-- schema.sql
CREATE TABLE products (
    id    bigint PRIMARY KEY,
    name  text NOT NULL,
    stock integer NOT NULL,
    CONSTRAINT stock_not_negative CHECK (stock >= 0)
);

INSERT INTO products (id, name, stock) VALUES (1, 'Last unit widget', 1);

-- The naive pattern from "The problem", kept here to demonstrate why it is
-- unsafe even with the constraint in place. Do not copy this into an
-- application.
CREATE OR REPLACE FUNCTION buy_naive(p_id bigint, p_qty integer)
RETURNS void
LANGUAGE plpgsql
AS $$
DECLARE
    v_stock integer;
BEGIN
    SELECT stock INTO v_stock FROM products WHERE id = p_id;

    -- Stands in for the application work that happens between reading the
    -- stock and writing it back: fraud checks, payment capture, building
    -- the order record. Widens the race window so it reproduces reliably
    -- on a single laptop instead of depending on timing luck.
    PERFORM pg_sleep(1);

    IF v_stock < p_qty THEN
        RAISE EXCEPTION 'insufficient stock for product %', p_id;
    END IF;

    UPDATE products SET stock = stock - p_qty WHERE id = p_id;
END;
$$;

-- The safe pattern: the check and the write are one statement.
CREATE OR REPLACE FUNCTION buy_atomic(p_id bigint, p_qty integer)
RETURNS boolean
LANGUAGE plpgsql
AS $$
DECLARE
    v_rows integer;
BEGIN
    UPDATE products
    SET stock = stock - p_qty
    WHERE id = p_id AND stock >= p_qty;

    GET DIAGNOSTICS v_rows = ROW_COUNT;
    RETURN v_rows = 1;
END;
$$;
```

Start a throwaway instance and load it:

```bash
docker run -d --name stock-demo -e POSTGRES_PASSWORD=postgres -p 5432:5432 postgres:16
sleep 3
DB="postgresql://postgres:postgres@localhost:5432/postgres"
psql "$DB" -f schema.sql
```

### Proving the naive version fails badly, not safely

Reset stock to one unit, then fire two concurrent calls to `buy_naive`:

```bash
psql "$DB" -c "UPDATE products SET stock = 1 WHERE id = 1;"

psql "$DB" -c "SELECT buy_naive(1, 1);" &
psql "$DB" -c "SELECT buy_naive(1, 1);" &
wait
```

Both calls read `stock = 1` before either commits, because neither has written yet — the
`pg_sleep(1)` guarantees the overlap. Both pass their `IF v_stock < p_qty` check. The
first `UPDATE` then commits, taking stock from 1 to 0. The second `UPDATE` was blocked on
the row lock; once it runs, it computes `0 - 1` and the `CHECK` constraint rejects it:

```
ERROR:  new row for relation "products" violates check constraint "stock_not_negative"
DETAIL:  Failing row contains (1, Last unit widget, -1).
```

The constraint did its job — stock never actually reached -1 in the table. But the
pattern is still the wrong one. The second customer's request ran the full one-second
simulated checkout — the fraud check, the payment capture, the order-record work the
`pg_sleep` stands in for — and only failed at the very last statement, as a database
exception the application now has to catch and unwind, including any external side
effects it already started. The constraint prevented data corruption; it did not prevent
the underlying design mistake of deciding "yes" before checking whether the write would
actually succeed.

### Proving the atomic version holds under concurrency

Reset stock again, then fire two concurrent calls to `buy_atomic`:

```bash
psql "$DB" -c "UPDATE products SET stock = 1 WHERE id = 1;"

psql "$DB" -tAc "SELECT buy_atomic(1, 1);" &
psql "$DB" -tAc "SELECT buy_atomic(1, 1);" &
wait

psql "$DB" -c "SELECT stock FROM products WHERE id = 1;"
```

One call prints `t`, the other prints `f`, and the final query shows `stock = 0`. No
exception, no rollback, no `pg_sleep` needed to demonstrate it — the decision and the
write happen as one statement, so there is no window in which both callers can believe
they succeeded.

A wider stress test makes the same point at higher concurrency, which is the case that
matters:

```bash
psql "$DB" -c "UPDATE products SET stock = 1 WHERE id = 1;"

for i in $(seq 1 20); do
  psql "$DB" -tAc "SELECT buy_atomic(1, 1);" &
done
wait

echo "Successful buys:"
psql "$DB" -tAc "SELECT stock FROM products WHERE id = 1;"
```

Twenty concurrent callers compete for one unit of stock. Exactly one of them can ever
receive `t`, because each `UPDATE` acquires the row lock in turn and re-evaluates
`stock >= 1` against whatever the previous holder left behind. The final `stock` value is
always 0, never negative, regardless of how many callers you add — the number of
concurrent callers changes how much they queue behind the row lock, not whether the
outcome is correct.

## Conclusion

The mistake in the original code was never the arithmetic. `stock - 1` is not wrong. The
mistake was splitting "decide whether to sell" and "record the sale" into two statements
and trusting that nothing would happen in between.

A few points generalise beyond stock counters:

**An invariant that must always hold belongs in the schema, not only in application
code.** `CHECK (stock >= 0)` costs one line and turns "a code path forgot to validate
this" from a silent data-integrity bug into an error PostgreSQL raises on your behalf,
from every code path, including the ones you have not written yet.

**When a decision and a write must be consistent with each other, make them one
statement.** `UPDATE ... WHERE stock >= qty` is not a style preference over
`SELECT` then `UPDATE` — it changes what the database can guarantee, using nothing more
than the row lock every `UPDATE` already takes, at the default isolation level, with no
retry loop required.

**A failed row count is not an exceptional case; it is the normal shape of "no".** The
atomic pattern makes rejection a value the caller checks, rather than an exception raised
after work has already been done that now has to be undone.

**This solves correctness, not availability.** A `CHECK` constraint and an atomic
`UPDATE` do not implement a reservation queue — there is no notion of "held for the next
five minutes while payment completes", and no partial-fulfilment or backorder logic; a
request for more than is available simply fails outright. Under very high contention for
a single row, every writer still queues behind the same row lock, so this protects
correctness under load without improving how much load one row can absorb — if that
becomes the bottleneck, it is a capacity problem to solve separately, for example by
batching demand or partitioning stock across rows, not a reason to go back to
check-then-decrement.
