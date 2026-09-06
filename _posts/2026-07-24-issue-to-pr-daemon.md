---
layout: post
title: "A Deterministic Daemon That Turns a Labelled Issue Into a Pull Request"
subtitle: "Keeping every commit and push in code you can read, with the model producing only a diff."
date: 2026-07-24 09:00:00 +0200
tags: [agents, python, ci-cd]
description: >-
  Letting a model call git directly makes every commit and push as
  unpredictable as the model's own reasoning, which is hard to audit and
  harder to trust. This walks through splitting an issue-to-PR pipeline so
  the model only ever produces a patch, while a small deterministic daemon
  performs every side effect, with a complete runnable example.
---

## The problem

An appealing way to automate routine issue work is to give an agent a shell and a GitHub
token and let it work an issue end to end: read the issue, write the fix, commit, push, open
a PR. It is also the version of this that is hardest to audit, because every side effect —
which files were touched, which branch was pushed to, what the commit message says, whether
a PR was opened against the right base — now depends on what the model decided to do in that
particular run, and "what the model decided to do" is not something you can read as a diff
against a known set of rules.

```python
# Broken. Do not copy this.
agent.run(f"""
Read issue #{issue_number}, fix it, and open a PR.
You have git and gh available. Use them however you need to.
""")
```

The failure here is not that the model necessarily does something wrong — it might do
exactly the right thing most of the time. It is that "most of the time" is not an audit
trail. If a commit lands with a message that misdescribes the change, or a push goes to the
wrong branch, or the model decides an unrelated file needs updating too, there is no
deterministic layer between the model's reasoning and the repository's history — the model's
tool calls *are* the history. Reviewing that after the fact means reviewing an agent
transcript, not a diff, and the two are not the same kind of evidence.

## Working through it

### Split the pipeline at exactly the point where side effects start

The model's job and the daemon's job are different in kind: the model looks at an issue and
proposes a change; the daemon decides whether, when, and how that change becomes a commit
that leaves the local disk. Drawing the line there means the model never needs — and never
gets — credentials that can push to the real repository. It works inside an isolated
workspace and hands back a patch. Everything after "apply this patch" is ordinary,
reviewable Python that does the same three things every time: apply, commit, push, open PR.

### Give the model an isolated workspace, not the daemon's own checkout

A fresh `git worktree` (or a throwaway clone) per issue means the model's changes cannot
touch anything outside that directory, and a workspace that goes wrong — the model produces
garbage, or the patch does not apply — is thrown away rather than cleaned up by hand. The
daemon creates the worktree, hands its path to the patch-generation step, and removes it
once the issue is either handled or abandoned.

### Validate the patch before it ever reaches `git commit`

`git apply --check` tells you whether a patch applies cleanly without touching the working
tree. Running this before committing turns "the model produced something unusable" into a
skipped issue with a clear reason, rather than a broken commit or a `git apply` failure
buried in the middle of the daemon's own code path.

### Make re-polling safe by transitioning the label, not by remembering state internally

If the daemon polls open issues carrying `agent-ready` every few minutes, the same issue
must not be picked up twice while it is being worked, and must not be picked up again after
it succeeds. GitHub's issue labels are already a durable, external record of that state — the
daemon moves the label from `agent-ready` to `agent-in-progress` the moment it starts work,
and to `agent-done` (or removes it entirely) once a PR is open. No internal database is
needed for this, and — importantly — the state survives a daemon restart, because it lives on
the issue itself rather than in the daemon's memory.

### Bound the blast radius of a stub or a misbehaving model

The example below uses a pluggable `generate_patch` function so the article's tests do not
require a live model API key — this is a deliberate design point, not just a convenience for
the example: whatever generates a patch should be swappable, testable in isolation, and
replaceable with a stub that returns a known, fixed patch. A daemon whose commit-and-push
logic can only be tested by also invoking a real language model is not testable at all.

## The solution

A complete daemon: polling, worktree isolation, patch application, commit and push, PR
creation via the GitHub API, and a test proving an issue already moved past `agent-ready` is
never reprocessed.

