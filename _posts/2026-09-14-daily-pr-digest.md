---
layout: post
title: "A Daily Digest of What Actually Got Merged, Written by a Local LLM"
subtitle: "An MQTT collector buffers every merged PR into SQLite; a timer at 18:00 asks a local model for a per-project summary, commits it to a repo, and publishes it to a feed."
date: 2026-09-14 09:00:00 +0200
tags: [ci-cd, observability, python, architecture]
description: >-
  Nobody reads the PR list at the end of the day, so we stopped asking them to.
  A small pipeline buffers every merged pull request as it happens, groups the
  day's work by project at 18:00, asks a local LLM for a summary that names
  every contributor, commits the result to a repository, and publishes it to an
  RSS feed. This article covers the shape of the pipeline and the three design
  decisions that make it boring.
---

## The problem

A team that merges a dozen pull requests a day across several repositories has
two options for finding out what happened. One is to read the list of merged
PRs, which is a list of titles, and titles are not summaries. The other is to
ask someone, which is a tax on the one person who was actually doing the work.
Both options scale badly, and both are skipped, which means the people who
need to know what changed — the ones who do not work on the code — do not know,
and find out later, in the form of a question that could have been an
afternoon's reading.

The obvious answer is a bot that summarises the day's PRs. The less obvious
questions are where the data comes from, where the model runs, and what the
output is *for* — because a summary that exists only as a chat message is a
summary that is gone by tomorrow, and a summary that requires someone to open
the right channel is a summary that does not reach the people who need it.

## Working through it

### Where the data comes from: the merge event, not the API

The first decision is what triggers a digest entry. Polling the repository's
API for recently merged PRs is the approach most implementations take, and it
has a property that matters less than it looks: it couples the digest to the
API being up, rate-limited, and returning the same view of "merged" that the
digest wants. A merge is also an *event* that the CI already knows about —
the pipeline that ran the checks and merged the PR has the repository, the
number, the title, the author, and the URL in hand, at the moment it happens.

So the pipeline does not poll. A small GitHub action, run on merge, publishes
one message to an MQTT topic — `ci/pr/merged` — with the PR's metadata. MQTT
is the bus the estate already runs for everything else, so this is not a new
infrastructure choice; it is the same one, applied to a new kind of event.
The message is the whole contract: a JSON object with `repo`, `pr_number`,
`title`, `description`, `author`, `url`, and `merged_at`. Nothing else.

The consequence of making the merge event the source is that the digest can
only ever contain things that actually merged. A closed PR, a reverted PR, a
PR that merged to a branch that never shipped — none of it appears, because
none of it produced the event. The digest's accuracy is inherited from the CI,
which is the strongest accuracy guarantee available, and it costs nothing to
maintain because there is nothing to maintain.

### The buffer: why SQLite, and why the collector never sleeps

The collector is a small Python service that runs forever as a systemd unit.
It subscribes to the merge topic at QoS 1, and every message it receives is
inserted into a SQLite database. That is the whole job.

SQLite is the right shape for this for the reason that is usually the wrong
reason: it is a file. The collector and the digest job are two different
processes on the same machine, and the only thing they share is a file on disk.
There is no database server to run, no connection string to configure, no
backup to schedule, and no failure mode that involves a network. The database
is a few thousand rows of text, and its entire schema is one table with a
`UNIQUE(repo, pr_number)` constraint, which is what makes the insert
idempotent: if the same PR is published twice — a retried action, a
reconnect that replays a QoS 1 message — the second insert updates the row
instead of duplicating it.

The collector is deliberately dumb. It does not classify, summarise, or
decide. It buffers. The reason for the split is that the two halves have
different failure requirements: the collector must never drop a message, so
it runs forever and reconnects on its own; the digest job runs once a day and
is allowed to fail, because tomorrow's run will include today's PRs anyway —
the buffer keeps them.

### The digest: one job, one timer, one model

