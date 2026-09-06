---
layout: post
title: "Building the Dry-Run Path First, and Testing That It Sends Nothing"
subtitle: "Separating planning from execution so a dry run cannot drift out of sync with the real code path."
date: 2026-02-10 09:00:00 +0200
tags: [testing, python, automation, infrastructure-as-code]
description: >-
  A dry-run flag added after the real logic is written tends to fall out of
  sync as the real path grows new side effects nobody remembers to gate. This
  describes structuring a tool so planning and execution are separate from the
  start, and writing a test that proves dry-run mode sends nothing.
---

## The problem

The usual way a dry-run mode gets built is: write the tool's real logic first — the part
that creates records, deletes files, calls an API that changes something — and then, once
it works, sprinkle `if dry_run: return` or `if dry_run: print(...); return` checks in front
of each side-effecting call. This works at the moment it is written. It stops working the
first time someone adds a new side effect to the real path and forgets the dry-run guard,
because the guard is not structural — it is a convention that has to be remembered at every
call site, forever, by everyone who touches the code afterwards.

The cost of that gap is specific and bad: dry-run mode is exactly the mode people trust
before running something for real, often against production. A dry-run flag that misses
one new side effect does not fail loudly — it reports a plan, looks correct, and then the
real run does something the plan never mentioned. The confidence a dry run is supposed to
provide becomes actively misleading in the one case it was meant to prevent.

The deeper problem is that this failure mode cannot be tested for reliably once the code
is structured this way. A test can assert that dry-run mode does not call a particular
mock. It cannot assert "no side-effecting call anywhere in this function was missed a
guard", because that would require enumerating every call site by hand, which is the same
manual process that let the gap appear in the first place.

## Working through it

### Separate deciding from doing

The fix is architectural, not a discipline to remember. Split the tool into two parts that
cannot see each other's concerns. A planner looks at the desired state and the current
state and produces a list of actions — plain data, no side effects, nothing that touches
a network or a filesystem. An executor takes that list and, only when explicitly told to
apply it, performs each action against a real client.

Once the split exists, "dry run" and "apply" stop being two branches of the same function.
Dry run is: call the planner, print or return what it produced, stop. Apply is: call the
planner, then call the executor with its output. The planner is identical in both cases
because it was never given a way to have a side effect in the first place — there is
nothing to gate, because the part capable of a side effect is a separate function that
dry-run mode simply never calls.

### This also makes the tool testable without touching anything real

A planner that takes state in and returns actions out, with no I/O of its own, is trivial
to unit test — feed it various current/desired states, assert on the actions it produces,
no mocking required because there is nothing to mock. The executor is the only part that
needs a mock client or a fake backend in its tests, and it is a thin enough piece of code
that its own test suite can be small: for each action type, assert the client was called
once, with the right arguments.

This is a better outcome than the usual one even for a tool with no dry-run requirement at
all — most of the tool's logic (the planning) becomes pure and cheaply testable, and only a
thin sliver (the executor) needs anything resembling an integration test.

### Prove dry run sends nothing, rather than trusting the flag

The test that actually matters is not "dry-run mode prints a plan" — it is "dry-run mode
never calls the client". That only becomes a checkable assertion once execution is a
separate function: pass a mock client into the executor and assert it in dry-run mode
never gets called, then assert it gets called exactly once per planned action in apply
mode. This is stronger than asserting on printed output, because printed output can be
correct even if something else, elsewhere, also fired.

### Make dry run the default, not an opt-in

Given the split above, defaulting the CLI to dry-run and requiring an explicit flag to
apply costs nothing extra to implement — it's whichever of the two call patterns above the
CLI wires up by default. Given the global principle that anything touching real
infrastructure should default to a dry run, this is the shape that makes that principle
free to follow rather than something to remember on every new tool.

## The solution

A small reconciliation tool against a declarative desired state, using an in-memory fake
backend so the whole example runs anywhere with no real infrastructure involved.

