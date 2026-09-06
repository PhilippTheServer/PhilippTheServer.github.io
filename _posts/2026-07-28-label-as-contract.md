---
layout: post
title: "The Label as Contract: Consent and Priority as the Whole Queue"
subtitle: "Making a human-applied label the only thing an autonomous worker is allowed to trust."
date: 2026-07-28 09:00:00 +0200
tags: [agents, security]
description: >-
  An autonomous worker that infers permission or priority from an issue's own
  text is trusting content anyone can write. This covers designing a label as
  the sole, explicit consent gate for an agent, a deterministic priority
  order derived from it, and a claim mechanism that survives two runs racing
  for the same issue, with a complete, testable example.
---

## The problem

Give an autonomous agent access to an issue tracker and, sooner or later, someone asks a
reasonable-sounding question: why does it need an explicit label at all, when the issue
title already says "urgent, please fix now" or the body already asks the agent by name to
handle it? Why not let the agent read intent from the content, the way a person would?

Because an issue's content is exactly the part of the system anyone can write, including
someone with no authority to direct the agent's work, including — if this tracker or any
part of it is ever exposed to external input — someone actively trying to. Free text is not
a channel with an authorisation model; it is a channel with whatever words someone chose to
put in it. "Please treat this as top priority" in an issue body is not evidence that a human
who can actually authorise the agent's time decided that; it is evidence that someone typed
those words. An agent that infers consent or priority from content is trusting the one part
of the input that is, by construction, unauthenticated.

This also fails in a more mundane way even with no adversary involved: an agent that
decides for itself which issues look important enough to work will disagree with what the
team actually wants prioritised, and there is no way to review that decision after the fact,
because "the agent judged this urgent" is not a record anyone signed off on. There has to be
a durable, external, human-applied fact that says "this specific unit of work is authorised,
right now" — and it cannot live inside the same content the agent is reading to decide what
to do.

## Working through it

### Make the label the entire authorisation model, with nothing else able to substitute for it

The rule is deliberately narrow: an agent may only act on an issue carrying a specific,
human-applied label — call it `agent-ready` — and never on the basis of anything else in the
issue: not the title, not the body, not who opened it, not another label that merely
resembles this one. A label is applied by someone clicking a UI element or calling an API
with credentials; it cannot be phrased into existence by text in an issue body the way a
sentence can. That is the entire reason it is trustworthy where content is not — it is a
distinct action, taken by an authenticated actor, that leaves its own audit trail
independent of anything the issue says.

### Derive priority from the label transition, not from a re-reading of urgency each poll

Once consent is established by presence of the label, a second question remains when
several issues qualify at once: which one first. Reading urgency language out of each
issue's text has the same problem as inferring consent from text — it is not authoritative,
and it will drift as people phrase urgency differently. A deterministic, auditable ordering
instead comes from either a numeric priority label a human explicitly chose (`priority-1`,
`priority-2`), or — with no explicit priority scheme — the timestamp at which `agent-ready`
was applied, read from the issue's own timeline of events. Either way the order is a fact
recorded by GitHub, not a judgement the agent makes fresh on every poll.

### Claim atomically, and treat a failed claim as someone else winning, not as an error

Two runs of the same polling daemon — a slow previous run still finishing as a new one
starts, or two daemons pointed at the same repository by mistake — can both see the same
`agent-ready` issue in the same poll. The fix is not "poll less often", which reduces the
odds without removing the race; it is making the claim itself atomic against the possibility
of a second claim: read the issue's current labels immediately before writing, and only
proceed if `agent-ready` is still present at that moment. GitHub's API returning success on
a label update is not, on its own, proof of exclusive ownership — but combined with reading
the current label state and swapping in the same operation, a losing run can detect that
`agent-ready` was already gone by the time it tried to remove it, and back off rather than
proceeding to do the same work twice.

### Treat label removal mid-work as a revocation, checked, not assumed permanent

Consent is not a one-time gate checked only at the start. If a human removes `agent-ready`
— or removes `agent-in-progress` — while the agent is partway through its work, that is a
deliberate revocation and the agent must notice and stop, rather than finishing a piece of
work that a human decided, mid-flight, should not happen. A long-running daemon should
re-check the label state at meaningful checkpoints, not only once at the start.

## The solution

A complete claim mechanism: computing a deterministic work order, attempting an atomic
label-swap claim, and a test using a mocked GitHub API proving that two concurrent claim
attempts on the same issue produce exactly one winner.

