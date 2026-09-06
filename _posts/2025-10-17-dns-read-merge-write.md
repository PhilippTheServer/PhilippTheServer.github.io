---
layout: post
title: "Managing a Shared DNS Zone Through a Replace-Everything API"
subtitle: "A read-merge-write client with an ownership marker, so automation never deletes what it doesn't manage."
date: 2025-10-17 09:00:00 +0200
tags: [dns, automation, infrastructure-as-code, api-design]
description: >-
  Many registrar and DNS provider APIs expose only "replace the whole zone",
  with no way to add or remove a single record, so naive automation that
  computes its desired records and pushes them deletes every record it does
  not know about. This builds a read-merge-write client with an ownership
  marker that tells apart managed and unmanaged records, backed by a small
  local test server so the whole pattern can be run and verified without any
  real registrar.
---

## The problem

Some DNS provider APIs give you fine-grained record management: create this one record,
delete that one record. A surprising number do not — they expose `GET /zone` and
`PUT /zone`, and `PUT` means *this is now the entire zone*. Anything not in the payload is
gone after the call returns.

The naive automation script computes the records it wants — `app.example.internal` →
`10.0.0.10`, say — and `PUT`s exactly that. It works the first time, on a zone that had
nothing else in it. It is catastrophic the first time it runs against a zone that also has
an MX record for mail, a TXT record for a domain verification, and an A record for a
service the automation has never heard of, because none of those survive the `PUT`.

This is easy to miss in testing because test zones are usually created empty, precisely
for the automation being tested. It surfaces in production, against the zone that has
accumulated years of manually added records nobody wrote a ticket for, and the first sign
of trouble is unrelated services becoming unreachable minutes after a routine deploy.

The fix is not "be more careful with the payload" — that does not survive someone adding a
record by hand next month. The fix is a client that reads the current zone, keeps
everything it does not manage untouched, and can prove, by inspection, which records are
its responsibility.

## Working through it

### Marking ownership instead of assuming it

The client cannot tell "a record I created" from "a record someone else created" just by
looking at a name and a value — both look identical on the wire. It needs an explicit
marker. A common, low-tech approach (the one `external-dns` uses for exactly this problem)
is a sibling TXT record next to each managed record, recording that this name is owned by
this automation:

```
app.example.internal.       A     10.0.0.10
app.example.internal.       TXT   "managed-by=zone-automation"
```

Any record without a matching ownership TXT is left alone, whatever it is.

### The algorithm, not just the API call

1. `GET` the current zone.
2. Partition its records into "has a matching ownership TXT" (managed) and everything else
   (untouched).
3. Compute the desired managed records from configuration.
4. Build the record set to `PUT`: untouched records, unchanged, plus the desired managed
   records and their ownership TXT records.
5. `PUT` the result.

Steps 1 and 2 are what make step 5 safe. Skipping straight to "compute desired records and
`PUT`" is the naive version that deletes step 2's records.

### Detecting drift correctly, including deletions

If a record this automation is supposed to manage disappears from the *desired*
configuration (a service was decommissioned), the merge must remove it *and* its ownership
TXT — comparing the previous managed set against the new desired set, not just adding
what's new. Only comparing "what to add" silently leaves orphaned records; only comparing
by name misses the case where a record's type or value changed but ownership didn't.

### Testing this without a real registrar

The pattern is entirely about how the client behaves against an API shaped like
"replace-everything", not about any specific registrar's quirks. A tiny local HTTP server
that implements exactly that shape — `GET /zone` and `PUT /zone`, holding records in
memory — is enough to prove the client is correct, and it is something a reader can run
without an account anywhere.

## The solution

```python
# fake_registrar.py
"""A minimal stand-in for a replace-everything DNS API. Not for real use."""
from __future__ import annotations

from flask import Flask, jsonify, request

app = Flask(__name__)
ZONE: list[dict] = [
    {"name": "mail.example.internal", "type": "MX", "value": "10 mail.example.internal"},
    {"name": "verify.example.internal", "type": "TXT", "value": "site-verification=abc123"},
]


@app.get("/zone")
def get_zone():
    return jsonify(ZONE)


@app.put("/zone")
def put_zone():
    global ZONE
    ZONE = request.get_json()
    return jsonify(ZONE)


if __name__ == "__main__":
    app.run(port=8053)
```