At 18:00, a systemd timer runs the digest job. It reads every PR whose merge
date is today, groups them by project, and for each project asks a local LLM —
a 14-billion-parameter model on Ollama, running on the same machine — for a
markdown summary. The prompt is the project name, the date, and the list of
PRs with their titles, descriptions, and authors. The model's job is to write
a short update that references every contributor by name, because a summary
that says "improvements were made" is not a summary, and because the people
who did the work should be the ones named in it.

The model is local for the reason that is usually given — the PR descriptions
go to no external service — but also for a less obvious one: the digest runs
at a fixed time every day, unattended, and a local model has no rate limit,
no API key to rotate, and no failure mode that involves a third party's
infrastructure. The output quality of a 14B model on this task is good enough
that the constraint is not a compromise; it is the design.

### The output: a file in a repository, and a message on the bus

The digest has two outputs, and both matter, because they serve different
consumers.

The first is a dated markdown file — `YYYY/YYYY-MM-DD.md` — committed to a
dedicated repository. The file has a section per project, in a fixed order.
This is the archive. It is the thing you read a month later to find out what
happened, and it is versioned, which means it is searchable, diffable, and
gone-if-you-delete-the-repo in the way that a chat message is not. The commit
is made with a deploy key, by a small function that clones or updates the repo
and pushes the file. The function returns the file's URL, which is stamped
onto the second output.

The second output is a message per project, published to a summary topic on
the same MQTT bus. Each message carries the project name, the date, the
markdown summary, the list of contributors, and — this is the part that makes
the feed useful — a structured list of the PRs themselves, each with its
repository, number, title, URL, and author. The consumer of that topic is an
RSS feed, and the structured PR list is what lets the feed render each PR as a
link rather than as text. The digest URL in the message is what lets the feed
link the whole day's file.

The split between the file and the message is the difference between an
archive and a notification. The file is for the person who wants the detail;
the message is for the person who wants to know, at the end of the day, that
something happened and where to look.

### The classifier: re-derived on read, not trusted from the column

One detail in the buffer is worth naming, because it is the kind of decision
that looks like over-engineering until the day it saves you. The project each
PR belongs to is written into the database at insert time, by a classifier
that maps a repository name to a project. But when the digest job reads the
day's PRs, it does not trust the column. It re-runs the classifier on every
row it returns.

The reason is that the classifier is a list that changes. When a new
repository is added to a project, the rows already buffered for today still
carry the old classification — `Other` — and a digest that trusted the column
would file them under the wrong heading. Re-deriving on read makes the
classifier the single source of truth for the whole day, including the PRs
that were buffered before the change. It costs one function call per row, and
the rows are in the hundreds, not the millions.

## The solution

The pipeline, end to end:

```
GitHub (merged PR) ─► merge action ─► ci/pr/merged ─► collector ─► SQLite
                                                                      │
                                             18:00 (systemd timer)    │
                                                                      ▼
                              group by project ─► local LLM (per-project markdown)
                                                     │                        │
                                                     ▼                        ▼
                    commit YYYY/YYYY-MM-DD.md to the     publish per-project digest
                    digests repository                  to ci/pr/summary ─► RSS feed
```

And the three decisions that make it boring:

1. **The source is the merge event, not a poll.** The digest can only contain
   what actually merged, and it inherits the CI's accuracy for free.
2. **The buffer is a file, and the writer never sleeps.** The collector's only
   job is to not lose a message; the digest's failure is recoverable because
   the buffer keeps the day's PRs for the next run.
3. **The output is a versioned file and a structured message.** The archive
   and the notification are different things with different consumers, and
   the structured PR list is what turns the feed from a wall of text into
   links.

## Conclusion

A daily digest of merged work is not a hard problem; it is a shape problem.
Get the shape right — the event as the source, the file as the buffer, the
local model as the summariser, the versioned file and the structured message
as the two outputs — and the implementation is small enough to read in one
sitting, and boring enough to run unattended for a year.

The part that is worth keeping is not any single component. It is the
discipline of giving the summary a home that outlives the day: a file in a
repository, dated, versioned, linkable, and reachable from the feed that
announces it. A summary without an archive is a conversation; a summary with
one is a record.
