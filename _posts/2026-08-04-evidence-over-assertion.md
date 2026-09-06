---
layout: post
title: "Verifying an Agent's Work Against Reality, Not Its Own Report"
subtitle: "A verifier that inspects the repository state instead of trusting the agent's report."
date: 2026-08-04 09:00:00 +0200
tags: [agents, testing, ci-cd]
description: >-
  An agent process can exit cleanly and report success while having changed
  nothing, or while its tests silently didn't run. This article builds a
  separate verification step that checks git state and re-runs the real
  test command, and wires it into CI as an independent job the agent cannot
  influence.
---

## The problem

An agent given a task produces two things: a change to a repository, and a transcript
describing what it did. It is tempting to trust the transcript, especially when the run
exits with status zero and the final message reads like a normal pull request description:
"Fixed the bug, added a test, all tests pass."

None of that is evidence. An exception caught somewhere in the agent's own tool-calling
loop can be swallowed and reported as a graceful conclusion. A test command can be typed
into the transcript as prose without ever having been executed. A task can fail to produce
any diff at all — the agent decided the fix wasn't needed, or ran out of budget, or
misread the task — while the wrapper script around it still exits zero, because zero is
what "the process finished" means, not what "the task succeeded" means.

Trusting the report is quietly appealing because a well-behaved run does end with a report
that matches reality. The failure only shows up on the runs where it doesn't, and by then
whatever consumed the report — a merge, an escalation, a person's afternoon — has already
acted on it.

## Working through it

### Decide what "done" means before the agent starts

An outcome that can be checked has to be expressed as something observable in the
repository: specific files exist, a specific command exits zero, a diff is non-empty. This
has to be written down before the run, from the task, not derived afterwards from whatever
the agent happened to do — otherwise the check just restates the transcript in a different
format.

### Run the verification out of process, after the agent exits

The verifier is a separate program with no access to the agent's conversation, only to the
repository on disk and the command it's told to run. It has nothing to be talked into. If
the agent's transcript claims success and the verifier finds an untouched repository, the
verifier wins by construction, because it is the only one of the two that looked.

### Check the specific claim, not merely that something changed

"A diff exists" is necessary but not sufficient. If the task's expected outcome was "the
test suite passes," the verifier has to run that test suite itself, not scan the agent's
own log for the word "pass" — a string a confused or dishonest run can produce as easily as
a genuine one.

### Treat an empty diff as its own failure mode

A run that changes nothing when a change was expected is a different failure from a run
whose change breaks a test, and worth reporting differently: the first means the agent
didn't attempt the task, the second means it attempted it and got it wrong. Collapsing both
into "the tests failed" loses information a person fixing the pipeline needs.

## The solution

A minimal Python project the verifier is checked against:

```python
# src/calculator.py
def add(a, b):
    return a + b
```

```python
# tests/test_add.py
from src.calculator import add


def test_add_handles_floats():
    assert add(1.5, 2.5) == 4.0
```

The verifier, independent of any agent framework:

```python
#!/usr/bin/env python3
# verify.py
"""Verify a coding agent's claimed work against the actual repository state.

Run this after the agent process has exited. It has no access to the
agent's transcript - only to the repository and the test command, which
is the only evidence that counts.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


def git_diff_is_empty(repo: Path) -> bool:
    result = subprocess.run(
        ["git", "-C", str(repo), "diff", "--quiet", "HEAD"],
        capture_output=True,
    )
    return result.returncode == 0


def run_test_command(repo: Path, command: list[str]) -> tuple[bool, str]:
    result = subprocess.run(
        command, cwd=repo, capture_output=True, text=True, timeout=300
    )
    passed = result.returncode == 0
    return passed, result.stdout + result.stderr


def required_paths_exist(repo: Path, paths: list[str]) -> list[str]:
    return [p for p in paths if not (repo / p).exists()]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("repo", type=Path)
    parser.add_argument("expectation", type=Path)
    args = parser.parse_args()

    expectation = json.loads(args.expectation.read_text())
    failures = []

    if expectation.get("expect_a_diff", True) and git_diff_is_empty(args.repo):
        failures.append("no changes were made to the repository")

    for path in required_paths_exist(args.repo, expectation.get("must_exist", [])):
        failures.append(f"expected file is missing: {path}")

    test_command = expectation.get("test_command")
    if test_command:
        passed, output = run_test_command(args.repo, test_command)
        if not passed:
            failures.append(f"test command failed:\n{output[-2000:]}")

    if failures:
        print("VERIFICATION FAILED", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1

    print("VERIFICATION PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

```json
// expectation.json
{
  "expect_a_diff": true,
  "must_exist": ["src/calculator.py", "tests/test_add.py"],
  "test_command": ["python", "-m", "pytest", "-q"]
}
```

Wired into CI as a job the agent step cannot reach:

```yaml
# .github/workflows/agent-task.yml
name: agent-task
on: workflow_dispatch

jobs:
  run-agent:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Run the coding agent
        run: ./run-agent.sh --task "fix add() to accept floats"
      - uses: actions/upload-artifact@v4
        with:
          name: agent-workspace
          path: workspace/

  verify:
    needs: run-agent
    runs-on: ubuntu-latest
    steps:
      - uses: actions/download-artifact@v4
        with:
          name: agent-workspace
          path: workspace/
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install pytest==8.3.3
      - name: Verify against reality, not the agent's report
        run: python verify.py workspace/ expectation.json
```

Run it locally against the fixed project above:

```bash
python verify.py . expectation.json
```

```
VERIFICATION PASSED
```

Revert `add()` to its broken form and run the same command:

```
VERIFICATION FAILED
- test command failed:
FAILED tests/test_add.py::test_add_handles_floats - assert 4 == 4.0
```

## Conclusion

Only the repository's actual state is authoritative; a transcript, however detailed, is a
claim about that state, not the state itself.

Verification here is cheap precisely because it does nothing an ordinary CI pipeline
wouldn't already do — the point is running it unconditionally, in a process the agent has
no access to, rather than accepting the agent's own account of having run it.

An empty diff deserves its own failure category, separate from a failing test, because it
describes a different problem: the task not being attempted, rather than being attempted
and getting it wrong.

This is not specific to agents. A human contributor's "tested locally, all good" in a pull
request description carries exactly the same evidentiary weight as an agent's transcript —
none — until CI actually runs the tests. Agents just make it obvious why that check has to
be unconditional.
