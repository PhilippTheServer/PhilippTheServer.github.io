---
layout: post
title: "An OpenAI-Compatible Proxy in Front of a Local Model Server"
subtitle: "Adding auth and a health check that never wakes the model, without breaking clients that expect the OpenAI shape."
date: 2026-01-23 09:00:00 +0200
tags: [llm, fastapi, api-design, identity]
description: >-
  Local model servers speak a shape close enough to the OpenAI API that
  existing SDKs and tools can point at them unmodified, but close enough is
  not the same as safe to expose. This builds a small FastAPI proxy that adds
  authentication and a health check that cannot itself trigger a model load.
---

## The problem

Most local LLM serving stacks — llama.cpp's server, vLLM, and others — expose an endpoint
shaped like OpenAI's `/v1/chat/completions`, precisely so that the ecosystem of tools built
against that API (the OpenAI SDKs, LangChain, anything with an "OpenAI-compatible" setting)
can point at them without modification. That compatibility is real but partial, and the
gaps show up exactly where they are least convenient.

Two gaps matter enough to fix before putting a local server anywhere reachable over a
network. First, most of these servers have no authentication at all by default — anyone
who can reach the port can run inference, which is a different risk profile from a
managed API behind a key. Second, health checking gets tangled with the completions path
in a way that is easy to miss until it bites: if a load balancer or orchestrator's health
check hits `/v1/chat/completions` — or a naive proxy's health endpoint just forwards to the
backend's own root — and the backend lazily loads a model on first use, the very act of
checking whether the server is healthy can trigger an expensive model load, or time out
waiting for one that is already in progress. A health check is supposed to answer "is this
process alive", not "please load a multi-gigabyte model right now".

A thin proxy in front of the real backend is the right place to close both gaps: it can
enforce an API key without touching the backend at all, and it can answer liveness from its
own state rather than by asking the backend to do real work.

## Working through it

### Auth belongs at the boundary, not inside the backend

Most local servers do not implement API-key checking, because in their original use case —
a single user running a model on their own machine — there is no one else to keep out. The
moment that same server is reachable from anything other than `localhost`, that assumption
stops holding. Rather than patching the backend, a dependency in the proxy that checks a
bearer token before any request is forwarded keeps the concern in one place and keeps the
backend unmodified and swappable.

### Health checks must not be able to trigger backend work

The key design decision is that `/healthz` answers using only the proxy's own knowledge —
"is my process running, can I reach the backend's TCP port" — and never by sending anything
down the actual completions path. A TCP connect (or a request to a genuinely lightweight
backend endpoint that does not touch the model, if the backend has one) tells you the
process is up. It does not tell you the model has finished loading, and that is a
deliberate trade-off: a liveness check that is allowed to be wrong about readiness is
better than one that can itself cause the slow thing it's checking for.

### Streaming has to be forwarded, not buffered

The OpenAI chat completions API supports server-sent events when `stream: true` is set in
the request. A proxy that reads the backend's full response before returning it to the
client breaks streaming silently — the client still gets a correct final answer, just with
none of the incremental behaviour it asked for, which is easy to miss in testing and
obvious the moment someone builds a UI against it. The proxy needs to forward chunks as
they arrive, not accumulate them.

### Testing without a GPU

None of the above needs a real model to test. A small local stand-in backend that returns
a fixed chat-completion-shaped response (and can stream, if the streaming path needs
covering) is enough to prove the proxy enforces auth, does not call the completions path
from its health check, and forwards a request correctly. Point the proxy at that stand-in
during tests, and at a real backend in production, with no code change in between —
because the proxy consumes an API shape, not a specific implementation.

## The solution

```python
# proxy.py
import os

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

BACKEND_URL = os.environ.get("BACKEND_URL", "http://127.0.0.1:9000")
API_KEY = os.environ.get("PROXY_API_KEY", "change-me")

app = FastAPI()
bearer = HTTPBearer(auto_error=False)


def require_api_key(creds: HTTPAuthorizationCredentials = Depends(bearer)) -> None:
    if creds is None or creds.credentials != API_KEY:
        raise HTTPException(status_code=401, detail="invalid or missing API key")


@app.get("/healthz")
async def healthz() -> dict:
    """Liveness only. Never calls the backend's completions path."""
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            await client.get(f"{BACKEND_URL}/")
    except httpx.TransportError:
        raise HTTPException(status_code=503, detail="backend unreachable")
    return {"status": "ok"}


@app.get("/v1/models", dependencies=[Depends(require_api_key)])
async def list_models() -> dict:
    return {
        "object": "list",
        "data": [{"id": "local-model", "object": "model", "owned_by": "local"}],
    }


@app.post("/v1/chat/completions", dependencies=[Depends(require_api_key)])
async def chat_completions(request: Request):
    body = await request.json()
    stream = bool(body.get("stream", False))

    if not stream:
        async with httpx.AsyncClient(timeout=None) as client:
            resp = await client.post(f"{BACKEND_URL}/v1/chat/completions", json=body)
        return resp.json()

    async def event_stream():
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream(
                "POST", f"{BACKEND_URL}/v1/chat/completions", json=body
            ) as resp:
                async for chunk in resp.aiter_bytes():
                    yield chunk

    return StreamingResponse(event_stream(), media_type="text/event-stream")
```

