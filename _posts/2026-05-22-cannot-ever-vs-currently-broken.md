---
layout: post
title: "Separating 'Cannot Ever' From 'Currently Broken' in Sweep Automation"
subtitle: "Why one error code can mean two opposite things, and only one of them is fine."
date: 2026-05-22 09:00:00 +0200
tags: [automation, testing, api-design, observability]
description: >-
  A sweep that checks a list of targets for a feature has to decide what a
  failure means, and a response code alone cannot tell it whether that
  failure was expected or is a regression. Here is how to make that decision
  explicit, keep it loud when it should be, and test it so the escape hatch
  does not become a second way to hide problems.
---

## The problem

A sweep is a script that runs on a schedule and asks a list of targets the same question:
does this one support a given feature? It exists so that nobody has to check by hand. The
naive version looks like this:

```python
# broken_sweep.py — do not copy this
import urllib.request
import urllib.error

TARGETS = [
    "http://localhost:8001",
    "http://localhost:8002",
]

def check_feature(base_url: str) -> bool:
    url = f"{base_url}/v3/widgets"
    try:
        urllib.request.urlopen(url, timeout=2)
        return True
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return False  # "doesn't support it"
        raise

if __name__ == "__main__":
    for target in TARGETS:
        supported = check_feature(target)
        print(f"{target}: {'supported' if supported else 'not supported, skipping'}")
```

This runs, it is idempotent, and it produces a clean report. It is also wrong in a way
that will not show up for a long time.

`/v3/widgets` returning 404 means two entirely different things depending on the target.
One target is on an old integration that was never going to grow a `/v3` API — it 404s by
design, today, next year, forever. Another target is a normal, current integration that
is supposed to serve `/v3/widgets` just fine, but a bad deploy broke the route and it now
404s too. Both produce exactly the same HTTP response. The sweep above cannot tell them
apart, because it is not looking at anything that differs between them.

The first case is `TARGETS[0] = "http://localhost:8001": not supported, skipping` — correct,
expected, nothing to do. The second case, once the regression lands, prints the identical
line: `not supported, skipping`. Nothing about that output changes. No alert fires, no
counter moves, no log line looks different from the ten thousand before it. The sweep was
built specifically to remove the need for a human to check this by hand, and the one
failure mode it cannot catch is exactly the one a human checking by hand would have
noticed within a day: "hang on, that one used to work."

The danger is not that the sweep is wrong occasionally. It is that once a real regression
starts, it is filed under the same bucket as things that are fine on purpose, and there is
no mechanism inside the sweep that ever revisits that filing. It stays wrong for as long
as nobody happens to look at that target for an unrelated reason.

## Working through it

### "Cannot ever" has to come from somewhere other than the response

The response code is an observation of the target's current behaviour. "This target will
never support this feature" is a decision about the target's identity — its API version,
its integration type, the fact that it was deprecated eighteen months ago. Those are two
different kinds of fact, and only one of them is knowable from a single HTTP call.

The fix is to stop inferring permanence from a response and instead say it, once, in a
place a person had to deliberately edit:

```python
# cannot_ever.py
"""
Targets that are known, by design, never to support the feature the sweep
checks for. Every entry needs a reason: it is the thing that makes this list
a decision, not a place where errors quietly go to be ignored.
"""

CANNOT_EVER = [
    {
        "target": "http://localhost:8001",
        "reason": "legacy integration on API v2; the feature was introduced in v3 and v2 is frozen",
    },
]
```

Checked *before* the sweep even makes the request for that target: a target on this list
is not being tested for the feature at all, it is being skipped because someone already
established the answer. That is a materially different claim from "I asked and got a 404",
and it should look different in the code.

### Everything else defaults to loud

Once "cannot ever" is its own explicit list, every other 404 has lost its excuse. It is not
expected, because expected non-support has already been accounted for. So it must be
treated as "currently broken": something that is supposed to work and is not, right now,
which is precisely the condition a sweep exists to surface.