```python
# dns_sync.py
"""Read-merge-write client for a replace-everything DNS API."""
from __future__ import annotations

import requests

OWNER_ID = "zone-automation"


def _owner_txt_name(record_name: str) -> str:
    return f"_owner.{record_name}"


def _is_ours(record: dict, owned_names: set[str]) -> bool:
    if record["type"] == "TXT" and record["name"].startswith("_owner."):
        return record["name"][len("_owner."):] in owned_names
    return record["name"] in owned_names


def sync(api_base: str, desired: list[dict]) -> list[dict]:
    """desired: [{"name": ..., "type": ..., "value": ...}, ...]"""
    current = requests.get(f"{api_base}/zone", timeout=5).json()

    desired_names = {record["name"] for record in desired}
    owner_records_current = {
        record["name"][len("_owner."):]
        for record in current
        if record["type"] == "TXT" and record["name"].startswith("_owner.")
        and record["value"] == f"owner={OWNER_ID}"
    }
    previously_managed = owner_records_current

    untouched = [
        record
        for record in current
        if not _is_ours(record, previously_managed)
    ]

    managed = list(desired)
    for record in desired:
        managed.append(
            {
                "name": _owner_txt_name(record["name"]),
                "type": "TXT",
                "value": f"owner={OWNER_ID}",
            }
        )

    new_zone = untouched + managed
    response = requests.put(f"{api_base}/zone", json=new_zone, timeout=5)
    response.raise_for_status()
    return response.json()


if __name__ == "__main__":
    desired_records = [
        {"name": "app.example.internal", "type": "A", "value": "10.0.0.10"},
    ]
    result = sync("http://localhost:8053", desired_records)
    for record in result:
        print(record)
```

```python
# test_dns_sync.py
import threading

import pytest
import requests

from fake_registrar import app as fake_app
from dns_sync import sync


@pytest.fixture(scope="module", autouse=True)
def run_fake_registrar():
    server = threading.Thread(
        target=lambda: fake_app.run(port=8053, use_reloader=False), daemon=True
    )
    server.start()
    import time

    for _ in range(50):
        try:
            requests.get("http://localhost:8053/zone", timeout=0.2)
            break
        except requests.exceptions.ConnectionError:
            time.sleep(0.1)
    yield


def zone_names():
    return {r["name"] for r in requests.get("http://localhost:8053/zone").json()}


def test_sync_does_not_delete_preexisting_unmanaged_records():
    assert "mail.example.internal" in zone_names()
    sync("http://localhost:8053", [{"name": "app.example.internal", "type": "A", "value": "10.0.0.10"}])
    assert "mail.example.internal" in zone_names()
    assert "verify.example.internal" in zone_names()


def test_sync_adds_the_managed_record_and_its_owner_txt():
    sync("http://localhost:8053", [{"name": "app.example.internal", "type": "A", "value": "10.0.0.10"}])
    names = zone_names()
    assert "app.example.internal" in names
    assert "_owner.app.example.internal" in names


def test_sync_is_idempotent():
    desired = [{"name": "app.example.internal", "type": "A", "value": "10.0.0.10"}]
    sync("http://localhost:8053", desired)
    first = requests.get("http://localhost:8053/zone").json()
    sync("http://localhost:8053", desired)
    second = requests.get("http://localhost:8053/zone").json()
    assert sorted(first, key=str) == sorted(second, key=str)


def test_sync_removes_a_managed_record_dropped_from_desired_state():
    sync("http://localhost:8053", [{"name": "app.example.internal", "type": "A", "value": "10.0.0.10"}])
    assert "app.example.internal" in zone_names()

    sync("http://localhost:8053", [])
    names = zone_names()
    assert "app.example.internal" not in names
    assert "_owner.app.example.internal" not in names
    assert "mail.example.internal" in names
```

Running it:

```bash
pip install flask requests pytest
pytest -v test_dns_sync.py
# test_sync_does_not_delete_preexisting_unmanaged_records PASSED
# test_sync_adds_the_managed_record_and_its_owner_txt PASSED
# test_sync_is_idempotent PASSED
# test_sync_removes_a_managed_record_dropped_from_desired_state PASSED

# Or interactively:
python fake_registrar.py &
python dns_sync.py
# {'name': 'mail.example.internal', 'type': 'MX', 'value': '10 mail.example.internal'}
# {'name': 'verify.example.internal', 'type': 'TXT', 'value': 'site-verification=abc123'}
# {'name': 'app.example.internal', 'type': 'A', 'value': '10.0.0.10'}
# {'name': '_owner.app.example.internal', 'type': 'TXT', 'value': 'owner=zone-automation'}
```

`test_sync_removes_a_managed_record_dropped_from_desired_state` is the test that matters
most in this file: it is the one a reviewer should ask for if it is missing, because it is
the case that distinguishes "adds records safely" from "actually manages a set of
records", and it is the case most naive implementations get wrong first.

## Conclusion

A replace-everything API is not a worse API than one with granular endpoints, it is an API
that pushes the bookkeeping onto the client. Doing that bookkeeping honestly means the
client, not the server, is now responsible for knowing what it owns.

Three points generalise past DNS specifically:

**Any "replace the whole resource" API needs a read before the write, always.** This
applies identically to full-document config APIs, whole-file uploads that overwrite a
directory listing, and bulk-replace endpoints in general — the shape of the mistake is the
same in each case.

**An explicit ownership marker beats an implicit convention.** Assuming "anything matching
this naming pattern is ours" breaks the moment someone outside the automation happens to
use a similar name; a marker that says so explicitly does not.

**Test the deletion path as carefully as the creation path.** Automation that only adds
things looks correct in every demo and fails the first time something needs to be taken
away — which, for DNS records that outlive the service they pointed at, is not a rare
case.
