---
layout: post
title: "Self-Hosted LLM Inference: Serving, Benchmarking and Agent Guardrails"
subtitle: "Sending every prompt to someone else's GPU is a decision, not a default."
date: 2026-08-07 09:00:00 +0200
tags: [llm, agents, testing, security]
description: >-
  Running models on hardware you own removes a category of decision about
  where debugging context goes, at the cost of a real gap on the hardest
  reasoning tasks, and neither fact is worth much without a way to measure it.
  This covers serving, a repeatable benchmark harness that replaces "it feels
  smarter" with a number, and the guardrails that have to exist before an
  agent is allowed near anything that changes state, with runnable code for
  all three.
---

## The problem

Every prompt sent to a hosted model is a copy of data leaving the building. For a lot of
work that is entirely fine. For infrastructure work — where the context worth pasting is
config, logs, topology, sometimes credentials — it deserves a decision rather than a
default, and most setups never make the decision at all; they just default to whatever
editor plugin was easiest to install.

The second problem sits underneath the first. Even once inference runs locally, "is this
model actually any good" gets answered by vibes: try a new model, ask it a few things, form
an impression, swap. That impression is shaped by the last thing you happened to ask, by
prompt phrasing, and by wanting the new thing to be better. It produces confident opinions
that do not survive being written down as a number.

The third problem is sharper. An agent that can read logs, query metrics and propose
changes is genuinely useful. One that can *apply* changes is a different risk category, and
the failure mode that matters is not usually the agent doing something destructive — that
is what approval gates are for. It is an agent *reporting* something as fixed, deployed or
passing without having verified it, because an agent's confidence is uncorrelated with its
correctness.

## Working through it

### Why own the hardware, honestly

Data locality is the strongest argument — debugging context is exactly the material worth
keeping in the building. Hosted inference is also priced per token, which is a strange
incentive: it makes people ration a tool that gets better the more they use it, where a GPU
is a fixed cost and marginal use is free. Local inference also keeps working when the
internet does not, which is precisely when debugging is happening.

The honest counterweight: the best hosted models are better than what runs locally, and the
gap on hard reasoning is real. Local models are excellent at the bulk of practical work —
summarising, transforming, structured extraction, drafting code that gets reviewed anyway
— and noticeably weaker at genuinely difficult problems. Use both, and know which is which,
rather than pretending the gap does not exist.

### Serving: three things matter more than the choice of server

**Model swapping**, so a fixed allocation of VRAM behaves like a pool instead of a
per-model reservation — loading on demand and evicting what is idle costs a cold-start
delay and is almost always the right trade. **An OpenAI-compatible API**, not because that
API is well designed but because everything already speaks it — adopting it means every
existing tool works against owned hardware with a changed base URL and nothing else.
**Quantisation**, which is usually the actual lever between a model that does not fit and
one that fits comfortably, at a quality cost far smaller than expected at sensible levels.

### Replacing "it feels smarter" with a number

A benchmark only has to be honest about one thing: it should be *your* tasks, not an
academic leaderboard, because the leaderboard measures something real but not necessarily
the thing being relied on day to day. Run a fixed set of representative tasks against every
served model, the same way, every time, and the benchmark answers a question impressions
cannot: did that upgrade actually help, and by how much. It also produces genuinely
surprising results — a quantisation step assumed to be lossy that costs nothing measurable,
a widely recommended model that is worse at the actual job than a smaller one.

### The rules for letting an agent near infrastructure

Read-only by default, and generously so — most of what makes infrastructure work slow is
gathering context, not typing the fix, and an agent that only reads is already worth
having. Dry run before apply, always, with the dry-run output being the thing a human
reads, not the agent's summary of it. A human approves anything that changes state — not a
rubber stamp, the actual evidence, read, then approved. Never on a base branch, never
without a trail, so agent work goes through the same review a person's work would. And
evidence over assertion: a claim without the command output behind it is worth nothing, and
worse than nothing if it is believed — "I could not verify this" is a better answer than a
plausible falsehood.

## The solution

### A repeatable benchmark against a locally served model

```yaml
# docker-compose.yml
services:
  ollama:
    image: ollama/ollama:0.4.0
    volumes:
      - ollama-data:/root/.ollama
    ports:
      - "11434:11434"
volumes:
  ollama-data:
```

```bash
docker compose up -d
docker compose exec ollama ollama pull qwen2.5:0.5b
```

