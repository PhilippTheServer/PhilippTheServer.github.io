---
layout: post
title: "Why a Leaderboard Score Does Not Predict Your Workload"
subtitle: "Building a small, deterministic, task-specific eval instead of trusting a general ranking."
date: 2026-01-30 09:00:00 +0200
tags: [testing, llm]
description: >-
  A public leaderboard score describes performance on a broad, general set of
  tasks that has little in common with a narrow production workload such as
  calling a fixed set of tools with a strict schema. This walks through
  building a small task-specific evaluation and running candidate models
  against it instead.
---

## The problem

Public leaderboards — general knowledge benchmarks, aggregated chat-preference rankings —
answer a real question, but not the question that matters when choosing a model for a
specific production task. They measure broad competence across a wide distribution of
prompts written by other people for other purposes. A production workload is usually
narrow: a fixed small set of tools it must call with the right arguments in a strict JSON
schema, a specific document length it has to handle without degrading, a house style it
has to follow, or a narrow domain it has to stay inside without wandering into plausible
but wrong territory.

These are not the same axis, and a model can be strong on one and weak on the other in
either direction. A model near the top of a general leaderboard can still fail to produce
valid structured output for your particular schema, because "structured output" was
lightly represented in whatever the leaderboard measured. A smaller, cheaper model can
comfortably beat a much larger one on your narrow task, because it was fine-tuned toward
exactly that kind of structured behaviour, or because your task simply doesn't touch the
kind of broad general reasoning where the larger model's advantage lives.

The trap is that a leaderboard score is easy to obtain and feels like due diligence — a
citable number, produced by someone else, on a well-known benchmark. It gives a false sense
of having evaluated the choice, without ever running the candidate against anything close
to what it will actually be asked to do.

## Working through it

### Decide what a pass actually looks like, before picking cases

A general benchmark scores loosely because it has to generalise across many kinds of task.
A task-specific eval can score strictly, because you know exactly what correct looks like:
a response that validates against a schema, a tool call with the right name and arguments,
an answer that contains a specific fact given a specific document. Writing this scoring
rule down before collecting cases keeps it honest — it stops the scoring criteria from
being adjusted after the fact to make a preferred model look better.

### Collect real cases, or realistic ones, not hypothetical edge cases only

Twenty to fifty representative cases, drawn from real usage where it exists (anonymised
and stripped of anything sensitive) or written as realistic synthetic examples where it
does not, cover the workload far better than a handful of cases designed to be maximally
tricky. The goal is a set that looks like what the system will actually see, including the
boring, common cases — a model that only gets tested on adversarial inputs is being
evaluated on a different distribution than the one it will run against.

### Prefer a deterministic scorer over an LLM judge, at least as the primary signal

An LLM-as-judge approach is flexible but adds its own noise and its own cost to every run
of the eval, and its judgement can drift between the judge model's versions in ways that
are hard to distinguish from a real change in the candidate. For a task with a checkable
structure — a JSON schema, a specific expected field, a specific tool name and argument
set — a deterministic scoring function (schema validation, exact or near-match field
checks) is free to run, reproducible, and does not need its own evaluation to trust.

### Run every candidate against the exact same set

The comparison is only meaningful if every candidate model sees the same prompts, the same
scoring function, and the same decoding parameters. This is the same discipline as any
other controlled comparison: change one variable — the model — and hold everything else
fixed, including the harness that runs it.

## The solution

A small evaluation harness: a JSON file of test cases, a deterministic scorer using
`pydantic` for schema checks, and a runner that scores any OpenAI-compatible endpoint.

```json
[
  {
    "id": "lookup-basic",
    "prompt": "Look up ticket number 1042 and tell me its status as JSON: {\"ticket_id\": int, \"status\": str}",
    "expected_schema": {"ticket_id": "int", "status": "str"},
    "required_fields": {"ticket_id": 1042}
  },
  {
    "id": "lookup-missing-field",
    "prompt": "Return the priority of ticket 77 as JSON: {\"ticket_id\": int, \"priority\": str}",
    "expected_schema": {"ticket_id": "int", "priority": "str"},
    "required_fields": {"ticket_id": 77}
  }
]
```