```python
# daemon.py
import subprocess
from dataclasses import dataclass
from pathlib import Path

import requests

GITHUB_API = "https://api.github.com"


@dataclass
class Issue:
    number: int
    title: str
    body: str
    labels: list[str]


class GitHubClient:
    def __init__(self, token: str, repo: str):
        self.repo = repo
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
        })

    def issues_with_label(self, label: str) -> list[Issue]:
        resp = self.session.get(
            f"{GITHUB_API}/repos/{self.repo}/issues",
            params={"labels": label, "state": "open"},
        )
        resp.raise_for_status()
        return [
            Issue(i["number"], i["title"], i.get("body") or "", [l["name"] for l in i["labels"]])
            for i in resp.json()
            if "pull_request" not in i
        ]

    def set_labels(self, issue_number: int, labels: list[str]) -> None:
        resp = self.session.put(
            f"{GITHUB_API}/repos/{self.repo}/issues/{issue_number}/labels",
            json={"labels": labels},
        )
        resp.raise_for_status()

    def open_pull_request(self, title: str, head: str, base: str, body: str) -> str:
        resp = self.session.post(
            f"{GITHUB_API}/repos/{self.repo}/pulls",
            json={"title": title, "head": head, "base": base, "body": body},
        )
        resp.raise_for_status()
        return resp.json()["html_url"]


def generate_patch(issue: Issue, workspace: Path) -> str:
    """Pluggable patch-generation step.

    In production this calls out to a model. For this example, and for
    tests, it is a deterministic stub — the point of the split is that this
    function's output is the only thing the rest of the daemon trusts, and
    it can be swapped without touching commit/push/PR logic at all.
    """
    target = workspace / "NOTES.md"
    original = target.read_text() if target.exists() else ""
    target.write_text(original + f"\n- addressed issue #{issue.number}: {issue.title}\n")
    diff = subprocess.run(
        ["git", "diff"], cwd=workspace, capture_output=True, text=True, check=True
    )
    return diff.stdout


def create_worktree(repo_path: Path, branch: str) -> Path:
    worktree_path = repo_path.parent / f"worktree-{branch}"
    subprocess.run(
        ["git", "worktree", "add", "-b", branch, str(worktree_path)],
        cwd=repo_path, check=True,
    )
    return worktree_path


def remove_worktree(repo_path: Path, worktree_path: Path, branch: str) -> None:
    subprocess.run(
        ["git", "worktree", "remove", "--force", str(worktree_path)],
        cwd=repo_path, check=False,
    )
    subprocess.run(["git", "branch", "-D", branch], cwd=repo_path, check=False)


def patch_applies_cleanly(worktree_path: Path, patch: str) -> bool:
    result = subprocess.run(
        ["git", "apply", "--check", "-"],
        cwd=worktree_path, input=patch, text=True, capture_output=True,
    )
    return result.returncode == 0


def process_issue(gh: GitHubClient, repo_path: Path, issue: Issue) -> str | None:
    if "agent-ready" not in issue.labels:
        return None  # not our concern; also guards against a stale poll result

    gh.set_labels(issue.number, ["agent-in-progress"])

    branch = f"agent/issue-{issue.number}"
    worktree_path = create_worktree(repo_path, branch)
    try:
        patch = generate_patch(issue, worktree_path)
        if not patch.strip():
            gh.set_labels(issue.number, ["agent-ready"])  # nothing to do; retry later
            return None

        if not patch_applies_cleanly(worktree_path, patch):
            gh.set_labels(issue.number, ["agent-failed"])
            return None

        subprocess.run(["git", "add", "-A"], cwd=worktree_path, check=True)
        subprocess.run(
            ["git", "commit", "-m", f"Address issue #{issue.number}: {issue.title}"],
            cwd=worktree_path, check=True,
        )
        subprocess.run(["git", "push", "origin", branch], cwd=worktree_path, check=True)

        pr_url = gh.open_pull_request(
            title=f"Fix #{issue.number}: {issue.title}",
            head=branch,
            base="main",
            body=f"Closes #{issue.number}",
        )
        gh.set_labels(issue.number, ["agent-done"])
        return pr_url
    finally:
        remove_worktree(repo_path, worktree_path, branch)


def poll_once(gh: GitHubClient, repo_path: Path) -> list[str]:
    results = []
    for issue in gh.issues_with_label("agent-ready"):
        pr_url = process_issue(gh, repo_path, issue)
        if pr_url:
            results.append(pr_url)
    return results
```

```python
# test_daemon.py
from pathlib import Path
from unittest.mock import MagicMock

from daemon import Issue, process_issue


def test_issue_without_agent_ready_label_is_never_processed(tmp_path):
    """The load-bearing test: an issue whose label already moved past
    agent-ready (in-progress, done, or removed entirely) must not be
    reprocessed, even if a stale poll result still lists it."""
    gh = MagicMock()
    issue = Issue(number=1, title="Already claimed", body="", labels=["agent-in-progress"])

    result = process_issue(gh, tmp_path, issue)

    assert result is None
    gh.set_labels.assert_not_called()


def test_issue_with_agent_ready_label_is_claimed_first(tmp_path, monkeypatch):
    gh = MagicMock()
    issue = Issue(number=2, title="Needs work", body="", labels=["agent-ready"])

    calls = []
    gh.set_labels.side_effect = lambda num, labels: calls.append((num, tuple(labels)))

    import daemon
    monkeypatch.setattr(daemon, "create_worktree", lambda repo, branch: tmp_path)
    monkeypatch.setattr(daemon, "generate_patch", lambda issue, ws: "")
    monkeypatch.setattr(daemon, "remove_worktree", lambda *a, **k: None)

    process_issue(gh, tmp_path, issue)

    # The very first label transition must be the claim, before any patch
    # work happens — this is what makes a second concurrent poll see
    # agent-in-progress rather than agent-ready.
    assert calls[0] == (2, ("agent-in-progress",))
```

```ini
# requirements.txt
requests==2.32.3
```

Run the tests:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt pytest==8.3.3
pytest -v
```

Expected output:

```
test_daemon.py::test_issue_without_agent_ready_label_is_never_processed PASSED
test_daemon.py::test_issue_with_agent_ready_label_is_claimed_first PASSED

2 passed in 0.12s
```

## Conclusion

The daemon does not make the model more trustworthy — it makes the model's untrustworthiness
irrelevant to what actually lands in the repository, because the model's output is a diff
that gets validated before anything with real consequences happens to it.

Three points generalise beyond issue automation specifically:

**Draw the line between "produces a proposal" and "performs a side effect" as sharply as
possible, and put the boundary in code, not in a prompt.** A prompt that tells a model
"only propose changes, never push" is a request the model can misunderstand or ignore under
the wrong conditions; a daemon that structurally never gives the model push credentials
cannot be talked out of that boundary.

**Validate untrusted output before it touches shared state.** `git apply --check` is one
line and it is the difference between a rejected patch and a broken commit history.

**Put durable state where it survives a restart of the thing that reads it.** The label on
the issue, not an in-memory set inside the daemon, is what prevents reprocessing — a crash
and restart of the daemon loses nothing, because the record of "already claimed" lives on
GitHub, not in the daemon's own process.
