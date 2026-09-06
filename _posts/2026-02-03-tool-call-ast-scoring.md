---
layout: post
title: "Scoring Tool Calls by Parsed Structure Instead of String Equality"
subtitle: "Comparing the canonicalised call, not the raw text, so equivalent arguments pass and wrong calls do not."
date: 2026-02-03 09:00:00 +0200
tags: [llm, testing]
description: >-
  Comparing a model's tool call against an expected one by exact string match
  fails on harmless formatting differences and a substring check lets wrong
  calls through by accident. This shows how to parse both sides into a
  structure first, canonicalise it, and compare field by field.
---

## The problem

An eval harness that checks a model's function-calling output usually starts with the
simplest possible comparison: does the output text equal the expected text. That breaks
almost immediately, for reasons that have nothing to do with whether the call is actually
correct.

JSON does not have a canonical key order, so `{"city": "Berlin", "unit": "celsius"}` and
`{"unit": "celsius", "city": "Berlin"}` are the same call with different text. Numbers are
not canonical either — `5`, `5.0` and `"5"` can all represent the same intended argument
depending on how the model formats it, and a strict string comparison treats all three as
different. Optional fields make it worse: a field the model omits, a field it sets to
`null`, and a field it sets to a default value can all be semantically equivalent for a
given tool, but they are three different strings.

Loosening the check to a substring match trades one failure for a worse one: it now passes
things it should not. If the expected argument is `"Berlin"` and the model calls a
different, wrong tool whose arguments happen to include the string `"Berlin"` somewhere
else, a substring check reports a pass. String-based scoring cannot win here in either
direction, because the actual object being compared — a structured call — is being
compared as if it were free text.

## Working through it

### Parse before comparing

The fix starts with not comparing text at all. Parse the model's tool call output — the
JSON arguments string that comes back in an OpenAI-compatible function-calling response —
into an actual Python object before anything else happens. If that parse fails, that is
itself the result: a hard failure, not a crash in the harness and not something to paper
over with a fallback string comparison. A tool call with invalid JSON arguments is broken
regardless of what the arguments were meant to say.

### Canonicalise both sides the same way

Once both the expected call and the actual call are parsed structures, canonicalise them
before comparing: recursively sort dictionary keys, and normalise numeric types so `5`,
`5.0` and `"5"` compare equal when the field is declared numeric. This removes the
formatting noise without weakening what is actually being checked — the comparison is
still exact, just exact on meaning rather than on text layout.

### Give fields their own comparison rules where "equal" isn't simple

Some fields are order-independent lists (a set of requested attributes, regardless of the
order the model lists them). Some are case-insensitive enums. Treating every field with the
same blanket equality check either rejects harmless variation or, worse, is loosened
globally to let it through everywhere, including places where an exact match matters. The
right level is per-field: a comparison spec that says which fields tolerate reordering, or
case, or numeric-type differences, so the exactness of the check matches the actual
semantics of that argument rather than a project-wide compromise.

### Decide what "extra" means before you hit it

A model that returns an additional field the expected spec did not mention is a judgement
call, not an automatic failure or an automatic pass — some tools accept extra optional
parameters harmlessly, others treat an unexpected field as a sign that the model
misunderstood the schema. Making this an explicit setting (`allow_extra_fields: bool` per
case) keeps the decision visible in the test data rather than buried in scorer code that
has to be read to know what's actually being tolerated.

## The solution

```python
#!/usr/bin/env python3
"""tool_call_scoring.py — structural comparison of tool calls.

score_tool_call(actual_raw, expected) compares a model's raw tool-call
output (name + JSON arguments string, as returned by an OpenAI-compatible
function-calling response) against an expected call spec.
"""
import json
from dataclasses import dataclass, field


class InvalidToolCall(Exception):
    pass


@dataclass
class FieldRule:
    order_independent_list: bool = False
    case_insensitive: bool = False


@dataclass
class ExpectedCall:
    name: str
    arguments: dict
    field_rules: dict = field(default_factory=dict)
    allow_extra_fields: bool = False


def parse_actual(name: str, arguments_json: str) -> tuple:
    try:
        args = json.loads(arguments_json)
    except json.JSONDecodeError as exc:
        raise InvalidToolCall(f"arguments are not valid JSON: {exc}") from exc
    if not isinstance(args, dict):
        raise InvalidToolCall(f"arguments must be a JSON object, got {type(args).__name__}")
    return name, args


def _normalise_scalar(value):
    if isinstance(value, bool):
        return value  # bool before int/float: True/False must not collapse into 1/0
    if isinstance(value, (int, float)):
        return float(value)
    return value


def _fields_equal(actual, expected, rule: FieldRule) -> bool:
    a, e = actual, expected
    if rule.order_independent_list and isinstance(a, list) and isinstance(e, list):
        return sorted(map(str, a)) == sorted(map(str, e))
    if rule.case_insensitive and isinstance(a, str) and isinstance(e, str):
        return a.lower() == e.lower()
    return _normalise_scalar(a) == _normalise_scalar(e)


def score_tool_call(actual_name: str, actual_arguments_json: str, expected: ExpectedCall) -> tuple:
    """Returns (passed: bool, diff: list[str])."""
    diffs = []

    try:
        name, args = parse_actual(actual_name, actual_arguments_json)
    except InvalidToolCall as exc:
        return False, [str(exc)]

    if name != expected.name:
        diffs.append(f"wrong tool: expected {expected.name!r}, got {name!r}")
        return False, diffs

    for key, expected_value in expected.arguments.items():
        if key not in args:
            diffs.append(f"missing argument: {key}")
            continue
        rule = expected.field_rules.get(key, FieldRule())
        if not _fields_equal(args[key], expected_value, rule):
            diffs.append(f"argument {key} mismatch: expected {expected_value!r}, got {args[key]!r}")

    if not expected.allow_extra_fields:
        extra = set(args) - set(expected.arguments)
        if extra:
            diffs.append(f"unexpected extra arguments: {sorted(extra)}")

    return (len(diffs) == 0), diffs
```

