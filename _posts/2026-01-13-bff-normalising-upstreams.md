---
layout: post
title: "One Dashboard on Five Unrelated Backends: Normalising at the Boundary"
subtitle: "Why a BFF is a translation layer, not a proxy, and how to make its failures partial."
date: 2026-01-13 09:00:00 +0200
tags: [fastapi, architecture, api-design, frontend]
description: >-
  A dashboard that calls a ticketing system and a CI system directly inherits
  their different pagination styles, error shapes and auth schemes. This
  article builds a FastAPI backend-for-frontend that normalises both into one
  schema, degrades gracefully when an upstream is down, and shows how to test
  each adapter without touching a real upstream.
---

## The problem

A portal surfacing open tickets, time tracked and CI status needs data from three systems
never designed to sit next to each other. The obvious first step is a FastAPI route per
upstream that forwards the request and returns whatever comes back:

```python
# Broken. Do not copy this.
import httpx
from fastapi import FastAPI

app = FastAPI()

@app.get("/api/tickets")
async def tickets():
    async with httpx.AsyncClient() as client:
        response = await client.get("https://tickets.example.com/api/v1/issues")
        return response.json()
```

A second route does the same for the CI system, and agrees with the first on almost nothing.
Ticketing paginates with `page`, `per_page` and `has_more`; CI with an opaque `cursor`.
Ticketing calls the status field "state" (`open`, `in-progress`, `resolved`, `closed`); CI
calls the same concept "result" (`running`, `passed`, `failed`). One authenticates with an
API key in the query string, the other with a bearer token. Neither is a bug — different
teams built these, years apart. The bug is assuming a thin proxy is a design.

Every one of those differences now lives in the frontend, because the route only changed the
URL:

- a pagination helper per upstream, because `has_more` and `cursor` need different loops
- a status-mapping table per upstream, because `state` and `result` speak different vocabularies
- the CI system's bearer token sitting in code the browser can read, forwarded verbatim
- a third full set of these the day a time-tracking upstream joins the portal

None of this produces an incident, which is why it accumulates unnoticed. What gets noticed
is a blank dashboard the day one upstream times out — a rejected promise took the whole
render tree down, because nothing decided otherwise.

## Working through it

### Why pass-through proxying leaks upstream inconsistency

A route that forwards a request and returns the response verbatim relocates where the
network call originates; it does not change what the frontend has to understand. It depended
on N upstream schemas before the proxy existed, and still does after, just reached through
one hostname instead of three.

A backend-for-frontend earns its name only when it does what a proxy does not: pick a shape
belonging to the frontend and translate every upstream into it. The adapter absorbs
pagination, field names and auth scheme; the frontend sees none of it.

### Designing a normalised internal schema

The schema is not the union of every field every upstream exposes. It is the smallest shape
the frontend needs, decided independently of any single upstream:

```python
class WorkItem(BaseModel):
    id: str
    source: WorkItemSource
    title: str
    status: WorkItemStatus
    url: str
    updated_at: datetime
```

`WorkItemStatus` is a closed enum — `open`, `in_progress`, `done`, `failed` — and each adapter
maps its upstream's vocabulary onto it once, by whoever understands that upstream best. The
alternative is a raw status string, mapped instead by whichever frontend engineer next
renders a badge.

Every adapter satisfies one contract:

```python
class UpstreamAdapter(ABC):
    source: WorkItemSource

    @abstractmethod
    async def fetch(self, client: httpx.AsyncClient) -> list[WorkItem]:
        raise NotImplementedError
```

Adding a fourth upstream — a time-tracking API, say — means writing one class that returns
`list[WorkItem]`, not touching the aggregator or the frontend.

### Handling partial failure

A naive proxy never answers what happens when one upstream is unreachable. Left unanswered,
the answer is "whatever an unhandled exception does" — usually the whole response failing
regardless of how many upstreams stayed healthy.

