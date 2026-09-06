---
layout: post
title: "OpenTaberna, and writing the wiki first"
subtitle: "An open-source hospitality stack, and what changes when the documentation is a repository."
date: 2026-09-05 12:00:00 +0200
tags: [OpenTaberna, FastAPI, Open Source, Documentation]
description: >-
  An open-source ordering and fulfilment system built as four repositories with
  a published wiki — and the thing I would take to every project after it: the
  documentation is a repository, reviewed like code.
---

[OpenTaberna](https://github.com/OpenTaberna) is an open-source hospitality stack: a
FastAPI backend, a customer frontend, an admin frontend, and a wiki. Ordering, fulfilment,
payments, returns. The domain is not exotic, which is exactly why it is a good place to be
strict about how a project is put together.

The part worth writing about is not the API. It is the wiki.

## The documentation is a repository

The wiki lives in [its own repository](https://github.com/OpenTaberna/wiki) as Markdown
files, and is published from there to [wiki.opentaberna.de](https://wiki.opentaberna.de).
It is not a wiki in the sense of a database somebody edits through a browser. It is text
under version control, and it moves through pull requests like everything else.

That one decision changes the character of the documentation completely.

**It can be wrong in a way that shows.** A page in a hosted wiki drifts silently — there is
no diff, no blame, no review, and no moment where somebody has to look at the change. A
page in a repository is reviewed by the same person reviewing the code that made it stale.

**It can be required.** If the docs are in the repository, "the docs are updated in the
same change" is a rule a reviewer can actually enforce, because the change is right there
in the diff. If the docs are somewhere else, that rule is a hope. I have watched the hope
version fail on every project that tried it, including mine.

**It can be run locally.** `docker compose up` brings up the same wiki software the
published site runs, serving this repository's pages, with no login and no setup wizard.
So a page looks locally exactly as it will look published. That sounds like a small
convenience and it is the difference between people previewing their writing and people
guessing.

## What the pages have to cover

The structure is the interesting bit, because it maps to the questions people actually
arrive with:

- **What is this and how is it built** — the architecture and the four repositories
- **How do I run it** — the whole stack, locally, in one place
- **How does authorization work** — the roles, the clients, and what the API enforces
- **The API** — endpoints, the response envelope, the error model
- **The database** — the schema as it is actually built
- **Orders and fulfilment** — the lifecycle, payments, the outbox, returns
- **Configuration** — every setting and where it can come from
- **Deployment** — how it runs in production

Two of those deserve comment.

**"The schema as it is actually built"** is a deliberate phrase. Schema documentation
drifts faster than anything else in a project, because it is written during design and
then the migrations happen. A page that promises to describe reality has to be revisited
when reality changes, and saying so in the title makes that obligation explicit.

**"Every setting and where it can come from"** matters more than it sounds. Most
configuration documentation lists the settings and omits the precedence — file, environment,
default, flag — and precedence is exactly what you need at the moment configuration is not
doing what you expect. That is the only moment anyone reads the page.

## The response envelope, and why consistency beats cleverness

An API that returns a bare object here and a wrapped one there, an error as a string in
one place and an object in another, is an API where every client writes its own special
cases. The special cases are where bugs live, and they are invisible until a client hits
the one endpoint that is shaped differently.

Deciding the envelope and the error model **once**, writing it on a page, and then holding
every endpoint to it is unglamorous and it is most of what makes an API pleasant. It also
makes the documentation shorter, because the shape is described once instead of per
endpoint.

The same argument applies to authorization. Roles and clients defined centrally, with the
API enforcing them, means "who can do this" is answerable by reading one page rather than
by grepping decorators.

## Open source changes the standard

Working on something public raises the bar in a specific way: **you cannot rely on
anybody knowing anything.**

There is no colleague to ask, no shared context, no "obviously it needs the database
running first". Everything a person needs has to be written down, and the fastest way to
find out whether it is written down is that somebody tries and fails.

That is uncomfortable and it is the most useful review a project gets. Every internal
project I have worked on had gaps that were invisible precisely because everyone had
already been told the missing thing in a conversation. A public project has no
conversations to lean on.

The related discipline is repository conventions — how issues are written, what a commit
message says, what "done" means. Internally you can get away with these living in
somebody's head. Publicly they have to be written down, and once they are written down you
notice they were never really agreed.

## What I would take to every project

The wiki-as-repository pattern, without hesitation.

Documentation that is not under version control is documentation that will be wrong, and
the only question is when. Putting it in a repository does not make anybody write more —
it makes not writing visible in a diff, which turns out to be the same thing.
