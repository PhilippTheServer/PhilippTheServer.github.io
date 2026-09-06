---
layout: post
title: "UUID Case Normalization as a Recurring Cross-Service Bug"
subtitle: "One identifier, cased two different ways by two services, silently becomes two."
date: 2025-12-19 09:00:00 +0200
tags: [reliability, architecture, python, testing]
description: >-
  An uppercase UUID from one service and a lowercase one from another look
  identical to a person and different to a dict, a cache, or a database
  index, which turns one entity into two without raising an error. Here is
  a minimal reproduction of the bug and a boundary-normalisation fix with a
  test that keeps it fixed.
---

## The problem

An uppercase UUID from one service and a lowercase one from another create two keys
instead of one, silently.

```python
import uuid

cache = {}

def create_order():
    order_id = str(uuid.uuid4())          # lower-case, e.g. '3fa85f64-5717-...'
    cache[f"order:{order_id}"] = {"status": "new"}
    return order_id

order_id = create_order()

# A downstream system receives the same order id from an upstream feed that
# renders UUIDs upper-case — common with SQL Server UNIQUEIDENTIFIER columns,
# some .NET clients, and plenty of hand-rolled ID generators.
warehouse_order_id = order_id.upper()

print(cache.get(f"order:{warehouse_order_id}"))   # None — "order not found"
```

Nothing here throws. Nothing logs an error. The lookup simply misses, and whatever
called it now has to decide what a missing order means: retry, alert, or — the
genuinely dangerous option — create a new order, because as far as that code path is
concerned, none exists yet.

RFC 4122 defines a UUID as 128 bits of data; the hyphenated 36-character string is only
one way to render those bits, and the RFC explicitly says comparisons should be
case-insensitive. Almost nothing that actually stores or looks up a UUID honours that.
A Python `dict` compares strings byte-for-byte. So does a Redis key. So does a
`VARCHAR` column in Postgres unless you go out of your way to add a case-insensitive
collation or index. The specification says case doesn't matter; every general-purpose
key-value structure disagrees.

This is what makes the bug recurring rather than a one-off typo: it isn't caused by one
piece of wrong code, it's caused by a mismatch between what a UUID conceptually *is*
and how the storage layer treats the string that represents it. It reappears every time
a new pair of systems that disagree on casing meets a shared key. A Python service
using `uuid.uuid4()` talking to a .NET service using `Guid.ToString()`. A message queue
carrying an ID that passed through a database function like SQL Server's `NEWID()`,
whose default string rendering is upper-case. A partner API that upper-cases IDs in its
webhook payloads for no documented reason. Each pairing is a coincidence; the fact that
it keeps happening is not.

It's also close to unfalsifiable in isolation. A unit test for `create_order` alone
never sees another casing convention, so it passes. Integration tests that mock the
downstream service usually mock it with an ID the test itself generated, so the two
IDs are byte-identical by construction. The bug needs two independently-cased
producers of the *same* logical ID actually meeting in the same store before it shows
up — which in practice means it shows up in whichever environment first has real
traffic from both sides, often long after both services individually shipped.

## Working through it

### Why it isn't caught by normal testing

The failure mode is a false negative, not an exception. A lookup that should hit
returns nothing, and "nothing found" is a state every cache and every database
legitimately produces for unrelated reasons — cold cache, expired key, record not yet
created, race condition. There is no distinct error to catch, so there is no stack
trace pointing at the real cause. Whoever investigates first typically checks whether
the record was created at all, not whether it was created under a different-cased key,
because the two IDs look the same to a person reading them in a log.

### Where the mismatch actually comes from

It is tempting to blame ".NET" or "SQL Server" specifically, and that's too narrow.
Case conventions for UUID string rendering are just uncoordinated defaults:

- `str(uuid.uuid4())` in Python — lower-case.
- `Guid.ToString()` in .NET — lower-case by default, but upper-case is one format
  specifier away (`ToString("D").ToUpper()`), and plenty of existing code does exactly
  that for display purposes and then persists the display value.
