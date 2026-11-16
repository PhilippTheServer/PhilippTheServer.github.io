---
layout: post
title: "The Information Problem"
subtitle: "Nisaba is a closed-loop lifecycle for information about a moving world. Twenty days, twenty-eight pull requests, ten thousand lines of tests against nine thousand of code, and not a single AI model required."
date: 2026-11-16 09:00:00 +0200
tags: [cpp, architecture, testing, documentation, methodology]
description: >-
  Nisaba treats information as stale claims about a moving world: bitemporal
  revisions, adversarial traces, and a closed loop that needs no AI.
---

## The problem

Information is a description of something. That something — a codebase, a
hospital ward, a bakery, a warehouse — is in motion. It changes. Any
description of it is therefore a **snapshot**: a claim captured at one moment,
in one form, by someone or something that may be right or may not. The moment
the snapshot is taken, the thing it describes has already moved on. A
description is never *current*. It is only **less or more stale**.

That sounds like a documentation problem, and the original example was
documentation. Humans are notoriously bad at documenting their own work; even
when they do, the documentation may unknowingly claim behaviour the code does
not implement, and the bugs stay undocumented because nobody knows them yet.
The code is a moving target, so it is its own most precise documentation —
which is a problem, because that documentation has to reach people who cannot
read it.

But the documentation example is just the friendliest face of it. In the
original conversation, the same shape showed up everywhere:

- **A hospital.** A patient has just come out of an operation. Who needs
  informing, with what, by whom, and what is the next step? Every answer is a
  claim about a patient who is, at this very moment, changing.
- **A bakery.** The bread is in the oven at 220 °C. Who takes it out, when,
  and what happens if the timer is the only thing that knows?

Written down, the problem has eleven facets: capturing information competes
with the work being done; a description is a claim, not a fact, and nothing in
it says whether it holds; it decays silently — *the longer a description
lives, the more it lies*; you cannot tell when it went stale; it points at
other things; you cannot say what changed; it is hard to remove, hard to
find, has no front door, has no marked next step, and no shared form.

One problem, many faces. That is what **Nisaba** is an answer to.

## Working through it

### It started as a conversation, not as code

On 24 October I had a long conversation with a language model about exactly
this — information gathering, updating, and distribution, with the
documentation, hospital and bakery examples threaded through it. The first
thing done with the result was to create a repository and commit the
conversation itself, preserved verbatim, alongside the distilled problem
statement. The repository's first act of information management is to record
the information that started the information-management project. (The
information XD.)

That was a deliberate move. The problem is about information that has an
owner, a moment of capture, and a history. The seed conversation has all
three, so it gets treated like any other atom of information in the system:
attributed, versioned, and never overwritten.

### The loop

The answer is not a database with better timestamps. It is a maintained
**correspondence and responsibility loop**:

```
world / source authority
    -> identified observations
    -> versioned, attributed assertions with explicit dependencies
    -> answers to declared questions, with conditions of use
    -> accountable delivery / decisions / actions
    -> observed outcomes and new questions
    -> renewed observation
```

The last edge is the one everyone leaves out. An information system that
delivers answers but never observes what those answers *did* is a broadcast,
not a loop. The loop closes through responsibility: someone — a human, a
service, an agent — accepted the delivery, did the next step, and the outcome
is observed and fed back.

Six governing rules keep the loop honest. Statements are separated from the
acts of observing and asserting them. Every assertion carries two time
histories and several independent assessments. Dependencies are conservative
and declared in explicit source contracts. Answers are given to *declared*
questions under hard conditions before anything is ranked. The loop closes
through responsibility and observed effects. And the boundaries of semantics,
authority and removal are explicit instead of implicit.

### Bitemporal truth

The central data structure is a revision with two time dimensions. Each
assertion has immutable recorded revisions, and each revision separates:

- **Claimed validity** — when the source says the assertion applies in the
  modeled world (world time).
- **Record history** — when the system held that revision (record time).
- **Observation** — when the source was actually sampled.
- **Admissibility** — whether a particular reader may rely on it for a
  particular question *now*.

A revision is live at a read cut `K` when
`valid_from <= S < valid_to and system_from <= K < system_to`. An open valid
end means *no end recorded*, not *guaranteed true*.

Why two clocks? Because the work that comes out of confusing them is
misleading. Take the worked hospital example: a dose of 10 mg is reported at
cut K1 as valid from 09:00, stopped at 11:00 at K2, and then corrected to
5 mg at K3. If you audit what the system believed **at K1**, after K3 has
happened, you must see the original 10 mg belief *and* a separately labeled
correction notice. You must not see a rewritten history in which the system
always knew 5 mg, and you must not see the 10 mg belief standing alone as if
the correction never existed.

Three changes that look identical in a single-timestamp store must not be
confused: ending validity (the world changed), correcting a revision
(knowledge changed), and a review going overdue (maintenance failed — the
world may not have changed at all).

There is a hard limit here, and the design says so plainly. If two possible
worlds produce exactly the same observations but differ in something
unobserved — a stable patient versus one deteriorating quietly — no system
given only those observations can certify which world is real. More
computation, a graph, or an AI does not remove that information limit. What
is achievable is being **exact about what is recorded** and **conditional
about what it establishes**. A reported fact, a reproducible interpretation,
a recent observation, a source-enforced consistency guarantee, and a safe
action are five distinct achievements. No timestamp or confidence score
converts one into the others.

### AI is optional