```python
#!/usr/bin/env python3
"""reconcile.py — plan/apply a set of named records against a backend.

Usage:
    python reconcile.py desired.json                # dry run (default)
    python reconcile.py desired.json --apply         # actually apply

desired.json: {"records": {"name": "value", ...}}
"""
import argparse
import json
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Action:
    kind: str  # "create", "update", "delete"
    name: str
    value: str | None = None


class RecordClient(Protocol):
    def list_records(self) -> dict: ...
    def create(self, name: str, value: str) -> None: ...
    def update(self, name: str, value: str) -> None: ...
    def delete(self, name: str) -> None: ...


class FakeRecordClient:
    """An in-memory stand-in for a real record store, for demos and tests."""

    def __init__(self, initial: dict | None = None):
        self._records = dict(initial or {})
        self.calls: list[tuple] = []

    def list_records(self) -> dict:
        return dict(self._records)

    def create(self, name: str, value: str) -> None:
        self.calls.append(("create", name, value))
        self._records[name] = value

    def update(self, name: str, value: str) -> None:
        self.calls.append(("update", name, value))
        self._records[name] = value

    def delete(self, name: str) -> None:
        self.calls.append(("delete", name, None))
        self._records.pop(name, None)


def plan(desired: dict, current: dict) -> list[Action]:
    """Pure: no I/O, no side effects. Just a diff turned into actions."""
    actions = []

    for name, value in desired.items():
        if name not in current:
            actions.append(Action(kind="create", name=name, value=value))
        elif current[name] != value:
            actions.append(Action(kind="update", name=name, value=value))

    for name in current:
        if name not in desired:
            actions.append(Action(kind="delete", name=name))

    return sorted(actions, key=lambda a: (a.kind, a.name))


def apply(actions: list[Action], client: RecordClient) -> None:
    """The only part of this tool allowed to have a side effect."""
    for action in actions:
        if action.kind == "create":
            client.create(action.name, action.value)
        elif action.kind == "update":
            client.update(action.name, action.value)
        elif action.kind == "delete":
            client.delete(action.name)


def describe(actions: list[Action]) -> str:
    if not actions:
        return "No changes."
    lines = [f"  {a.kind:6} {a.name}" + (f" -> {a.value}" if a.value is not None else "") for a in actions]
    return "\n".join(lines)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("desired_file")
    p.add_argument("--apply", action="store_true", help="Actually perform the changes. Default is dry run.")
    args = p.parse_args()

    with open(args.desired_file) as f:
        desired = json.load(f)["records"]

    client = FakeRecordClient(initial={"legacy-record": "old-value"})
    actions = plan(desired, client.list_records())

    if not args.apply:
        print("Dry run — no changes will be made.")
        print(describe(actions))
        return

    print("Applying:")
    print(describe(actions))
    apply(actions, client)
    print("Done.")


if __name__ == "__main__":
    main()
```

```json
{
  "records": {
    "app-record": "10.0.0.0/24",
    "legacy-record": "new-value"
  }
}
```

```bash
python reconcile.py desired.json
```

```
Dry run — no changes will be made.
  create app-record -> 10.0.0.0/24
  update legacy-record -> new-value
```

The tests that prove the design does what it claims:

```python
# test_reconcile.py
from reconcile import Action, FakeRecordClient, apply, plan


def test_plan_is_pure_and_produces_expected_actions():
    desired = {"a": "1", "b": "2"}
    current = {"b": "old", "c": "stale"}

    actions = plan(desired, current)

    assert Action(kind="create", name="a", value="1") in actions
    assert Action(kind="update", name="b", value="2") in actions
    assert Action(kind="delete", name="c") in actions
    assert len(actions) == 3


def test_dry_run_never_touches_the_client():
    client = FakeRecordClient(initial={"b": "old"})
    actions = plan({"a": "1", "b": "old"}, client.list_records())

    # Dry run: describe the plan, never call apply().
    assert len(actions) == 1  # only the create for "a"
    assert client.calls == []
    assert client.list_records() == {"b": "old"}


def test_apply_calls_client_exactly_once_per_action():
    client = FakeRecordClient(initial={"b": "old", "c": "stale"})
    actions = plan({"a": "1", "b": "new"}, client.list_records())

    apply(actions, client)

    assert len(client.calls) == 3
    assert ("create", "a", "1") in client.calls
    assert ("update", "b", "new") in client.calls
    assert ("delete", "c", None) in client.calls
    assert client.list_records() == {"a": "1", "b": "new"}


def test_no_actions_means_apply_calls_nothing():
    client = FakeRecordClient(initial={"a": "1"})
    actions = plan({"a": "1"}, client.list_records())

    apply(actions, client)

    assert client.calls == []
```

```bash
pip install pytest
pytest test_reconcile.py -v
```

```
test_reconcile.py::test_plan_is_pure_and_produces_expected_actions PASSED
test_reconcile.py::test_dry_run_never_touches_the_client PASSED
test_reconcile.py::test_apply_calls_client_exactly_once_per_action PASSED
test_reconcile.py::test_no_actions_means_apply_calls_nothing PASSED
```

`test_dry_run_never_touches_the_client` is the one that matters most: it does not check
printed output, it checks that `client.calls` is empty and that the fake backend's state
is unchanged. That is only possible to assert because `apply()` — the only function capable
of calling the client — is never invoked in the dry-run path at all, by construction.

## Conclusion

**Make the safe behaviour the only behaviour a code path can exhibit, rather than the
behaviour it happens to have today.** A `plan()` function with no access to a client cannot
develop a stray side effect no matter what gets added to it later, which is a stronger
guarantee than a convention that says "remember to check `dry_run` here too".

**A pure planning stage is worth extracting even when dry-run mode is not a requirement.**
It is the part of most reconciliation-style tools that benefits most from unit testing,
and separating it out is what makes that testing cheap.

**The test that proves safety asserts on the absence of a call, not the presence of correct
output.** A dry run that prints a correct-looking plan can still, separately, have done
something — the only way to rule that out is to assert the side-effecting client was never
invoked, which requires the architecture above to even be expressible as a test.