```python
if target in cannot_ever_targets:
    return Outcome.EXPECTED_SKIP
try:
    urllib.request.urlopen(url, timeout=2)
    return Outcome.OK
except urllib.error.HTTPError:
    return Outcome.CURRENTLY_BROKEN  # never a silent skip
```

The asymmetry matters. If a target genuinely gains a new, legitimate reason for not
supporting the feature — it gets deprecated tomorrow, say — the sweep now raises a false
alarm about it, and fixing that costs someone a few minutes: confirm the reason, add one
line to `cannot_ever.py`, done. If instead the default stays quiet and a real regression
slips through, the cost is unbounded — it lasts exactly as long as it takes someone to
notice by some means entirely outside the sweep, which could be a support ticket, a
customer complaint, or nothing at all for months. A fixed, small cost paid occasionally is
a better trade than an unbounded cost paid silently.

### The exception list needs its own test

An explicit allowlist only holds the line if nothing can grow it without scrutiny. The
moment "add it to `cannot_ever.py`" becomes the easy way to make an inconvenient alert go
away, the list has quietly turned back into the same lack of distinction the sweep was
just fixed to avoid — it is just one file removed from the response itself.

The cheapest defence is a test that fails if an entry has no reason:

```python
def test_every_cannot_ever_entry_has_a_reason():
    for entry in CANNOT_EVER:
        assert entry.get("reason", "").strip(), (
            f"{entry.get('target')} is on the cannot-ever list with no reason"
        )
```

This does not stop someone writing a lazy reason. It does stop the list from growing by
omission, and it means every addition leaves a one-line paper trail in the diff that a
reviewer can actually question.

## The solution

Four files, complete, standard library only. `fake_targets.py` stands in for two real
services so the whole thing runs on a laptop with no external dependency.

```python
# fake_targets.py
"""
Two fake targets on two ports:
  - :8001 never serves /v3/widgets. This is the "cannot ever" target.
  - :8002 is supposed to serve /v3/widgets but is currently broken.
Run with: python fake_targets.py
"""
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread

class LegacyHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(404)
        self.end_headers()
    def log_message(self, *args):
        pass

class BrokenHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        # Should return 200 for /v3/widgets; a bad deploy broke the route.
        self.send_response(404)
        self.end_headers()
    def log_message(self, *args):
        pass

def serve(port, handler_cls):
    HTTPServer(("localhost", port), handler_cls).serve_forever()

if __name__ == "__main__":
    legacy = Thread(target=serve, args=(8001, LegacyHandler), daemon=True)
    broken = Thread(target=serve, args=(8002, BrokenHandler), daemon=True)
    legacy.start()
    broken.start()
    print("Serving on :8001 (legacy, cannot ever) and :8002 (currently broken)")
    legacy.join()
```

```python
# cannot_ever.py
"""
Targets that are known, by design, never to support the feature. Every
entry needs a reason: it is the thing that makes this list a decision,
not a place where errors quietly go to be ignored.
"""

CANNOT_EVER = [
    {
        "target": "http://localhost:8001",
        "reason": "legacy integration on API v2; the feature was introduced in v3 and v2 is frozen",
    },
]

def cannot_ever_targets():
    return {entry["target"] for entry in CANNOT_EVER}
```