This is the part I keep having to repeat. The baseline engine uses source
transactions, parsers, measurements, forms, finite deterministic queries and
rules, and accountable human judgments. A language model may sit on top as an
optional producer, projector, or reader — under the exact same contracts as
everything else. Remove every model from the stack and no required capability
is lost.

It is not a novelty claim, and I have checked the foundations rather than
around them: W3C PROV-DM already models agents as persons, organizations or
software; Levy's 1996 work on complete answers from incomplete databases,
Fowler's bitemporal history, Doyle's truth maintenance systems, provenance
semirings, RFC 9111, Gray and Cheriton's leases, Chandy and Lamport's
snapshots, and the end-to-end argument all support specific mechanisms the
specification builds on. The contribution is an explicit **composition
contract** between them, not any single piece.

Which brings me to the overclaim I retracted. An early draft of the
prior-art document claimed the refresh loop closed a newly discovered general
gap in the field. It did not. The document now says, in so many words, that
the claim was not justified and is withdrawn, and that the repository does
not establish that other systems lack proactive staleness handling. I would
rather have a retraction in the repository than a claim in it. A system about
the integrity of information should practice it on its own claims, and a
withdrawn overclaim is the cheapest possible demonstration.

### Sixteen adversarial traces

The specification ships with sixteen adversarial worked traces, V1 through
V16, across four domains — code, hospital, bakery, and cross-domain — plus
six conditional invariant arguments. Each trace gives inputs and events and
states the required observable result. They are designed to fail, not to
pass. A few favorites:

- **V4 — replay is reproducible, and wrong.** A parser interprets a timeout
  field in seconds where the source meant milliseconds. Replay the same
  input and you get the same wrong answer, every time. Reproducibility is
  not semantic correctness, and the trace requires the system to say so.
- **V6 — absence is not negation.** No allergy assertion is on record for a
  patient. The answer to "does the patient have allergies?" is *unknown*,
  not *no allergies*. A copied report is two paths to one assessment, not
  double corroboration.
- **V7 — an ACK is not responsibility.** A transport-level acknowledgment for
  a handoff does not transfer responsibility until the authorized receiver
  accepts.
- **V9 — a countdown is not removal.** A timer expiring makes a task *due*;
  it does not assert the bread was removed. The 220 °C setpoint is not proof
  of a 220 °C reading, and operator acknowledgment records acceptance, not
  the effect.
- **V13 — the gap in the feed.** A sequence arrives as 100, 102. Number 101
  is missing. Continuity certificates are withdrawn and the feed must be
  reconciled; the system does not interpolate the gap away.
- **V16 — the unstaffed role.** A role name exists in the configuration.
  A role name existing is not an assignment.

The traces are now implemented as the acceptance suite in C++23, with
deterministic fixtures per domain and negative assertions — tests that assert
what must **not** happen, like "the countdown does not remove the
obligation". The line counts tell the story: 8,909 lines of source, 10,337
lines of tests. The tests outnumber the code, and most of them are trying to
break the specification rather than confirm it.

## The solution

To be precise about the state of things: Nisaba is a **conditional
specification** with a working implementation of its full build plan, not a
deployment. The document set — overview, the atom, temporal truth, refresh,
graph, history, removal, retrieval, navigation, form, projector, validation,
coordination, plus seventeen design documents — defines the contracts. The
implementation is C++23 under CMake, built against a pinned GCC 16.2
toolchain in a container, on a repository-scoped private self-hosted runner
that I enrolled through the usual homeIaC process.

The build plan was 22 blocks, B01 through B22, each a pull request: from CI
and semantic records, through an RFC 6902 patch executor, a durable ordered
journal with race-safe per-stream acceptance, an atomic graph projection with
honest application watermarks, bitemporal reads, identity resolution,
typed justifications, footprints and invalidation, question plans with hard
eligibility gates, durable obligations, handoff acceptance, purge manifests
and erasure receipts, a GraphQL transport with bounded request handling, to
metrics, backpressure and dead-letter visibility — ending with the V1–V16
adversarial qualification. All 22 were delivered in twenty days, 28 merged
pull requests from 24 October to 13 November. The bootstrap passed 173
model/codec/field-parity checks before any logic was written.

Scope was cut deliberately: no authentication, accounts or roles — the
trusted deployment boundary is external; no UI; no built-in AI; payloads are
inert data that is never executed and never auto-fetched; JSON is capped at
1 MiB and 64 container levels. The journal is for delivery, replay and
recovery; the graph is the only database for current and historical reads.
GraphQL is the API language, not a graph database.

What the system can claim, and only that: it is exact about what is recorded,
conditional about what that establishes, and its limits — observation,
coverage, action — are explicit fields rather than footnotes.

## Conclusion

**A description is never current; it is only stale.** The entire design
follows from refusing to pretend otherwise, and from giving staleness the
same treatment as any other fact: recorded, attributed, and revisable.

**Two clocks are not a feature, they are a bug fix.** The moment you stop
confusing "the world changed" with "we learned something" with "we stopped
checking", your history stops lying to your auditors.

**A countdown is not a removal.** Timers, ACKs, setpoints and role names are
all weaker than the effects people assume they record. An information system
must say which side of that gap every value lives on.

**Make the AI optional and the system becomes testable.** If the baseline
path is deterministic, the traces are deterministic, and a model can be
inserted or removed without changing the contracts. The models get the same
contracts as the humans.

**Withdraw the overclaim, keep the work.** The strongest validation artifact
in the repository is a retraction. Being exact about what is recorded and
conditional about what it establishes applies to the project's own claims
first.
