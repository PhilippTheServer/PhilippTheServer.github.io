---
layout: post
title: "Serving Several Local Models on One GPU with On-Demand Loading"
subtitle: "A config-driven proxy that starts, health-checks and idles out model processes so one GPU can serve several models."
date: 2026-01-20 09:00:00 +0200
tags: [llm, performance, architecture]
description: >-
  One GPU can hold one large model at a time, but different tasks want
  different models and restarting a server by hand does not scale past a
  handful of requests. This describes a proxy that loads a model on first
  request, keeps it warm, and swaps it out for the next one automatically.
---

## The problem

A single GPU has a fixed amount of VRAM, and a loaded model occupies most of it for as
long as the serving process is alive. That is fine when there is exactly one model and one
workload. It stops being fine the moment there are two: a general chat model for one task
and a smaller, fine-tuned model for another, or a large model for quality-sensitive work
and a fast one for anything latency-sensitive. Both cannot be resident at once on hardware
sized for one.

The naive answer is to run one model, and when a request needs the other, stop the server,
start the other one, and route the request. Doing that by hand does not survive contact
with more than a couple of requests a day: someone has to notice which model is needed,
kill the right process, wait for the new one to finish loading — which for a large model
can take a noticeable amount of time — and only then send the request. Automate the
restarting badly and a new failure mode appears: two requests for two different models
arrive close together, both trigger a "start the model" action, and now two server
processes are racing for the same GPU memory, or the same TCP port.

What is needed is a thin, stateful layer in front of the actual model servers: something
that knows which model is currently loaded, starts the right one on demand, blocks
concurrent requests for a different model until the first one is ready, and shuts a model
down after it has sat idle for a while so the VRAM is free for whichever model is asked for
next.

## Working through it

### Separate "which model does this request want" from "is a server running for it"

The proxy's job is routing plus lifecycle, not inference. It needs, for each configured
model, a command that starts a normal OpenAI-compatible server process (llama.cpp's
`llama-server` is a common choice) bound to a local port, and a way to tell when that
process is actually ready to answer requests rather than merely started.

Readiness matters because "the process exists" and "the process can answer a chat
completion" are different things — a large model can take from several seconds to over a
minute to load its weights, and a request forwarded before that finishes just times out
against a process that is technically running.

### Serialise starts so two requests cannot race

If a request for model A arrives while nothing is loaded, the proxy starts A. If a second
request for model B arrives a moment later, the correct behaviour is not to also start B
immediately — model A hasn't finished loading yet and might not even be the model that
ends up serving anything if B's request is going to require swapping A back out anyway.
The proxy needs a lock: only one model transition (start, wait for health, or stop and
start a different one) happens at a time, and requests for the currently-loading model
wait for it, while requests for a different model wait for the transition to finish before
starting their own.

This is the part that is easy to get wrong by building it optimistically — start
whichever model each request asks for, in parallel — and then discovering under any real
concurrent load that two `llama-server` processes are fighting over the same GPU memory,
each partially loaded, both failing.

### Free memory when nothing is using it

An idle timeout per model — stop the process if no request has used it for some
configurable period — is what keeps VRAM available for whatever the next request needs,
without the proxy having to be told explicitly to unload anything. This is a simple
timestamp check on every request plus a background sweep, not something that needs its own
scheduler.

### This is a known, public pattern — llama-swap