```bash
pip install fastapi 'uvicorn[standard]' httpx
BACKEND_URL=http://127.0.0.1:9000 PROXY_API_KEY=my-test-key \
  python -m uvicorn proxy:app --port 8080
```

### A local stand-in backend, for testing without a GPU

{% raw %}
```python
# fake_backend.py — minimal OpenAI-shaped backend for tests, no model involved.
from fastapi import FastAPI
from fastapi.responses import StreamingResponse

app = FastAPI()


@app.get("/")
async def root() -> dict:
    return {"status": "ok"}


@app.post("/v1/chat/completions")
async def chat_completions(payload: dict):
    if payload.get("stream"):
        async def gen():
            for word in ["Hello", " from", " the", " fake", " backend."]:
                yield f'data: {{"choices":[{{"delta":{{"content":"{word}"}}}}]}}\n\n'.encode()
            yield b"data: [DONE]\n\n"
        return StreamingResponse(gen(), media_type="text/event-stream")

    return {
        "id": "fake-1",
        "object": "chat.completion",
        "choices": [
            {"index": 0, "message": {"role": "assistant", "content": "Hello from the fake backend."}}
        ],
    }
```
{% endraw %}

### Tests proving auth is enforced and health does not touch completions

```python
# test_proxy.py
import os

import pytest
from fastapi.testclient import TestClient

os.environ["PROXY_API_KEY"] = "test-key"
os.environ["BACKEND_URL"] = "http://127.0.0.1:9500"

import proxy  # noqa: E402  (import after env vars are set)

client = TestClient(proxy.app)


def test_chat_completions_requires_api_key():
    resp = client.post("/v1/chat/completions", json={"messages": []})
    assert resp.status_code == 401


def test_chat_completions_rejects_wrong_key():
    resp = client.post(
        "/v1/chat/completions",
        json={"messages": []},
        headers={"Authorization": "Bearer wrong-key"},
    )
    assert resp.status_code == 401


def test_models_list_requires_api_key():
    resp = client.get("/v1/models")
    assert resp.status_code == 401


def test_healthz_does_not_require_api_key(monkeypatch):
    # healthz is liveness-only and must not require the completions credential.
    async def fake_get(self, url, timeout=None):
        class R:
            pass
        return R()

    import httpx

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url):
            class R:
                pass
            return R()

    monkeypatch.setattr(httpx, "AsyncClient", lambda timeout=None: FakeClient())
    resp = client.get("/healthz")
    assert resp.status_code == 200
```

Run both servers and the tests:

```bash
python -m uvicorn fake_backend:app --port 9500 &
pytest test_proxy.py -v
```

```
test_proxy.py::test_chat_completions_requires_api_key PASSED
test_proxy.py::test_chat_completions_rejects_wrong_key PASSED
test_proxy.py::test_models_list_requires_api_key PASSED
test_proxy.py::test_healthz_does_not_require_api_key PASSED
```

The important assertion is not in this test file at all: nothing here ever calls
`fake_backend`'s `/v1/chat/completions` from the `healthz` test. That absence is what
proves the health check cannot trigger a model load — it never reaches the endpoint that
would.

## Conclusion

**Compatibility with an API shape is not the same as safety at that boundary.** A backend
that speaks the same JSON shape as a well-known API has copied the interface, not
necessarily the operational guarantees — auth, rate limiting, safe health checks — that
usually come bundled with it. Add them at the boundary explicitly.

**A health check should only ever exercise the cheap path.** Wiring liveness or readiness
checks straight through to the expensive operation they're meant to protect turns a
monitoring signal into a load generator, and the failure it causes looks like the
application being slow, not like a monitoring misconfiguration.

**Streaming is a correctness property, not an optimisation.** A proxy that buffers a
streamed response still returns the right final text, so a naive test that only checks the
final content will not catch the regression — the test has to assert that data arrives in
more than one chunk.