The adapters below do not catch their own errors — `raise_for_status()` is left to raise.
Partial failure is decided in one place, the aggregator:

```python
async def gather_dashboard(client, adapters):
    results = await asyncio.gather(
        *(adapter.fetch(client) for adapter in adapters),
        return_exceptions=True,
    )
    ...
```

`return_exceptions=True` turns a raised exception into a value in the results list instead
of cancelling the other, still-running calls. A failed adapter contributes no items and one
`UpstreamHealth` entry marked unavailable; the response still carries HTTP 200.

The cost: a monitoring setup checking only status codes will not notice an upstream down for
a week if every response stays a 200. `upstream_health` gives something — the frontend, a
synthetic check — somewhere to look. A graceful degrade nobody watches just hides an outage
more slowly.

### Caching and aggregation cost

Each dashboard load can mean several sequential requests per adapter once pagination is
accounted for, multiplied by however many people have it open. That multiplier argues for
caching at the aggregation boundary rather than per upstream: it sees total request volume,
and one cached response there serves every viewer.

A single BFF instance can hold this in process — a dictionary keyed by nothing, since there
is one dashboard, refreshed on a short TTL with an `asyncio.Lock` around the refresh so
concurrent misses do not all hit every upstream at once. That is enough for one instance.
Behind more than one process it stops being coherent — instances degrade and recover
independently — and a shared cache becomes necessary, worth adding only once a second
instance is actually planned.

### Testing adapters in isolation

`httpx.MockTransport` intercepts requests before they reach a socket and hands back whatever
response a plain function decides. A test never touches a network or the real upstream's
availability — it only has to construct the JSON that upstream returns and check what the
adapter turns it into. `respx` is worth reaching for once a test needs to stub several
distinct URLs with different behaviour per call; for one endpoint per adapter, a plain
function is one less dependency for the same result.

The aggregator is tested the same way, one level up: `dependency_overrides` replaces the
shared `httpx.AsyncClient` with one wired to a mock transport, and the request goes in
through `ASGITransport` — in process, no network — exercising the full path.

## The solution

Python 3.11–3.13 (the pinned `pydantic-core` has no wheel for newer interpreters yet):

```ini
# requirements.txt
fastapi==0.115.0
httpx==0.27.2
pydantic==2.9.2
uvicorn==0.30.6
pytest==8.3.3
pytest-asyncio==0.24.0
```

```python
# app/models.py
from datetime import datetime
from enum import Enum

from pydantic import BaseModel


class WorkItemSource(str, Enum):
    ticketing = "ticketing"
    ci = "ci"


class WorkItemStatus(str, Enum):
    open = "open"
    in_progress = "in_progress"
    done = "done"
    failed = "failed"


class WorkItem(BaseModel):
    id: str
    source: WorkItemSource
    title: str
    status: WorkItemStatus
    url: str
    updated_at: datetime


class UpstreamHealth(BaseModel):
    source: WorkItemSource
    available: bool
    error: str | None = None


class DashboardResponse(BaseModel):
    items: list[WorkItem]
    upstream_health: list[UpstreamHealth]
```

```python
# app/adapters/base.py
from abc import ABC, abstractmethod

import httpx

from app.models import WorkItem, WorkItemSource


class UpstreamAdapter(ABC):
    source: WorkItemSource

    @abstractmethod
    async def fetch(self, client: httpx.AsyncClient) -> list[WorkItem]:
        raise NotImplementedError
```

