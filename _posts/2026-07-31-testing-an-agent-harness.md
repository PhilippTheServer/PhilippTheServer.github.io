---
layout: post
title: "Testing an Agent Harness Without Ever Calling the Model"
subtitle: "Testing a policy layer's allow or deny decisions without a model anywhere in the loop."
date: 2026-07-31 09:00:00 +0200
tags: [testing, agents, python]
description: >-
  A coding agent's permission decisions are ordinary deterministic code, but
  testing them by running the model end to end is slow, expensive and
  non-reproducible. Separating the decision layer from the model and
  recording real tool-call shapes as fixtures makes the whole thing testable
  with an ordinary unit-test suite, in milliseconds, with no API key
  required.
---

## The problem

A coding agent's harness has a component that decides, for every tool call the model
proposes, whether to run it, refuse it, or ask the operator. That component is a plain
function: it takes a structured request and returns a decision. Nothing about it needs a
model at runtime.

Yet the obvious way to test it is to run the whole agent and see what happens:

```python
# Broken. Do not copy this.
def test_the_agent_refuses_to_delete_the_repo():
    session = start_agent_session(model="some-model")
    session.send("Please delete everything in this directory.")
    session.run_until_done()
    assert session.last_tool_call is None
```

This test calls a real model, waits on a network round trip, and asserts on whatever the
model happened to sample that run. It is slow enough that nobody runs it on every change.
It is non-deterministic, so a failure might be the harness or might be the model choosing
different wording this time. And it tests the wrong thing: whether the model was talked
out of a bad idea, not whether the harness would have stopped it regardless.

The permission layer is the part that actually has to be correct, every time, regardless
of how the request arrived. Testing it by proxy, through a model, means the thing doing
the enforcing is the one part of the system the test suite never directly exercises.

## Working through it

### Separate the decision from the request that triggers it

The seam that makes this testable is making the decision function take a plain, structured
request — a tool name and its arguments — rather than a model response object or a
conversation. It does not care whether that request came from a model, a replay of a
recorded session, or a person typing it by hand. That is what lets a test construct one
directly instead of producing one by running a model.

### Capture real requests as fixtures, once

Real tool-call shapes are worth more than invented ones, because a model's actual output
has quirks — extra whitespace, slightly different argument names, commands wrapped in
`bash -c` — that a hand-written test case won't think to include. Recording a handful of
real requests once, redacting anything specific to the session they came from, and
committing them as fixtures gives the test suite realistic inputs without ever calling a
model again.

### Write the harness so it never imports the model client

The strongest guarantee here is architectural: if the policy module has no import of any
LLM SDK, it cannot accidentally depend on model behaviour, and a reviewer or a simple
`grep` in CI can prove that boundary holds. A module that only understands `ToolCall` and
`Decision` objects can be as thoroughly tested as any other pure function.

## The solution

```python
# policy.py
"""Deterministic decision layer for a coding agent's tool calls.

This module has no dependency on any LLM client. It only knows about
ToolCall objects and returns a Decision. That is what makes it testable
without a model in the loop.
"""
from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from enum import Enum


class Decision(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"


@dataclass(frozen=True)
class ToolCall:
    tool: str
    command: str = ""
    path: str = ""


# Patterns are matched with fnmatch against `command`, not parsed as shell.
DENY_PATTERNS = (
    "rm -rf /*",
    "* > /dev/sda*",
    "mkfs*",
    "dd if=* of=/dev/*",
)

READ_ONLY_TOOLS = frozenset({"read_file", "list_dir", "grep"})


def decide(call: ToolCall) -> Decision:
    if call.tool in READ_ONLY_TOOLS:
        return Decision.ALLOW

    if call.tool == "run_command":
        for pattern in DENY_PATTERNS:
            if fnmatch.fnmatch(call.command, pattern):
                return Decision.DENY
        return Decision.ASK

    if call.tool == "write_file":
        if call.path.startswith("/etc/") or call.path.startswith("/boot/"):
            return Decision.DENY
        return Decision.ASK

    return Decision.ASK
```

```json
// fixtures/recorded_calls.json
[
  {
    "note": "recursive-delete-of-root-is-denied",
    "call": {"tool": "run_command", "command": "rm -rf /*"},
    "expected": "deny"
  },
  {
    "note": "writing-outside-etc-is-only-asked-about",
    "call": {"tool": "write_file", "path": "/home/user/project/config.py"},
    "expected": "ask"
  },
  {
    "note": "writing-under-etc-is-denied",
    "call": {"tool": "write_file", "path": "/etc/passwd"},
    "expected": "deny"
  },
  {
    "note": "an-ordinary-command-is-asked-about",
    "call": {"tool": "run_command", "command": "npm install"},
    "expected": "ask"
  }
]
```

```python
# test_policy.py
import json
from pathlib import Path

import pytest

from policy import Decision, ToolCall, decide

FIXTURES = Path(__file__).parent / "fixtures" / "recorded_calls.json"


def load_cases():
    cases = json.loads(FIXTURES.read_text())
    return [
        pytest.param(
            ToolCall(**case["call"]),
            Decision(case["expected"]),
            id=case["note"],
        )
        for case in cases
    ]


@pytest.mark.parametrize("call,expected", load_cases())
def test_recorded_call_gets_expected_decision(call, expected):
    assert decide(call) == expected


def test_read_only_tools_are_never_asked_about():
    call = ToolCall(tool="read_file", path="/home/user/notes.txt")
    assert decide(call) == Decision.ALLOW


def test_unknown_tool_defaults_to_ask_not_allow():
    call = ToolCall(tool="send_email", command="")
    assert decide(call) == Decision.ASK
```

Run it:

```bash
pip install pytest==8.3.3
pytest -v
```

```
test_policy.py::test_recorded_call_gets_expected_decision[recursive-delete-of-root-is-denied] PASSED
test_policy.py::test_recorded_call_gets_expected_decision[writing-outside-etc-is-only-asked-about] PASSED
test_policy.py::test_recorded_call_gets_expected_decision[writing-under-etc-is-denied] PASSED
test_policy.py::test_recorded_call_gets_expected_decision[an-ordinary-command-is-asked-about] PASSED
test_policy.py::test_read_only_tools_are_never_asked_about PASSED
test_policy.py::test_unknown_tool_defaults_to_ask_not_allow PASSED
======================== 6 passed in 0.02s ========================
```

Six assertions, no network call, no API key, and a run time measured in milliseconds. The
boundary is cheap to enforce in CI, too:

```bash
grep -Ei "openai|anthropic|import.*llm" policy.py && \
  echo "policy.py must not depend on a model client" && exit 1
exit 0
```

## Conclusion

Determinism in a harness is a property you design for by drawing the seam in the right
place, not something you recover afterwards by mocking the model more carefully.

A permission layer that can only be exercised by running the model is a permission layer
you cannot safely change: every edit needs a live, non-reproducible run to have any
confidence in, so edits happen rarely and get less scrutiny than they need.

Fixtures recorded from real sessions are more valuable than hand-written ones, but they do
decay — a new tool, a changed argument shape — and are worth regenerating occasionally
rather than treating as permanently complete.

The principle generalises past agents: whenever a system sits in front of something
probabilistic, the part of it that enforces a guarantee should be extractable, testable,
and provably free of any dependency on the probabilistic part.