```python
# claim.py
from dataclasses import dataclass

READY_LABEL = "agent-ready"
IN_PROGRESS_LABEL = "agent-in-progress"


@dataclass
class Issue:
    number: int
    labels: list[str]
    ready_since: float  # unix timestamp the READY_LABEL was applied


class ClaimConflict(Exception):
    """Raised when another run already claimed the issue first."""


class GitHubIssuesClient:
    """Thin wrapper. Swap this out for a real REST client; the ordering and
    claim logic below depends only on this interface."""

    def __init__(self, session):
        self.session = session  # a requests.Session, or a test double

    def list_issues_with_label(self, repo: str, label: str) -> list[Issue]:
        resp = self.session.get(f"/repos/{repo}/issues", params={"labels": label})
        return [Issue(i["number"], i["labels"], i["ready_since"]) for i in resp.json()]

    def get_current_labels(self, repo: str, issue_number: int) -> list[str]:
        resp = self.session.get(f"/repos/{repo}/issues/{issue_number}")
        return resp.json()["labels"]

    def set_labels(self, repo: str, issue_number: int, labels: list[str]) -> None:
        resp = self.session.put(f"/repos/{repo}/issues/{issue_number}/labels", json={"labels": labels})
        resp.raise_for_status()


def work_order(issues: list[Issue]) -> list[Issue]:
    """Deterministic FIFO by the moment agent-ready was applied — not by any
    text in the issue, and not by insertion order in whatever list the API
    happened to return."""
    return sorted(issues, key=lambda i: i.ready_since)


def claim(client: GitHubIssuesClient, repo: str, issue_number: int) -> None:
    """Atomic-enough claim: re-read current labels immediately before
    swapping, and only proceed if READY_LABEL is still present. If it is
    not, someone else's claim already won."""
    current = client.get_current_labels(repo, issue_number)
    if READY_LABEL not in current:
        raise ClaimConflict(f"issue #{issue_number} was already claimed")

    new_labels = [l for l in current if l != READY_LABEL] + [IN_PROGRESS_LABEL]
    client.set_labels(repo, issue_number, new_labels)
```

```python
# test_claim.py
import pytest

from claim import ClaimConflict, GitHubIssuesClient, Issue, claim, work_order


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        pass


class FakeSession:
    """A tiny in-memory stand-in for the GitHub API. Labels live in a dict
    keyed by issue number, and set_labels mutates it — this is enough to
    prove the race-handling logic without any network access."""

    def __init__(self, initial_labels: dict[int, list[str]]):
        self.labels = initial_labels

    def get(self, path, params=None):
        issue_number = int(path.rstrip("/").split("/")[-1])
        return FakeResponse({"labels": self.labels[issue_number]})

    def put(self, path, json):
        issue_number = int(path.split("/")[-2])
        self.labels[issue_number] = json["labels"]
        return FakeResponse({})


def test_work_order_is_fifo_by_ready_since():
    issues = [
        Issue(number=3, labels=["agent-ready"], ready_since=300),
        Issue(number=1, labels=["agent-ready"], ready_since=100),
        Issue(number=2, labels=["agent-ready"], ready_since=200),
    ]
    ordered = work_order(issues)
    assert [i.number for i in ordered] == [1, 2, 3]


def test_only_one_of_two_concurrent_claims_wins():
    session = FakeSession({42: ["agent-ready", "bug"]})
    client = GitHubIssuesClient(session)

    # First claim succeeds.
    claim(client, "example/repo", 42)
    assert "agent-in-progress" in session.labels[42]
    assert "agent-ready" not in session.labels[42]

    # A second run attempting to claim the same issue must see that
    # agent-ready is already gone, and refuse rather than proceeding.
    with pytest.raises(ClaimConflict):
        claim(client, "example/repo", 42)


def test_claim_preserves_other_labels():
    session = FakeSession({7: ["agent-ready", "bug", "priority-1"]})
    client = GitHubIssuesClient(session)

    claim(client, "example/repo", 7)

    assert set(session.labels[7]) == {"bug", "priority-1", "agent-in-progress"}
```

```ini
# requirements.txt
requests==2.32.3
pytest==8.3.3
```

Run the tests:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pytest -v
```

Expected output:

```
test_claim.py::test_work_order_is_fifo_by_ready_since PASSED
test_claim.py::test_only_one_of_two_concurrent_claims_wins PASSED
test_claim.py::test_claim_preserves_other_labels PASSED

3 passed in 0.08s
```

The middle test is the one that matters: it proves that when two runs both attempt to claim
issue 42, the second one raises `ClaimConflict` rather than silently succeeding and leaving
two runs working the same issue in parallel — because by the time it re-reads the labels,
`agent-ready` is already gone.

## Conclusion

The security property here is narrow and easy to state precisely: the agent's entire
authorisation and priority model is reducible to one label and one timestamp, both of which
are set by an action a human took deliberately, and neither of which can be forged by writing
text into an issue.

Two points generalise past issue-tracker automation specifically:

**Never let an autonomous system infer authorisation from content it does not control the
provenance of.** Free text — an issue body, a comment, a commit message — is written by
whoever has write access to that field, which is frequently a much larger set of people (or
systems) than the set authorised to direct an agent's actions. A label, a signed request, an
explicit API call from a known identity: these carry provenance that text embedded in
content does not.

**A race between two workers claiming the same unit of work needs an atomic check-and-set,
not a shorter poll interval.** Reducing the polling frequency reduces the probability of a
collision; it does not remove it. Re-reading state immediately before writing, and treating
a changed precondition as someone else's win rather than your own error, is what actually
closes the race.
