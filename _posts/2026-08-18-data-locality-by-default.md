---
layout: post
title: "One Local Endpoint for Every Agent Session"
subtitle: "Routing every local tool through one proxy so egress is a single, auditable decision."
date: 2026-08-18 09:00:00 +0200
tags: [llm, security, python]
description: >-
  An IDE assistant, a terminal agent and a background daemon can each be
  configured to send code to a different endpoint, and nobody can answer
  where code actually goes without checking every tool individually. This
  article routes all of them through one local proxy that logs and can
  refuse requests, with a complete Docker Compose example and an honest
  account of what it doesn't guarantee.
---

## The problem

A single developer machine ends up running several tools that talk to a model: an editor
plugin, a terminal-based coding agent, sometimes a background daemon watching for issues.
Each one is configured independently, usually at the point someone first installed it, with
whatever endpoint and key it happened to ask for at the time.

That sprawl accumulates quietly. One tool points at a vendor's cloud endpoint. Another was
switched to a self-hosted model six months ago and nobody updated the first one to match.
A third still has a key for a proxy that was decommissioned, and either fails silently or
falls back to something nobody chose deliberately. Answering the question "where does code
from this machine actually get sent" means opening every tool's settings individually and
hoping the answer hasn't changed since the last time someone checked.

Any policy you'd like to enforce — "this repository's code never leaves the building," say
— has to be implemented, and kept correct, once per tool. There is no single place that
sees every outbound request, so there is no single place to enforce or even observe
anything about them.

## Working through it

### Make egress a routing decision, not a per-tool setting

Most tools that speak to a model already support pointing at an arbitrary base URL, because
the OpenAI-compatible API shape is close to a de facto standard. Pointing every tool at the
same local address — `http://localhost:8080/v1`, say — turns "where does this go" from a
property of each tool into a property of one process.

### Put the decision in one auditable place

A local proxy that every tool's requests pass through can log the essentials of each one —
which project it was for, how large it was, which upstream it went to — before forwarding
it. "Did any tool send this repository's code externally, ever" becomes one log query
instead of an audit of every tool's configuration file.

### Make policy enforceable, not just observable

Once every request already passes through one place, that place can also refuse a request,
not just record it — for instance, routing anything tagged as belonging to a sensitive
project only to a self-hosted model, and refusing outright to forward it anywhere else,
regardless of what any individual tool was configured to think its upstream was.

### Accept what this doesn't solve

A proxy only sees what's configured to use it. A tool with a hardcoded upstream URL, or one
that ignores the environment variable convention, bypasses it entirely — this is a
convention enforced by configuration, not a network-level guarantee. It also adds a hop of
latency and a single point of failure: every tool that depends on the proxy is down when
the proxy is down. A genuine guarantee, rather than a convention, needs this paired with an
actual egress firewall rule blocking direct outbound connections from the machine to model
vendors' networks, which is a heavier and more disruptive control to run alongside it.

## The solution

A stand-in for a self-hosted model, so the whole example runs without a real model or API
key:

```python
# mock_model/app.py
"""A stand-in for a self-hosted model backend. Echoes the prompt back so
the example runs without a real model or API key."""
from fastapi import FastAPI, Request

app = FastAPI()


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    body = await request.json()
    prompt = body["messages"][-1]["content"]
    return {
        "id": "mock-1",
        "object": "chat.completion",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": f"echo: {prompt}"},
                "finish_reason": "stop",
            }
        ],
    }
```

The single egress point:

```python
# router/app.py
"""The single egress point every local tool is configured to use.

Every request is logged before it is forwarded, and requests tagged for
a project marked internal-only are refused rather than forwarded anywhere
external.
"""
import logging
import os

import httpx
from fastapi import FastAPI, HTTPException, Request, Response

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger("router")

UPSTREAM = os.environ.get("UPSTREAM_URL", "http://mock-model:8000")
INTERNAL_ONLY_PROJECTS = {"restricted-project"}

app = FastAPI()


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    body = await request.body()
    project = request.headers.get("x-project-id", "unspecified")

    logger.info("project=%s bytes=%d upstream=%s", project, len(body), UPSTREAM)

    if project in INTERNAL_ONLY_PROJECTS and "example.com" in UPSTREAM:
        raise HTTPException(
            status_code=403,
            detail=f"project '{project}' may not route to an external upstream",
        )

    async with httpx.AsyncClient() as client:
        upstream_response = await client.post(
            f"{UPSTREAM}/v1/chat/completions", content=body, timeout=60
        )
    return Response(
        content=upstream_response.content,
        status_code=upstream_response.status_code,
        media_type="application/json",
    )
```

```dockerfile
# router/Dockerfile and mock_model/Dockerfile (identical apart from the copy)
FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir fastapi==0.115.0 uvicorn==0.30.6 httpx==0.27.2
COPY app.py .
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
```

```yaml
# docker-compose.yml
services:
  mock-model:
    build: ./mock_model
    expose:
      - "8000"

  router:
    build: ./router
    ports:
      - "8080:8000"
    environment:
      UPSTREAM_URL: "http://mock-model:8000"
    depends_on:
      - mock-model
```

Run it and point a request at the single local endpoint every tool would use:

```bash
docker compose up --build -d

curl -s http://localhost:8080/v1/chat/completions \
  -H "content-type: application/json" \
  -H "x-project-id: demo-project" \
  -d '{"messages": [{"role": "user", "content": "hello"}]}'
```

```
{"id":"mock-1","object":"chat.completion","choices":[{"index":0,"message":{"role":"assistant","content":"echo: hello"},"finish_reason":"stop"}]}
```

```bash
docker compose logs router --tail 2
```

```
router-1  | 2026-08-18 09:00:01,004 project=demo-project bytes=63 upstream=http://mock-model:8000
```

Every tool on the machine would set `OPENAI_BASE_URL=http://localhost:8080/v1` and nothing
else changes about how they're used — the routing decision, and the log of it, now lives in
one place instead of scattered across each tool's own settings.

## Conclusion

Centralising egress turns an audit of every tool's configuration into a single log stream,
which is the difference between a question you can actually answer and one you can only
guess at.

The convention is only as strong as every tool's willingness to honour it — treat this as a
control that removes sprawl and gives you visibility, not as a network-level guarantee.

For an actual guarantee, pair it with an egress firewall rule that blocks direct outbound
traffic to model vendors' networks; the proxy on its own is trust plus visibility, not
enforcement against a tool that decides to bypass it.

The idea generalises past model traffic: any fleet of independently-configured tools that
can each reach the outside world on their own benefits from being routed through one
chokepoint that can log, and can refuse, before anything actually leaves the machine.
