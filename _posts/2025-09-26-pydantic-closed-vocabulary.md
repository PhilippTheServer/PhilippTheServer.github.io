---
layout: post
title: "Closed-Vocabulary Pydantic Validation for a Public Ingest Endpoint"
subtitle: "An endpoint anyone can POST to will accept a typo'd field and silently drop it."
date: 2025-09-26 09:00:00 +0200
tags: [fastapi, api-design]
description: >-
  Pydantic ignores fields it does not recognise by default, so a client with
  a typo'd field name or an out-of-vocabulary value gets a 200 response and
  never learns their request did not do what they thought. This shows how to
  close the vocabulary with extra="forbid" and Literal types, and what that
  choice costs in forward compatibility.
---

## The problem

A public ingest endpoint accepts events from clients you do not control the code of —
webhooks from a partner, telemetry from devices already deployed in the field, or simply
any external caller of an API you publish. The model looks ordinary:

```python
class EventIn(BaseModel):
    event_type: str
    source: str
    payload: dict
```

A client integrating against this sends:

```json
{"event_type": "user_signed_up", "sourc": "mobile_app", "payload": {}}
```

`sourc` is a typo for `source`. Pydantic's default behaviour for a field it does not
recognise is to ignore it — not warn, not error, simply drop it from the validated model.
The request validates successfully, the endpoint returns `201`, and the event is stored
with `source` absent or defaulted to whatever the field's default happens to be. The
client sees a success response and has no reason to suspect anything is wrong. Weeks
later, someone asks why every event from that integration is missing its source, and the
answer is a single missing letter that nothing in the request/response cycle ever
surfaced.

`event_type` has the same problem in a different shape. It is typed as `str`, which
accepts anything — `"user_signed_up"`, `"usre_signed_up"`, `"UserSignedUp"` are all
equally valid as far as Pydantic is concerned. A typo here does not even get dropped; it
gets stored as a new, silently-invented event type that nothing downstream recognises,
and it sits in the data indefinitely until someone happens to query for distinct event
types and notices one that should not exist.

Both failures share a root cause: the model describes a *shape* — some fields with some
types — without describing a *vocabulary*. Extra fields are permitted implicitly. Field
values that happen to be strings are permitted to be any string. Nothing about the schema
actually constrains the space of valid requests to the space of requests that make sense.

## Working through it

### `extra="forbid"` turns a silent drop into a visible rejection

Pydantic v2's model configuration includes an `extra` setting, and the default,
`"ignore"`, is precisely what causes the typo'd field to vanish without complaint.
Setting it to `"forbid"` makes any unrecognised field a validation error instead:

```python
class EventIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_type: str
    source: str
    payload: dict
```

The same `{"event_type": "...", "sourc": "...", "payload": {}}` payload now fails with a
`422`, and FastAPI's default error body names the exact field it did not recognise. The
client's integration breaks immediately, during development, instead of shipping a
silent data quality problem into production. This is the entire value of the setting: it
converts a class of bug from "discovered eventually by someone auditing the data" to
"discovered immediately by the client's own test suite."

### `Literal` and `Enum` close the value space, not just the field space

`extra="forbid"` only governs which *fields* are accepted. `event_type: str` still accepts
any string value for a field it does recognise. Closing that requires naming the actual
vocabulary:

```python
from typing import Literal

EventType = Literal["user_signed_up", "user_deleted", "order_placed"]


class EventIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_type: EventType
    source: str
    payload: dict
```

`"usre_signed_up"` is now a validation error naming exactly which values are acceptable,
rather than a value that quietly makes it into storage. An `Enum` gives the same closure
and is worth preferring over `Literal` once the vocabulary is reused across more than one
model, or needs a docstring per value — `Literal` is the leaner choice for a vocabulary
that lives in exactly one place.

### The trade-off: this is a compatibility decision, not just a validation one

Closing the vocabulary means every new event type, and every new field a client might
reasonably want to send, requires a server-side change before any client can use it. This
is the correct trade for an endpoint where an unrecognised field is a bug — most public
ingest APIs, where "the client meant something we don't understand yet" is worse than "the
client cannot send it until we add support." It is the wrong trade for an endpoint that
genuinely wants forward-compatible, additive fields from clients you do not control the
release schedule of, where `extra="allow"` and storing the extra fields verbatim, or a
versioned schema per client, are more honest solutions.