```python
#!/usr/bin/env python3
# benchmark.py
"""Score a served model against a fixed set of tasks, repeatably - so 'did
that upgrade help' has an answer with evidence instead of an impression."""
import json
import sys
import time
import urllib.request

ENDPOINT = "http://localhost:11434/v1/chat/completions"

TASKS = [
    {
        "id": "extract-port",
        "prompt": "What port does the config line 'listen 8443 ssl;' bind? "
                  "Answer with only the number.",
    },
    {
        "id": "summarise",
        "prompt": "Summarise in one sentence: a full OSD stops writes "
                  "cluster-wide, not just on that disk.",
    },
    {
        "id": "classify",
        "prompt": "Is 'systemctl restart nginx' a read-only or a "
                  "state-changing command? Answer with one word.",
    },
]


def run_task(model: str, task: dict) -> dict:
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": task["prompt"]}],
        "temperature": 0,
    }
    req = urllib.request.Request(
        ENDPOINT,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    start = time.monotonic()
    with urllib.request.urlopen(req, timeout=60) as resp:
        result = json.load(resp)
    elapsed = time.monotonic() - start
    answer = result["choices"][0]["message"]["content"].strip()
    return {"id": task["id"], "seconds": round(elapsed, 2), "answer": answer}


def main() -> None:
    model = sys.argv[1] if len(sys.argv) > 1 else "qwen2.5:0.5b"
    results = [run_task(model, task) for task in TASKS]
    print(json.dumps({"model": model, "results": results}, indent=2))


if __name__ == "__main__":
    main()
```

```bash
python3 benchmark.py qwen2.5:0.5b
```
```json
{
  "model": "qwen2.5:0.5b",
  "results": [
    { "id": "extract-port", "seconds": 0.41, "answer": "8443" },
    { "id": "summarise", "seconds": 0.63, "answer": "A full OSD blocks writes across the whole cluster, not just the affected disk." },
    { "id": "classify", "seconds": 0.29, "answer": "State-changing" }
  ]
}
```

Pull a second model and run the same script against it to compare — the same three tasks,
the same prompts, the same measurement, which is the entire point.

### A minimal guardrail shape for an agent

```python
#!/usr/bin/env python3
# agent.py
"""Minimal guardrail shape: an agent can always read, must always show a
dry run, and can never apply anything without a separate, explicit
approval."""
import argparse
import subprocess

READ_ONLY_PREFIXES = ("cat ", "systemctl status", "journalctl", "docker ps", "docker logs")


class NotApproved(Exception):
    pass


def is_read_only(command: str) -> bool:
    return command.strip().startswith(READ_ONLY_PREFIXES)


def dry_run(command: str) -> str:
    return f"[dry-run] would execute: {command}"


def apply(command: str, approved: bool) -> str:
    if not approved:
        raise NotApproved(f"refusing to run '{command}' without --approve")
    result = subprocess.run(command, shell=True, capture_output=True, text=True, check=False)
    return result.stdout + result.stderr


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command")
    parser.add_argument("--approve", action="store_true")
    args = parser.parse_args()

    if is_read_only(args.command):
        print(subprocess.run(args.command, shell=True, capture_output=True, text=True).stdout)
        return 0

    print(dry_run(args.command))
    try:
        print(apply(args.command, args.approve))
    except NotApproved as exc:
        print(f"BLOCKED: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

```python
# test_agent.py
import pytest
from agent import is_read_only, apply, NotApproved


def test_read_only_commands_are_recognised():
    assert is_read_only("docker ps -a")
    assert not is_read_only("systemctl restart nginx")


def test_state_changing_command_without_approval_is_blocked():
    with pytest.raises(NotApproved):
        apply("systemctl restart nginx", approved=False)


def test_state_changing_command_with_approval_runs():
    assert "applied" in apply("echo applied", approved=True)
```

```bash
pytest test_agent.py
# 3 passed

python3 agent.py "systemctl restart nginx"
# [dry-run] would execute: systemctl restart nginx
# BLOCKED: refusing to run 'systemctl restart nginx' without --approve

python3 agent.py "systemctl restart nginx" --approve
# [dry-run] would execute: systemctl restart nginx
# (command output)
```

This is deliberately narrow. The workflow that actually holds up in practice is narrower
still: work is handed to an agent as an issue carrying an explicit label, applied by a
person, and the result comes back as a pull request reviewed like anyone else's — nothing
is ever picked up because an agent decided it was in scope, and removing the label takes
the work back.

## Conclusion

**A benchmark you did not build yourself is measuring someone else's workload, not
yours.** Academic scores and vendor leaderboards are real signals about something; they are
not a substitute for a fixed set of your own tasks, scored the same way every time.

**The riskiest failure of an agent is a false claim, not a destructive action.**
Destructive actions are what approval gates exist for. A confident, unverified "this is
fixed" is the one that gets through, because nothing was there to check it.

**The label is the whole boundary, and that is a feature.** A very small, explicit
mechanism — an issue tagged, a branch, a pull request, a human review — is what keeps a
system built around a model debuggable, because when something goes wrong it is nearly
always the issue or the harness, and both are things a person can read.
