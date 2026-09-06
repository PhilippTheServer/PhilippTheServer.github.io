---
layout: post
title: "Testing Infrastructure Code by Executing Its Real Expressions"
subtitle: "Rendering a role's actual expression through Ansible's engine instead of restating it."
date: 2026-08-28 09:00:00 +0200
tags: [testing, ansible, ci-cd, infrastructure-as-code]
description: >-
  A test that reimplements an Ansible expression's logic in Python proves
  two independent implementations agree today, not that the shipped
  expression is correct, and the two can drift apart while both keep
  passing. This article renders the actual expression through Ansible's own
  Templar and filters, using a recursive dict merge as a concrete case
  where a hand-written paraphrase gets it wrong.
---

## The problem

An Ansible role often computes a value with a Jinja expression rather than a plain literal
— merging a set of default resource limits with an environment-specific override, say,
using the `combine` filter with `recursive=True` so that overriding one nested key doesn't
wipe out its siblings.

The tempting way to test that this works is to reimplement the same computation in Python
and compare the two answers:

```python
# Broken. Do not copy this reasoning.
def test_merge_logic():
    defaults = {"memory": "512Mi", "requests": {"cpu": "250m", "memory": "256Mi"}}
    overrides = {"memory": "1Gi", "requests": {"memory": "512Mi"}}
    # A hand-written "equivalent" of what the role is supposed to do:
    merged = {**defaults, **overrides}
    assert merged["memory"] == "1Gi"
```

This test passes, and it proves nothing about the role. It never touches the role's actual
expression, the actual Jinja engine, or Ansible's actual `combine` filter — it checks that a
second, independently written piece of logic agrees with itself. If someone later edits the
role's expression to something subtly different, this test keeps passing, because it was
never wired to the expression in the first place. And the naive merge used above (`{**a,
**b}`) is shallow: it would have replaced the entire `requests` dictionary, silently
dropping `cpu`, which is exactly the kind of divergence a paraphrase is prone to introduce
without anyone noticing.

## Working through it

### Decide what "correct" means: the expression's own output, given inputs

The purpose of the test is to catch a change to the shipped expression, not to independently
derive the right answer from first principles. The only way to do that reliably is to
render the actual expression string, through the actual engine, with the actual filters
Ansible would use at runtime, and assert on the value that comes out — not to hand-write a
formula that is merely supposed to behave the same way.

### Render through Ansible's own machinery, not a bare Jinja2 environment

A plain `jinja2.Environment` does not know about Ansible's own filters — `combine` among
them — because they are registered by Ansible itself, not by Jinja2. `ansible.template.Templar`,
part of `ansible-core`, is the same rendering path a real play uses, so a test built on it
exercises the real filter behaviour, including any change in that behaviour between
Ansible versions.

### Extract the expression from the file that ships, don't retype it

If the test contains its own copy of the expression's text, the two copies can drift apart
independently, in exactly the way a paraphrase can — someone edits the role and forgets the
test, or edits the test and forgets the role. Loading the actual `defaults/main.yml` file
the role ships, and pulling the expression string out of it, means editing the shipped file
is what the test responds to.

### Keep the fixture data honest

Passing the same structure of variables a real play would supply — rather than a shape
that happens to be convenient to type into a test — keeps the test representative of what
actually runs, rather than of a simplified stand-in for it.

## The solution

{% raw %}
```yaml
# roles/limits/defaults/main.yml
default_limits:
  cpu: "500m"
  memory: "512Mi"
  requests:
    cpu: "250m"
    memory: "256Mi"

environment_overrides:
  memory: "1Gi"
  requests:
    memory: "512Mi"

effective_limits: "{{ default_limits | combine(environment_overrides, recursive=True) }}"
```
{% endraw %}

```python
# tests/test_effective_limits.py
from pathlib import Path

import yaml
from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar

DEFAULTS_PATH = (
    Path(__file__).parent.parent / "roles" / "limits" / "defaults" / "main.yml"
)


def render(expression, variables):
    templar = Templar(loader=DataLoader(), variables=variables)
    return templar.template(expression)


def test_effective_limits_is_a_real_recursive_merge_not_a_shallow_one():
    variables = yaml.safe_load(DEFAULTS_PATH.read_text())
    expression = variables["effective_limits"]

    result = render(expression, variables)

    assert result == {
        "cpu": "500m",
        "memory": "1Gi",
        "requests": {"cpu": "250m", "memory": "512Mi"},
    }


def test_a_naive_shallow_merge_would_have_lost_requests_cpu():
    """This documents why the recursive merge matters: it illustrates the
    failure a hand-written paraphrase would have missed, it does not test
    the shipped expression itself."""
    variables = yaml.safe_load(DEFAULTS_PATH.read_text())
    shallow = {**variables["default_limits"], **variables["environment_overrides"]}

    assert shallow["requests"] == {"memory": "1Gi"}  # cpu silently gone
```

```bash
pip install ansible-core==2.17.5 pyyaml==6.0.2 pytest==8.3.3
pytest tests/ -v
```

```
tests/test_effective_limits.py::test_effective_limits_is_a_real_recursive_merge_not_a_shallow_one PASSED
tests/test_effective_limits.py::test_a_naive_shallow_merge_would_have_lost_requests_cpu PASSED
======================== 2 passed in 0.31s ========================
```

The first test only passes because `templar.template()` runs the exact expression the role
ships, through Ansible's real `combine` filter — it recovers `requests.cpu: "250m"` because
the merge is genuinely recursive, not because a Python dict comprehension was told to
assume it should be.

{% raw %}
Changing the shipped expression from `recursive=True` to a plain `combine` call with no
argument — a one-word edit that looks harmless in review — makes the first test fail
immediately, because it renders the actual file:
{% endraw %}

```
AssertionError: assert {'cpu': '500m', 'memory': '1Gi', 'requests': {'memory': '512Mi'}} == {'cpu': '500m', 'memory': '1Gi', 'requests': {'cpu': '250m', 'memory': '512Mi'}}
```

A test built on a Python paraphrase of the merge would never have noticed that edit at all.

## Conclusion

A test that recomputes the same answer with independent logic proves the two
implementations agree today; it says nothing about whether the shipped expression is
correct, and the two can drift apart from each other while both keep passing.

Render through the actual engine and the actual filter set the runtime uses. A bare
Jinja2 environment silently lacks Ansible's own filters, which makes it look like a
convenient shortcut and makes it the wrong tool for this specific job.

Pull the expression from the file that ships rather than retyping it into the test, so
that editing the role — not editing the test alongside it from memory — is the thing the
test actually responds to.

The general principle extends past Ansible: whenever the artefact under test is itself an
expression — a filter chain, a database query, a regular expression — the correctness
oracle is running that expression, not restating in different code what you believe it
does.