Pick one, and say so next to the model, because "why can't I add a field to this payload"
is a question that will get asked, and the answer needs to be a decision someone made, not
a default nobody noticed.

### Making the rejection message actually useful

FastAPI's default `422` body for a Pydantic validation error is functional but generic —
it is worth checking, once, that it actually names the offending field and the accepted
values clearly enough for an external integrator with no access to your source code to fix
their request without asking you. The default from Pydantic v2 does include the literal
values it expected, which is usually sufficient without further customisation.

## The solution

A complete, runnable ingest endpoint with a closed vocabulary, plus tests that assert both
the acceptance and rejection paths.

```text
app/
  main.py
  schemas.py
tests/
  test_ingest.py
requirements.txt
```

```python
# app/schemas.py
from typing import Literal

from pydantic import BaseModel, ConfigDict

EventType = Literal["user_signed_up", "user_deleted", "order_placed"]
EventSource = Literal["mobile_app", "web_app", "partner_webhook"]


class EventIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_type: EventType
    source: EventSource
    payload: dict
```

```python
# app/main.py
from fastapi import FastAPI

from app.schemas import EventIn

app = FastAPI(title="Closed-vocabulary ingest example")

_events: list[EventIn] = []


@app.post("/events", status_code=201)
def ingest_event(event: EventIn) -> dict:
    _events.append(event)
    return {"stored": True, "count": len(_events)}


@app.get("/events/count")
def count_events() -> dict:
    return {"count": len(_events)}
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
# tests/test_ingest.py
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_valid_event_is_accepted():
    response = client.post(
        "/events",
        json={"event_type": "user_signed_up", "source": "mobile_app", "payload": {}},
    )
    assert response.status_code == 201
    assert response.json()["stored"] is True


def test_typo_d_field_name_is_rejected_not_dropped():
    response = client.post(
        "/events",
        json={"event_type": "user_signed_up", "sourc": "mobile_app", "payload": {}},
    )
    assert response.status_code == 422
    body = response.json()
    assert any("sourc" in str(error["loc"]) for error in body["detail"])


def test_out_of_vocabulary_event_type_is_rejected():
    response = client.post(
        "/events",
        json={"event_type": "usre_signed_up", "source": "mobile_app", "payload": {}},
    )
    assert response.status_code == 422
    body = response.json()
    assert any(error["loc"][-1] == "event_type" for error in body["detail"])
```

### Verifying it

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pytest tests/ -v

uvicorn app.main:app --reload &
curl -s -X POST localhost:8000/events \
  -H 'content-type: application/json' \
  -d '{"event_type": "user_signed_up", "sourc": "mobile_app", "payload": {}}'
```

Correct output from pytest:

```text
tests/test_ingest.py::test_valid_event_is_accepted PASSED
tests/test_ingest.py::test_typo_d_field_name_is_rejected_not_dropped PASSED
tests/test_ingest.py::test_out_of_vocabulary_event_type_is_rejected PASSED
```

And the `curl` with the typo'd field returns a `422` naming `sourc` in its `detail`
array, rather than the `201` it would have returned before `extra="forbid"` was added.

## Conclusion

**A field Pydantic silently drops is a bug report the client never gets to file.** Default
`extra="ignore"` behaviour is right for a model consuming a payload you do not fully
control and only care about part of; it is wrong for a boundary where every field the
client sends is supposed to mean something.

**Typing a field as `str` describes its shape, not its vocabulary.** Anywhere a field has
a genuinely closed set of valid values, `Literal` or `Enum` should say so, and the cost is
one line per field, paid once, against an otherwise indefinite stream of silently invalid
data.

**Closing a vocabulary is a compatibility promise, and it needs to be a deliberate one.**
It means new values require a server-side release before clients can send them — correct
for a public ingest surface where an unrecognised value is a bug, wrong for a surface that
genuinely needs additive, client-driven evolution. The choice belongs next to the model,
stated on purpose, not left to whichever default Pydantic happened to ship with.
