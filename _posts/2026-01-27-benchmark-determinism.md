---
layout: post
title: "Making a Benchmark Deterministic Against a Server Tuned for Interactive Use"
subtitle: "Pinning decoding parameters and treating the result as a distribution, because bit-identical output is not always available."
date: 2026-01-27 09:00:00 +0200
tags: [testing, llm]
description: >-
  A model server tuned for a pleasant chat experience samples its output, so
  the same prompt run twice gives two different answers and a benchmark score
  moves for reasons that have nothing to do with a regression. This shows how
  to pin what can be pinned and measure variance for what cannot.
---

## The problem

A default model server configuration is tuned for people, not for comparison. A non-zero
temperature and top-p/top-k sampling make chat responses feel varied and natural rather
than repetitive, which is the right choice for a chat product and the wrong one for a
benchmark. Run the same prompt against the same model twice with those defaults and you
get two different completions — sometimes close in meaning, sometimes not — and a scoring
function that grades the completion will produce two different scores.

That variance is invisible until you actually need to compare two things: a new model
version against the old one, a changed system prompt against the previous one, or a
different serving configuration against the current one. Score both, see a change, and
have no way to tell whether it is a real regression or sampling noise that would have
appeared even comparing the same model against itself. The natural instinct — run it again
to check — does not resolve this, because "again" is also a sample, and now you have three
numbers instead of two, none of them a ground truth.

Fixing decoding parameters removes most of this noise, but not always all of it. Some
serving stacks batch requests together for throughput, and floating-point reduction order
can differ depending on what else happens to be in the batch at the same time — meaning
that even at temperature zero, output is not always guaranteed to be bit-identical across
runs on some backends. The right response to that is not to give up on reproducibility; it
is to be explicit about which layer of determinism you actually have, and to treat the
benchmark as a statistical comparison rather than an equality check where full determinism
is not available.

## Working through it

### Pin every parameter that controls sampling

Temperature at zero (or as close to it as the API allows) removes the deliberate
randomness. Where the backend exposes it, pinning `top_p` to 1 and `seed` to a fixed value
removes two more sources — a fixed seed matters most when temperature cannot be forced all
the way to zero, or for backends where a seed affects other stochastic elements of
generation. None of this is optional if the benchmark's purpose is comparison rather than
just "produce an answer": leaving any of these at chat-friendly defaults reintroduces the
exact noise the benchmark exists to control for.

### Record the version, not just the score

A score without the model version, backend version and exact decoding parameters that
produced it cannot be compared against a score from another day, because any of those three
could have changed independently of whatever you meant to test. This is unglamorous but
it is what makes a benchmark a time series rather than a one-off measurement — record it
alongside every run, not as an afterthought when a comparison turns out to matter.

### Run each case more than once, and report a distribution

Even with everything above pinned, some backends' continuous batching can perturb
floating-point results slightly depending on batch composition — a real, documented
effect, not a hypothetical one, though its exact magnitude depends on the backend and
should not be asserted as a specific number without measuring it on the system in
question. The practical response is to stop expecting a single number to be the truth.
Run each prompt N times, keep every result, and report both a central tendency and a
spread. A "regression" is then a shift big enough to be outside the spread you'd expect
from noise alone, not any change in the single most recent run.

### Decide the comparison rule before you need it

Deciding, after seeing two numbers, whether the difference "counts" is how confirmation
bias gets into a benchmark. Decide in advance: for example, a change counts as a
regression only if the new run's mean score falls outside the old run's observed range (or
some number of standard deviations from it), computed from the repeats you already
collected. It does not need to be a rigorous statistical test to be worth having — it
needs to be decided before the numbers exist.

## The solution

A small harness that fixes decoding parameters, runs each prompt N times against any
OpenAI-compatible chat completions endpoint, and reports mean, spread, and a simple
before/after comparison.