```python
# test_tool_call_scoring.py
import pytest

from tool_call_scoring import ExpectedCall, FieldRule, score_tool_call


def test_passes_on_key_order_difference():
    expected = ExpectedCall(name="get_weather", arguments={"city": "Berlin", "unit": "celsius"})
    passed, diffs = score_tool_call(
        "get_weather", '{"unit": "celsius", "city": "Berlin"}', expected
    )
    assert passed, diffs


def test_passes_on_numeric_type_difference():
    expected = ExpectedCall(name="search_flights", arguments={"max_price": 500})
    passed, diffs = score_tool_call(
        "search_flights", '{"max_price": 500.0}', expected
    )
    assert passed, diffs


def test_fails_on_wrong_tool_even_with_matching_substring():
    expected = ExpectedCall(name="get_weather", arguments={"city": "Berlin"})
    passed, diffs = score_tool_call(
        "get_forecast_history", '{"city": "Berlin", "note": "not the right tool"}', expected
    )
    assert not passed
    assert "wrong tool" in diffs[0]


def test_order_independent_list_field():
    expected = ExpectedCall(
        name="search_flights",
        arguments={"cabins": ["economy", "business"]},
        field_rules={"cabins": FieldRule(order_independent_list=True)},
    )
    passed, diffs = score_tool_call(
        "search_flights", '{"cabins": ["business", "economy"]}', expected
    )
    assert passed, diffs


def test_fails_on_extra_unexpected_field_by_default():
    expected = ExpectedCall(name="get_weather", arguments={"city": "Berlin"})
    passed, diffs = score_tool_call(
        "get_weather", '{"city": "Berlin", "unexpected": true}', expected
    )
    assert not passed
    assert "unexpected extra arguments" in diffs[0]


def test_allows_extra_field_when_configured():
    expected = ExpectedCall(
        name="get_weather", arguments={"city": "Berlin"}, allow_extra_fields=True
    )
    passed, diffs = score_tool_call(
        "get_weather", '{"city": "Berlin", "unexpected": true}', expected
    )
    assert passed, diffs


def test_invalid_json_is_a_hard_failure_not_a_crash():
    expected = ExpectedCall(name="get_weather", arguments={"city": "Berlin"})
    passed, diffs = score_tool_call("get_weather", "{not valid json", expected)
    assert not passed
    assert "not valid JSON" in diffs[0]
```

```bash
pip install pytest
pytest test_tool_call_scoring.py -v
```

```
test_tool_call_scoring.py::test_passes_on_key_order_difference PASSED
test_tool_call_scoring.py::test_passes_on_numeric_type_difference PASSED
test_tool_call_scoring.py::test_fails_on_wrong_tool_even_with_matching_substring PASSED
test_tool_call_scoring.py::test_order_independent_list_field PASSED
test_tool_call_scoring.py::test_fails_on_extra_unexpected_field_by_default PASSED
test_tool_call_scoring.py::test_allows_extra_field_when_configured PASSED
test_tool_call_scoring.py::test_invalid_json_is_a_hard_failure_not_a_crash PASSED
```

## Conclusion

**Compare the thing being tested, not its text representation.** A tool call is a
structured object with a name and typed arguments; scoring it as a string throws that
structure away and then tries to compensate with pattern matching, which cannot recover
what was lost.

**A scoring function needs its own tests before it is trusted to score anything else.** The
test suite here is not incidental — the tricky cases (key order, numeric type, extra
fields, invalid JSON) are exactly the ones a naive scorer gets wrong, and asserting the
scorer handles them is cheaper than discovering it does not from a confusing eval result
weeks later.

**Make the comparison strictness a property of the test data, not the scorer's code.** Per-
field rules and an explicit `allow_extra_fields` flag mean a reviewer can see what's being
tolerated by reading the test case, instead of having to read the scoring function to find
out.