- `NEWID()` cast to a string in SQL Server, and `UNIQUEIDENTIFIER` columns rendered by
  many database tools — upper-case.
- A frontend that reads an ID from a URL, from a QR code, or from something a human
  typed — any case, because humans don't preserve it reliably.

None of these are wrong on their own. The bug is not "someone chose the wrong case";
it's that the system has more than one place minting or reformatting the same class of
identifier, and nothing declares which case is canonical.

### Normalising at the boundary, not scattered through the codebase

The fix that doesn't work is "remember to `.lower()` it" at every call site that
touches an ID. That's not a fix, it's a maintenance obligation with no enforcement,
and it fails the same way `lineinfile` regexes fail against config files: it works
until the one place someone forgot.

The fix that does work is a single function that every ID crosses on the way in —
when it's created, when it arrives over HTTP, when it's read back from a queue message
or a database row from a system you don't control — and never again after that. Pick
one canonical form, validate against it, and store nothing else.

### Choosing the canonical form

`uuid.UUID(value)` parses upper-case, lower-case, mixed-case, braced, and
hyphen-free variants — anything that is structurally a valid UUID — and Python's
`uuid` module always renders a `UUID` object back to a string in lower-case canonical
form (`8-4-4-4-12`, hyphenated, no braces). That makes `str(uuid.UUID(value))` both a
normaliser and a validator in one call: garbage input raises `ValueError` instead of
being silently accepted as a new, different-looking key.

### Making the fix impossible to quietly undo

A boundary function only holds as an invariant if something checks that it's still
being used. Code review catches the first regression; it won't catch the fifth, six
months later, written by someone who never read this article. That's what the test in
the next section is for — it doesn't just prove the fix works, it fails loudly the day
someone reintroduces a raw dictionary keyed on the unnormalised string.

## The solution

Three files: the boundary function and store, a small script that reproduces the
original bug and shows it fixed, and a pytest file that keeps it fixed.

```python
# boundary.py
"""Canonical UUID handling for service boundaries.

Call canonical_uuid() wherever an identifier crosses a process boundary: an
incoming HTTP request, a message from a queue, a row read from a database
that does not enforce a single case.
"""
from __future__ import annotations

import uuid


def canonical_uuid(value: str) -> str:
    """Normalise any valid UUID string to its canonical lower-case form.

    Accepts upper-case, mixed-case, and braced or hyphen-free forms, because
    uuid.UUID() parses all of them. Raises ValueError on anything that is not
    a valid UUID — deliberately, so a malformed id fails loudly here instead
    of quietly becoming a cache entry nobody will ever look up successfully.
    """
    return str(uuid.UUID(value))


class OrderStore:
    """A minimal stand-in for a cache or database table.

    Every key passes through canonical_uuid before it is used, so the store
    behaves identically whether the caller passes the id exactly as
    order_service minted it, or as warehouse_service reformatted it after a
    round trip through an upstream system.
    """

    def __init__(self) -> None:
        self._rows: dict[str, dict] = {}

    def create(self, order_id: str, payload: dict) -> None:
        self._rows[canonical_uuid(order_id)] = payload

    def get(self, order_id: str) -> dict | None:
        return self._rows.get(canonical_uuid(order_id))

    def __len__(self) -> int:
        return len(self._rows)
```

```python
# demo.py
"""Run with: python3 demo.py"""
from __future__ import annotations

import uuid

from boundary import OrderStore


def order_service_create_order(store: OrderStore) -> str:
    order_id = str(uuid.uuid4())  # lower-case, minted by this service
    store.create(order_id, {"status": "new"})
    return order_id


def warehouse_service_lookup(store: OrderStore, order_id_from_upstream: str) -> dict | None:
    # The upstream feed renders ids upper-case; the store no longer cares.
    return store.get(order_id_from_upstream)


if __name__ == "__main__":
    store = OrderStore()
    order_id = order_service_create_order(store)
    upstream_id = order_id.upper()

    print("order created with:", order_id)
    print("warehouse looks up: ", upstream_id)
    print("found:              ", warehouse_service_lookup(store, upstream_id))
    print("rows in store:      ", len(store))
```