This exact shape — a config file mapping model name to start command and port, health
checking, request queuing per model, idle-timeout unloading — is implemented by the
open-source project [llama-swap](https://github.com/mostlygeek/llama-swap), which sits in
front of llama.cpp's server binary. Using it, or something shaped like it, is preferable to
writing this from scratch for anything beyond a demonstration: process supervision and
graceful shutdown have enough edge cases (a model that hangs on exit, a health check that
needs a longer grace period than usual) that a maintained tool is worth the dependency. The
example below is a runnable llama-swap configuration, followed by a small self-contained
version of the same pattern for anyone who wants to see the mechanism without installing
anything extra.

## The solution

### A working llama-swap configuration

```yaml
# config.yaml — llama-swap configuration.
# Run with: llama-swap --config config.yaml --listen 127.0.0.1:8080
# (llama-swap and llama-server binaries must be on PATH; models are local GGUF files.)

healthCheckTimeout: 120

models:
  "example-7b-instruct":
    cmd: >
      llama-server
      --model models/example-7b-instruct.Q4_K_M.gguf
      --port ${PORT}
      --ctx-size 8192
    ttl: 300

  "example-3b-fast":
    cmd: >
      llama-server
      --model models/example-3b-instruct.Q4_K_M.gguf
      --port ${PORT}
      --ctx-size 4096
    ttl: 300
```

`${PORT}` is assigned by llama-swap per model so two configured models never collide, `ttl`
is the idle-unload timeout in seconds, and `healthCheckTimeout` is how long llama-swap
waits for a freshly started model to answer before giving up. Requesting either model name
against `/v1/chat/completions` on the proxy's listen address triggers a load if that model
is not already running:

```bash
curl -s http://127.0.0.1:8080/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model": "example-7b-instruct", "messages": [{"role": "user", "content": "hi"}]}'
```

The first call for a given model pays the load time; a second call shortly afterwards, for
the same model, is fast because the process is already warm. A call for the other model
name triggers a swap: the first process is stopped (or left running if `ttl` allows more
than one to fit) and the second is started.

### The mechanism, minimally, in plain Python

For anyone who wants to see exactly what is happening without depending on an external
project, this is the same pattern — start-on-demand, one transition at a time, idle
timeout — reduced to under a hundred lines, proxying to two placeholder backend commands
instead of `llama-server`:

```python
#!/usr/bin/env python3
"""minimal_swap.py — on-demand process swapping proxy, illustrative only.

Run: python minimal_swap.py
Then: curl http://127.0.0.1:8000/v1/chat/completions \
        -d '{"model": "model-a", "messages": []}'
"""
import asyncio
import subprocess
import time

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

MODELS = {
    "model-a": {"cmd": ["python", "-m", "http.server", "9001"], "port": 9001},
    "model-b": {"cmd": ["python", "-m", "http.server", "9002"], "port": 9002},
}
IDLE_TIMEOUT_S = 300

app = FastAPI()
state = {"current": None, "process": None, "last_used": 0.0}
lock = asyncio.Lock()


async def wait_healthy(port: int, timeout_s: float = 30.0) -> None:
    deadline = time.monotonic() + timeout_s
    async with httpx.AsyncClient() as client:
        while time.monotonic() < deadline:
            try:
                r = await client.get(f"http://127.0.0.1:{port}/", timeout=1.0)
                if r.status_code < 500:
                    return
            except httpx.TransportError:
                pass
            await asyncio.sleep(0.2)
    raise TimeoutError(f"model on port {port} did not become healthy in time")


async def ensure_loaded(model: str) -> int:
    async with lock:
        if state["current"] == model and state["process"] and state["process"].poll() is None:
            state["last_used"] = time.monotonic()
            return MODELS[model]["port"]

        if state["process"] is not None:
            state["process"].terminate()
            state["process"].wait(timeout=10)

        spec = MODELS[model]
        state["process"] = subprocess.Popen(spec["cmd"])
        state["current"] = model
        await wait_healthy(spec["port"])
        state["last_used"] = time.monotonic()
        return spec["port"]


@app.post("/v1/chat/completions")
async def chat_completions(request: Request) -> JSONResponse:
    body = await request.json()
    model = body.get("model")
    if model not in MODELS:
        raise HTTPException(status_code=404, detail=f"unknown model: {model}")

    port = await ensure_loaded(model)
    return JSONResponse({"routed_to_port": port, "model": model})


async def idle_reaper() -> None:
    while True:
        await asyncio.sleep(10)
        async with lock:
            if state["process"] and time.monotonic() - state["last_used"] > IDLE_TIMEOUT_S:
                state["process"].terminate()
                state["process"] = None
                state["current"] = None


@app.on_event("startup")
async def on_startup() -> None:
    asyncio.create_task(idle_reaper())
```

```bash
pip install fastapi 'uvicorn[standard]' httpx
python -m uvicorn minimal_swap:app --port 8000 &

curl -s http://127.0.0.1:8000/v1/chat/completions \
  -d '{"model": "model-a", "messages": []}'
# {"routed_to_port":9001,"model":"model-a"}

curl -s http://127.0.0.1:8000/v1/chat/completions \
  -d '{"model": "model-b", "messages": []}'
# model-a's http.server is stopped, model-b's is started and health-checked
# {"routed_to_port":9002,"model":"model-b"}
```

This version proxies nothing back to the client and uses `http.server` as a stand-in
backend — the point is the lifecycle, not the HTTP forwarding, which is a straightforward
`httpx` passthrough once the correct port is known.

## Conclusion

**A shared resource with mutually exclusive occupants needs a lock around every
transition, not just around the resource itself.** VRAM is the constrained resource here,
but the bug that actually bites is two starts racing, which is a concurrency problem, not
a capacity problem — the fix is a lock, not more memory.

**Readiness is not the same as "the process started".** Any process with meaningful
startup work — loading weights, opening a database, warming a cache — needs a real health
check before it is used, and skipping that check is invisible right up until the first
concurrent request hits a process that isn't ready yet.

**Reach for a maintained tool once the shape is a known one.** The minimal Python version
above is worth understanding, but running it in place of llama-swap in anything other than
a demonstration means re-solving process supervision, log capture and graceful shutdown
that the existing project has already handled.