```python
# app/adapters/tickets.py
from datetime import datetime

import httpx

from app.adapters.base import UpstreamAdapter
from app.models import WorkItem, WorkItemSource, WorkItemStatus

_STATUS_MAP = {
    "open": WorkItemStatus.open,
    "in-progress": WorkItemStatus.in_progress,
    "resolved": WorkItemStatus.done,
    "closed": WorkItemStatus.done,
}


class TicketingAdapter(UpstreamAdapter):
    source = WorkItemSource.ticketing

    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url
        self.api_key = api_key

    async def fetch(self, client: httpx.AsyncClient) -> list[WorkItem]:
        items: list[WorkItem] = []
        page = 1
        while True:
            response = await client.get(
                f"{self.base_url}/issues",
                params={"page": page, "per_page": 50, "api_key": self.api_key},
            )
            response.raise_for_status()
            payload = response.json()
            for raw in payload["results"]:
                items.append(
                    WorkItem(
                        id=f"ticket-{raw['key']}",
                        source=self.source,
                        title=raw["summary"],
                        status=_STATUS_MAP.get(raw["state"], WorkItemStatus.open),
                        url=raw["self_url"],
                        updated_at=datetime.fromisoformat(raw["updated"]),
                    )
                )
            if not payload.get("has_more"):
                break
            page += 1
        return items
```

```python
# app/adapters/ci.py
from datetime import datetime, timezone

import httpx

from app.adapters.base import UpstreamAdapter
from app.models import WorkItem, WorkItemSource, WorkItemStatus

_RESULT_MAP = {
    "running": WorkItemStatus.in_progress,
    "passed": WorkItemStatus.done,
    "failed": WorkItemStatus.failed,
}


class CIAdapter(UpstreamAdapter):
    source = WorkItemSource.ci

    def __init__(self, base_url: str, token: str):
        self.base_url = base_url
        self.token = token

    async def fetch(self, client: httpx.AsyncClient) -> list[WorkItem]:
        items: list[WorkItem] = []
        cursor: str | None = None
        while True:
            params = {"cursor": cursor} if cursor else {}
            response = await client.get(
                f"{self.base_url}/pipelines",
                params=params,
                headers={"Authorization": f"Bearer {self.token}"},
            )
            response.raise_for_status()
            payload = response.json()
            for raw in payload["pipelines"]:
                items.append(
                    WorkItem(
                        id=f"ci-{raw['id']}",
                        source=self.source,
                        title=raw["name"],
                        status=_RESULT_MAP.get(raw["result"], WorkItemStatus.in_progress),
                        url=raw["link"],
                        updated_at=datetime.fromtimestamp(
                            raw["finished_at"] / 1000, tz=timezone.utc
                        ),
                    )
                )
            cursor = payload.get("next_cursor")
            if not cursor:
                break
        return items
```

```python
# app/aggregator.py
import asyncio

import httpx

from app.adapters.base import UpstreamAdapter
from app.models import DashboardResponse, UpstreamHealth, WorkItem


async def gather_dashboard(
    client: httpx.AsyncClient, adapters: list[UpstreamAdapter]
) -> DashboardResponse:
    results = await asyncio.gather(
        *(adapter.fetch(client) for adapter in adapters),
        return_exceptions=True,
    )

    items: list[WorkItem] = []
    health: list[UpstreamHealth] = []
    for adapter, result in zip(adapters, results):
        if isinstance(result, Exception):
            health.append(
                UpstreamHealth(source=adapter.source, available=False, error=str(result))
            )
            continue
        items.extend(result)
        health.append(UpstreamHealth(source=adapter.source, available=True))

    items.sort(key=lambda item: item.updated_at, reverse=True)
    return DashboardResponse(items=items, upstream_health=health)
```

```python
# app/main.py
from contextlib import asynccontextmanager

import httpx
from fastapi import Depends, FastAPI, Request

from app.adapters.base import UpstreamAdapter
from app.adapters.ci import CIAdapter
from app.adapters.tickets import TicketingAdapter
from app.aggregator import gather_dashboard
from app.models import DashboardResponse


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with httpx.AsyncClient(timeout=5.0) as client:
        app.state.http_client = client
        yield


app = FastAPI(lifespan=lifespan)


def get_http_client(request: Request) -> httpx.AsyncClient:
    return request.app.state.http_client


def get_adapters() -> list[UpstreamAdapter]:
    return [
        TicketingAdapter(
            base_url="https://tickets.example.com/api/v1", api_key="placeholder"
        ),
        CIAdapter(base_url="https://ci.example.com/api/v1", token="placeholder"),
    ]


@app.get("/dashboard", response_model=DashboardResponse)
async def dashboard(
    client: httpx.AsyncClient = Depends(get_http_client),
    adapters: list[UpstreamAdapter] = Depends(get_adapters),
) -> DashboardResponse:
    return await gather_dashboard(client, adapters)
```