```python
# test_boundary.py
from __future__ import annotations

import uuid

import pytest

from boundary import OrderStore, canonical_uuid


def test_canonical_uuid_ignores_case():
    lower = "3fa85f64-5717-4562-b3fc-2c963f66afa6"
    assert canonical_uuid(lower) == canonical_uuid(lower.upper())


def test_canonical_uuid_rejects_garbage():
    with pytest.raises(ValueError):
        canonical_uuid("not-a-uuid")


def test_store_is_case_insensitive_end_to_end():
    store = OrderStore()
    order_id = str(uuid.uuid4())
    store.create(order_id, {"status": "new"})

    assert store.get(order_id.upper()) == {"status": "new"}
    assert len(store) == 1


def test_reproduces_the_bug_without_normalisation():
    """Same scenario, keyed directly on the raw string — the way the broken
    version in "The problem" did it. This documents exactly what breaks if
    OrderStore is ever "simplified" back to a plain dict.
    """
    raw_store: dict[str, dict] = {}
    order_id = "3fa85f64-5717-4562-b3fc-2c963f66afa6"
    raw_store[order_id] = {"status": "new"}

    assert raw_store.get(order_id.upper()) is None  # the miss from "The problem"

    raw_store[order_id.upper()] = {"status": "new"}
    assert len(raw_store) == 2  # a duplicate row, not an update
```

```bash
pip install pytest   # the only dependency; boundary.py and demo.py are stdlib-only

python3 demo.py
# order created with: 3fa85f64-5717-4562-b3fc-2c963f66afa6   (differs each run)
# warehouse looks up:  3FA85F64-5717-4562-B3FC-2C963F66AFA6
# found:               {'status': 'new'}
# rows in store:       1

python3 -m pytest test_boundary.py -v
# test_boundary.py::test_canonical_uuid_ignores_case PASSED
# test_boundary.py::test_canonical_uuid_rejects_garbage PASSED
# test_boundary.py::test_store_is_case_insensitive_end_to_end PASSED
# test_boundary.py::test_reproduces_the_bug_without_normalisation PASSED
```

The fourth test is the one worth keeping even after the bug feels obvious: it's a
regression test for a mistake, not for a feature, and those are the ones people forget
to write because nothing asked for them.

## Conclusion

**Case-insensitivity in a specification does not make storage case-insensitive.**
RFC 4122 says UUID comparison should ignore case; dictionaries, Redis, and most
database indexes do not know that. Any identifier whose spec says "case doesn't
matter" — UUIDs, email local parts in some contexts, hostnames — needs the same
question asked of it: does the thing actually storing it agree?

**Normalise once, at the boundary, not everywhere a value is used.** A rule enforced
by convention across every call site is a rule that will eventually be broken by
someone who didn't know the convention existed. A rule enforced by one function that
every external value must pass through is a rule that can't be forgotten, only bypassed
on purpose — and bypassing it on purpose is much easier to catch in review.

**Pick the canonical form deliberately, and let validation be a side effect of
normalising.** `str(uuid.UUID(value))` does both jobs in one line: garbage fails loudly
as `ValueError` instead of silently becoming a new, wrong key.

**A bug that only appears when two independently-built systems meet won't be caught by
either system's own tests.** It needs a test that constructs the meeting point
directly — two different casings of the same id, in the same store — the way
`test_reproduces_the_bug_without_normalisation` does above. Write that test before the
mismatch happens for real, not after someone spends an afternoon working out why an
order that clearly exists can't be found.