```python
#!/usr/bin/env python3
"""determinism_bench.py — repeat prompts against a fixed config, report variance.

Usage:
    python determinism_bench.py --base-url http://127.0.0.1:9000/v1 \
        --model example-model --repeats 5 --prompts prompts.json

prompts.json:
[
  {"id": "sum-1", "prompt": "What is 12 + 30?", "score_fn": "contains", "expect": "42"}
]
"""
import argparse
import json
import statistics
from dataclasses import dataclass

import httpx

FIXED_PARAMS = {"temperature": 0, "top_p": 1, "seed": 42}


def score_contains(completion: str, expect: str) -> float:
    return 1.0 if expect.strip() in completion else 0.0


SCORERS = {"contains": score_contains}


@dataclass
class CaseResult:
    case_id: str
    scores: list


def run_case(client: httpx.Client, base_url: str, model: str, case: dict, repeats: int) -> CaseResult:
    scores = []
    for _ in range(repeats):
        resp = client.post(
            f"{base_url}/chat/completions",
            json={
                "model": model,
                "messages": [{"role": "user", "content": case["prompt"]}],
                **FIXED_PARAMS,
            },
            timeout=60,
        )
        resp.raise_for_status()
        completion = resp.json()["choices"][0]["message"]["content"]
        scorer = SCORERS[case["score_fn"]]
        scores.append(scorer(completion, case["expect"]))
    return CaseResult(case_id=case["id"], scores=scores)


def summarise(result: CaseResult) -> dict:
    mean = statistics.mean(result.scores)
    stdev = statistics.pstdev(result.scores) if len(result.scores) > 1 else 0.0
    return {"case_id": result.case_id, "mean": mean, "stdev": stdev, "scores": result.scores}


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--base-url", required=True)
    p.add_argument("--model", required=True)
    p.add_argument("--prompts", required=True)
    p.add_argument("--repeats", type=int, default=5)
    args = p.parse_args()

    with open(args.prompts) as f:
        cases = json.load(f)

    with httpx.Client() as client:
        results = [run_case(client, args.base_url, args.model, c, args.repeats) for c in cases]

    for r in results:
        s = summarise(r)
        print(f"{s['case_id']}: mean={s['mean']:.2f} stdev={s['stdev']:.2f} scores={s['scores']}")


if __name__ == "__main__":
    main()
```

```json
[
  {"id": "arith-1", "prompt": "What is 12 + 30? Answer with just the number.", "score_fn": "contains", "expect": "42"},
  {"id": "arith-2", "prompt": "What is 9 * 6? Answer with just the number.", "score_fn": "contains", "expect": "54"}
]
```

A minimal fake backend to run this against without any GPU or hosted API:

```python
# fake_backend.py
from fastapi import FastAPI

app = FastAPI()


@app.post("/v1/chat/completions")
async def chat_completions(payload: dict):
    prompt = payload["messages"][-1]["content"]
    answer = "42" if "12 + 30" in prompt else "54" if "9 * 6" in prompt else "unknown"
    return {"choices": [{"message": {"role": "assistant", "content": answer}}]}
```

```bash
pip install fastapi 'uvicorn[standard]' httpx
python -m uvicorn fake_backend:app --port 9000 &

python determinism_bench.py --base-url http://127.0.0.1:9000/v1 \
    --model fake-model --repeats 5 --prompts prompts.json
```

```
arith-1: mean=1.00 stdev=0.00 scores=[1.0, 1.0, 1.0, 1.0, 1.0]
arith-2: mean=1.00 stdev=0.00 scores=[1.0, 1.0, 1.0, 1.0, 1.0]
```

Against the fixed fake backend the standard deviation is exactly zero, which is the
expected result for a backend with no batching-induced variation — it demonstrates what
the harness reports when there genuinely is none, so a nonzero `stdev` seen against a real
backend can be attributed to the backend, not the harness.

### Comparing two runs before/after

```python
def regressed(before: dict, after: dict, z: float = 2.0) -> bool:
    """Flag a regression only if 'after' falls outside 'before's observed spread."""
    if before["stdev"] == 0:
        return after["mean"] < before["mean"]
    return after["mean"] < before["mean"] - z * before["stdev"]
```

Save each run's `summarise()` output to a file (`--repeats` results per case, with model
and backend version recorded alongside), and feed the saved "before" and the new "after"
into `regressed()` for each case, rather than eyeballing two numbers.

## Conclusion

**Determinism is a spectrum, and the fix depends on which layer of it you actually have.**
Fixing temperature, top-p and seed removes the deliberate randomness that a chat-tuned
default introduces; it does not remove batching-order floating-point variation on backends
where that exists. Know which one you're dealing with before concluding a benchmark is
flaky rather than genuinely varying.

**Report a distribution, not a point.** A single score from a single run of a
non-deterministic system is a sample, and treating it as ground truth is how noise gets
mistaken for a regression, in either direction.

**Decide the comparison threshold before you have the numbers.** A rule agreed before the
data exists is a real check; a rule invented afterwards to explain the data you got is not
a check at all.