```python
# tests/test_dashboard.py
import httpx
import pytest

from app.main import app, get_http_client


def _mock_handler(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/api/v1/issues":
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "key": "OPS-104",
                        "summary": "Renew TLS certificate",
                        "state": "open",
                        "self_url": "https://tickets.example.com/OPS-104",
                        "updated": "2026-01-10T09:00:00+00:00",
                    }
                ],
                "has_more": False,
            },
        )
    if request.url.path == "/api/v1/pipelines":
        return httpx.Response(
            200,
            json={
                "pipelines": [
                    {
                        "id": 42,
                        "name": "deploy-staging",
                        "result": "failed",
                        "link": "https://ci.example.com/runs/42",
                        "finished_at": 1768124400000,
                    }
                ],
                "next_cursor": None,
            },
        )
    return httpx.Response(404)


async def _call_dashboard(handler) -> httpx.Response:
    app.dependency_overrides.clear()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as mock_upstream:
        app.dependency_overrides[get_http_client] = lambda: mock_upstream
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
            return await ac.get("/dashboard")


@pytest.mark.asyncio
async def test_dashboard_normalises_both_upstreams():
    response = await _call_dashboard(_mock_handler)

    assert response.status_code == 200
    body = response.json()
    assert {item["source"] for item in body["items"]} == {"ticketing", "ci"}
    assert {item["status"] for item in body["items"]} == {"open", "failed"}
    assert all(h["available"] for h in body["upstream_health"])


@pytest.mark.asyncio
async def test_dashboard_degrades_when_one_upstream_is_down():
    def flaky_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/pipelines":
            return httpx.Response(503, json={"error_code": "unavailable"})
        return _mock_handler(request)

    response = await _call_dashboard(flaky_handler)

    assert response.status_code == 200
    body = response.json()
    assert [item["source"] for item in body["items"]] == ["ticketing"]
    health = {h["source"]: h["available"] for h in body["upstream_health"]}
    assert health == {"ticketing": True, "ci": False}
```

Run it:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pytest -v
```

Both tests pass:

```
tests/test_dashboard.py::test_dashboard_normalises_both_upstreams PASSED
tests/test_dashboard.py::test_dashboard_degrades_when_one_upstream_is_down PASSED
```

Neither test opens a socket. The second is the interesting one: CI returns a 503, and the
dashboard still returns 200 with the ticketing item present and CI marked unavailable — the
decision the naive proxy at the top of this article had nowhere to put.

## Conclusion

A backend-for-frontend earns its place once the frontend's job stops being "render what the
upstream sent" and becomes "render one coherent view built from upstreams that agree on
nothing but the concept." A few things about it generalise past this dashboard:

**The schema is the product, not the plumbing.** Deciding `WorkItemStatus` has exactly four
values is made once, by someone who sees every upstream's vocabulary at once — not wherever
a frontend engineer next renders a badge.

**Partial failure belongs in the response type, not a try/except an adapter author has to
remember.** `UpstreamHealth` turns "is everything working" from an implicit status code into
a field a caller, and an alert, can check.

**Caching lives where the multiplier lives.** The aggregation endpoint sees total request
volume, a better place to absorb load than duplicating rate-limit handling per adapter.

**Adapters are commodity code because the contract is narrow.** One method, one return type,
tested against a crafted response rather than a live system — what makes a fifth backend a
small change rather than a rewrite of the aggregator.
