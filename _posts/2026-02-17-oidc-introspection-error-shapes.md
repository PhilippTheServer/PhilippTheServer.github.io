---
layout: post
title: "A 500 That Was a Misconfigured Client, Not an Auth Failure"
subtitle: "The introspection endpoint has three different response shapes, and only one has active"
date: 2026-02-17 09:00:00 +0200
tags: [identity, api-design, testing]
description: >-
  Code that assumes an OAuth2 introspection response always carries an active field
  works right up until the resource server's own client credentials are wrong, at which
  point every request that reaches it throws instead of returning a clean 401. This
  article separates the response shapes introspection can actually return and gives a
  tested client that handles all of them.
---

## The problem

RFC 7662 token introspection has one well-known response for a valid check:

```json
{"active": true, "exp": 1771000000, "scope": "openid profile"}
```

and one for a token that has expired or been revoked:

```json
{"active": false}
```

Most client code I have seen — including code I wrote — is built against exactly these
two shapes:

```python
# Broken. Do not copy this.
def is_active(token: str) -> bool:
    resp = requests.post(INTROSPECT_URL, auth=(CLIENT_ID, CLIENT_SECRET), data={"token": token})
    return resp.json()["active"]
```

This works in every manual test, because every manual test uses a token against a
correctly configured client. It breaks the first time the resource server's own
credentials are wrong — a rotated secret, a typo in an environment variable, a client
disabled in the provider console — because the introspection endpoint does not respond
with `{"active": false}` in that case. It responds with HTTP 401 and a body that has no
`active` key at all:

```json
{"error": "invalid_client", "error_description": "Invalid client credentials"}
```

`resp.json()["active"]` raises a `KeyError`. That exception propagates out of
`is_active`, and depending on the framework it turns into an unhandled 500 on every
single request the API receives, for as long as the misconfiguration lasts. The
symptom looks like "auth is down" or "the identity provider is unreachable". It is
neither — the provider is answering correctly, and the client library is asking a
question it cannot parse the answer to.

The reason this is easy to miss: the two RFC-defined shapes are indistinguishable by
schema (both are JSON objects) but very different in what they mean, and the third shape
— a client authentication failure — is not documented as part of introspection's contract
at all. It is a generic OAuth2 token-endpoint error. Nothing about the client code that
handles a `{"active": bool}` response has any reason to expect it, unless you have
deliberately gone looking.

## Working through it

### There are three shapes, not two

1. **200, `active: true`** — the token is real and current.
2. **200, `active: false`** — the token is expired, revoked, or was never issued by this
   provider. This is a normal, expected outcome, not an error.
3. **Not 200, no `active` field** — the *caller* failed to authenticate, or sent a
   malformed request. This says nothing about the token; it says the resource server's
   own credentials or request are broken.

Shape 3 must never be reported to the token's holder as "your token is invalid" — it is
not their fault and telling them so sends whoever is debugging it looking in the wrong
place. It also must not crash. It is an operational fault in the resource server's own
configuration and should be logged and surfaced as a 5xx from the API, distinctly from a
401 caused by a bad token.

### Checking the status code before touching the body

The fix is to gate on the HTTP status first, and only parse for `active` once you know
you are looking at a successful introspection response:

```python
class IntrospectionError(Exception):
    """The introspection call itself failed — not a comment on the token."""

def introspect(token: str) -> bool:
    resp = requests.post(
        INTROSPECT_URL, auth=(CLIENT_ID, CLIENT_SECRET),
        data={"token": token}, timeout=5,
    )
    if resp.status_code != 200:
        raise IntrospectionError(f"introspection endpoint returned {resp.status_code}: {resp.text}")
    body = resp.json()
    if "active" not in body:
        raise IntrospectionError(f"introspection response had no 'active' field: {body}")
    return bool(body["active"])
```

Callers now see three distinct outcomes instead of two: `True`, `False`, or an
exception — and the exception is the one that should turn into a 500 with a message an
operator can act on, not a 401 handed to a confused user.

### Writing the test before trusting the fix