```python
# sweep.py
"""
Run with: python sweep.py
Requires fake_targets.py running first.
"""
import urllib.request
import urllib.error
from enum import Enum, auto

from cannot_ever import CANNOT_EVER, cannot_ever_targets

TARGETS = [
    "http://localhost:8001",
    "http://localhost:8002",
]

class Outcome(Enum):
    OK = auto()
    EXPECTED_SKIP = auto()
    CURRENTLY_BROKEN = auto()

def default_transport(url: str) -> None:
    urllib.request.urlopen(url, timeout=2)

def classify(target: str, transport=default_transport) -> Outcome:
    if target in cannot_ever_targets():
        return Outcome.EXPECTED_SKIP
    try:
        transport(f"{target}/v3/widgets")
        return Outcome.OK
    except urllib.error.HTTPError:
        return Outcome.CURRENTLY_BROKEN
    except urllib.error.URLError:
        return Outcome.CURRENTLY_BROKEN

if __name__ == "__main__":
    for target in TARGETS:
        outcome = classify(target)
        if outcome is Outcome.EXPECTED_SKIP:
            reason = next(e["reason"] for e in CANNOT_EVER if e["target"] == target)
            print(f"{target}: expected skip ({reason})")
        elif outcome is Outcome.CURRENTLY_BROKEN:
            print(f"{target}: CURRENTLY BROKEN — alert")
        else:
            print(f"{target}: ok")
```

```python
# test_sweep.py
"""
Run with: pytest test_sweep.py -v
Tested against pytest 7.x and 8.x; no other dependency needed.
"""
import pytest

from cannot_ever import CANNOT_EVER
from sweep import Outcome, classify

def test_allowlisted_target_is_expected_skip_without_calling_transport():
    calls = []
    def transport(url):
        calls.append(url)
        raise AssertionError("should never be called for an allowlisted target")

    outcome = classify("http://localhost:8001", transport=transport)

    assert outcome is Outcome.EXPECTED_SKIP
    assert calls == []

def test_non_allowlisted_failure_is_currently_broken():
    def transport(url):
        import urllib.error
        raise urllib.error.HTTPError(url, 404, "Not Found", None, None)

    outcome = classify("http://localhost:8002", transport=transport)

    assert outcome is Outcome.CURRENTLY_BROKEN

def test_non_allowlisted_success_is_ok():
    def transport(url):
        return None

    outcome = classify("http://localhost:8002", transport=transport)

    assert outcome is Outcome.OK

def test_every_cannot_ever_entry_has_a_reason():
    for entry in CANNOT_EVER:
        assert entry.get("reason", "").strip(), (
            f"{entry.get('target')} is on the cannot-ever list with no reason"
        )
```

Running it end to end:

```bash
python fake_targets.py &
python sweep.py
# http://localhost:8001: expected skip (legacy integration on API v2; the feature was introduced in v3 and v2 is frozen)
# http://localhost:8002: CURRENTLY BROKEN — alert

pytest test_sweep.py -v
# test_sweep.py::test_allowlisted_target_is_expected_skip_without_calling_transport PASSED
# test_sweep.py::test_non_allowlisted_failure_is_currently_broken PASSED
# test_sweep.py::test_non_allowlisted_success_is_ok PASSED
# test_sweep.py::test_every_cannot_ever_entry_has_a_reason PASSED
```

Two targets, identical response code, opposite outcomes — because the outcome no longer
depends on the response code alone.

## Conclusion

**An error code or response shape is not a reason.** It is a symptom, and the same symptom
can have causes that need opposite handling. Using the symptom itself as the classifier
guarantees those causes get merged the moment they produce the same symptom, which for a
404 they very easily will.

**The permanent-exception list should be an artefact, not an inference.** Someone decided
that a target cannot ever support a feature; that decision should exist as a line of data
with a reason attached, checked before the sweep runs, not reconstructed after the fact
from what the sweep happened to observe.

**Test the exception list, not just the sweep.** A classifier that defaults to loud is
only as good as the list of things it is allowed to be quiet about. If that list can grow
without a reason, it will, and the whole design regresses to the original problem one
convenient addition at a time.

**Default to loud when in doubt.** A false alarm costs a fixed, small amount: someone
looks, confirms it is fine, adds a line to an allowlist. A silenced regression costs an
unbounded amount, because nothing in the system is looking for it any more. That asymmetry
holds for any sweep, canary, or health check that has to tell "known and accepted" apart
from "broken and urgent" using a signal both cases can produce identically.