```python
#!/usr/bin/env python3
"""task_eval.py — score a model on a fixed, task-specific case set.

Usage:
    python task_eval.py --base-url http://127.0.0.1:9000/v1 \
        --model example-model --cases cases.json
"""
import argparse
import json
import re

import httpx

TYPE_CHECKS = {"int": int, "str": str, "float": float, "bool": bool}


def extract_json(text: str) -> dict:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError("no JSON object found in completion")
    return json.loads(match.group(0))


def score_case(completion: str, case: dict) -> tuple:
    """Returns (passed: bool, reason: str)."""
    try:
        obj = extract_json(completion)
    except ValueError as exc:
        return False, f"invalid or missing JSON: {exc}"

    for field, type_name in case["expected_schema"].items():
        if field not in obj:
            return False, f"missing field: {field}"
        expected_type = TYPE_CHECKS[type_name]
        if not isinstance(obj[field], expected_type):
            return False, f"field {field} has wrong type: expected {type_name}, got {type(obj[field]).__name__}"

    for field, expected_value in case.get("required_fields", {}).items():
        if obj.get(field) != expected_value:
            return False, f"field {field} mismatch: expected {expected_value!r}, got {obj.get(field)!r}"

    return True, "ok"


def run(base_url: str, model: str, cases: list) -> list:
    results = []
    with httpx.Client() as client:
        for case in cases:
            resp = client.post(
                f"{base_url}/chat/completions",
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": case["prompt"]}],
                    "temperature": 0,
                },
                timeout=60,
            )
            resp.raise_for_status()
            completion = resp.json()["choices"][0]["message"]["content"]
            passed, reason = score_case(completion, case)
            results.append({"id": case["id"], "passed": passed, "reason": reason})
    return results


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--base-url", required=True)
    p.add_argument("--model", required=True)
    p.add_argument("--cases", required=True)
    args = p.parse_args()

    with open(args.cases) as f:
        cases = json.load(f)

    results = run(args.base_url, args.model, cases)
    passed = sum(1 for r in results if r["passed"])
    for r in results:
        status = "PASS" if r["passed"] else "FAIL"
        print(f"[{status}] {r['id']}: {r['reason']}")
    print(f"\n{args.model}: {passed}/{len(results)} passed")


if __name__ == "__main__":
    main()
```

A minimal fake backend so the harness is runnable without any real model, and to make the
example self-contained:

```python
# fake_backend.py
from fastapi import FastAPI

app = FastAPI()


@app.post("/v1/chat/completions")
async def chat_completions(payload: dict):
    prompt = payload["messages"][-1]["content"]
    if "1042" in prompt:
        content = '{"ticket_id": 1042, "status": "open"}'
    elif "77" in prompt:
        # Deliberately wrong type, to show the harness catching it.
        content = '{"ticket_id": "77", "priority": "high"}'
    else:
        content = "{}"
    return {"choices": [{"message": {"role": "assistant", "content": content}}]}
```

```bash
pip install fastapi 'uvicorn[standard]' httpx
python -m uvicorn fake_backend:app --port 9000 &

python task_eval.py --base-url http://127.0.0.1:9000/v1 \
    --model fake-model --cases cases.json
```

```
[PASS] lookup-basic: ok
[FAIL] lookup-missing-field: field ticket_id has wrong type: expected int, got str

fake-model: 1/2 passed
```

Running the same command with `--base-url` pointed at a different candidate server (a
different model, a different quantisation, a different version) and comparing the pass
counts is the actual comparison this article is arguing for — a number produced by your
own task, not someone else's benchmark.

## Conclusion

**A leaderboard measures a distribution of tasks that is not your distribution.** Treat a
public score as a rough prior about general capability, not as evidence about a specific
narrow task — the correlation between the two is real but weak, and weak correlations are
a poor basis for a production decision.

**A deterministic, schema-based scorer is worth building even for a small eval.** It costs
nothing to run, gives the same answer every time given the same completion, and does not
need to be trusted the way an LLM-judge score does.

**Twenty to fifty realistic cases, scored strictly, beat a large generic benchmark scored
loosely — for the question you are actually asking.** The size of an eval matters less than
whether it looks like the thing it is meant to predict.