The whole point is a shape that is easy to forget exists, so it needs a test that forces
it back into view every time this code changes. A tiny local HTTP server standing in for
the introspection endpoint makes this reproducible without any real identity provider:

```python
# test_introspect.py
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from introspect_client import introspect, IntrospectionError, INTROSPECT_URL  # noqa: F401


class _Handler(BaseHTTPRequestHandler):
    response_status = 200
    response_body = {"active": True}

    def do_POST(self):
        self.send_response(self.response_status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(self.response_body).encode())

    def log_message(self, *args):  # silence test output
        pass


@pytest.fixture
def fake_provider(monkeypatch):
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(
        "introspect_client.INTROSPECT_URL", f"http://127.0.0.1:{server.server_port}/introspect"
    )
    yield _Handler
    server.shutdown()


def test_active_token(fake_provider):
    fake_provider.response_status, fake_provider.response_body = 200, {"active": True}
    assert introspect("some-token") is True


def test_inactive_token(fake_provider):
    fake_provider.response_status, fake_provider.response_body = 200, {"active": False}
    assert introspect("some-token") is False


def test_client_auth_failure_does_not_read_as_invalid_token(fake_provider):
    fake_provider.response_status = 401
    fake_provider.response_body = {"error": "invalid_client"}
    with pytest.raises(IntrospectionError):
        introspect("some-token")


def test_missing_active_field_is_a_client_error_not_a_crash(fake_provider):
    fake_provider.response_status = 200
    fake_provider.response_body = {"unexpected": "shape"}
    with pytest.raises(IntrospectionError):
        introspect("some-token")
```

`test_client_auth_failure_does_not_read_as_invalid_token` is the one that would have
caught the original bug: without the status-code check, that test raises `KeyError`
instead of `IntrospectionError`, which is a signal the test suite can assert on and a
production log line cannot.

## The solution

The complete client module, with the timeout and status handling in one place so no
caller has to remember to add them:

```python
# introspect_client.py
import requests

INTROSPECT_URL = "http://localhost:8080/realms/demo/protocol/openid-connect/token/introspect"
CLIENT_ID = "introspection-client"
CLIENT_SECRET = "replace-with-a-real-secret"


class IntrospectionError(Exception):
    """Raised when the introspection call itself failed.

    This is never a statement about the token being checked — it means the resource
    server could not complete the check at all, most often because its own client
    credentials are wrong.
    """


def introspect(token: str) -> bool:
    try:
        resp = requests.post(
            INTROSPECT_URL,
            auth=(CLIENT_ID, CLIENT_SECRET),
            data={"token": token},
            timeout=5,
        )
    except requests.RequestException as exc:
        raise IntrospectionError(f"could not reach introspection endpoint: {exc}") from exc

    if resp.status_code != 200:
        raise IntrospectionError(
            f"introspection endpoint returned {resp.status_code}: {resp.text}"
        )

    try:
        body = resp.json()
    except ValueError as exc:
        raise IntrospectionError(f"introspection response was not JSON: {resp.text}") from exc

    if "active" not in body:
        raise IntrospectionError(f"introspection response had no 'active' field: {body}")

    return bool(body["active"])
```

```bash
pip install requests pytest
pytest test_introspect.py -v
```

```
test_introspect.py::test_active_token PASSED
test_introspect.py::test_inactive_token PASSED
test_introspect.py::test_client_auth_failure_does_not_read_as_invalid_token PASSED
test_introspect.py::test_missing_active_field_is_a_client_error_not_a_crash PASSED
```

## Conclusion

**An external API's documented "happy path" shape is not its only shape.** RFC 7662
defines two success responses and leaves failure to the generic OAuth2 error format;
code that only models the two it read about in the first paragraph of the spec will
break on the third.

**Distinguish "the thing you asked about is invalid" from "I could not ask".** These need
different HTTP status codes on your own API, different log severities, and different
runbooks. Collapsing them into one boolean throws away the information a responder needs
to know whether to look at the token or at the resource server's own configuration.

**Write the test for the shape you have not seen yet, not just the one you have.** The
inactive-token test would have shipped with the original code and never caught this bug.
The one that does is the one that requires imagining a failure mode the spec mentions
only in passing.
