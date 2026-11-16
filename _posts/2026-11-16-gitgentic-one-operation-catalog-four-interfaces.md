---
layout: post
title: "GitGentic: One Operation Catalog, Four Interfaces"
subtitle: "One typed operation catalog in Rust generates the REST API, the MCP server, the CLI and the browser client. 37 entities, 142 operations, eight catalog rules, and a frontend that is only allowed to draw."
date: 2026-11-16 10:00:00 +0200
tags: [rust, agents, api-design, architecture, frontend]
description: >-
  GitGentic is a self-hosted git forge for agents: one Rust operation
  catalog generates REST, MCP, CLI and browser client; parity tests fail
  the build on drift.
---

## The problem

Coding agents already live in git repositories. They read code, open
branches, write commits, answer review comments, and merge. But every forge
they work in was designed for humans: the web UI is the front door, the REST
API is an afterthought documented for integrators, and the MCP server, if
there is one, is someone's weekend project with an arbitrary subset of
capabilities.

The mismatches are not cosmetic. Many agent harnesses never show HTTP headers
to the model, so rate-limit state in a header is information that does not
exist. A model that cannot block has to poll, and polling is the most
expensive thing an agent can do with its budget — one workflow I have seen
cited goes from about 150,000 tokens to 2,000 when a CLI filters the output
before it enters context. Error messages written for a human on a screen —
"403 Forbidden" — do not tell a model what to do next. And when the web app
and the API drift apart, the agent and the human quietly work in two
different repositories.

The requirement for **GitGentic**, a self-hosted git forge, is therefore a
single sentence: **anything you can click, an agent can call.** Not
approximately, not for a curated subset of endpoints — by construction.

## Working through it

### One catalog, four projections

The answer is that the forge does not *have* four interfaces. It has one
**operation catalog**, defined in Rust, and the interfaces are generated:

- a REST API with OpenAPI 3.1,
- an MCP server,
- a CLI with JSON output,
- the browser's TypeScript client.

An operation is written once in the contract crate: a trait with a const
name, summary, safety class (Read / Write / Destructive), scope, and HTTP
mapping, plus associated `Input` and `Output` types and examples.

```rust
pub trait Operation {
    /// Dotted name, for example `pull_request.merge`.
    const NAME: &'static str;
    const SUMMARY: &'static str;
    const SAFETY: Safety;
    /// Token scope required to call the operation.
    const SCOPE: &'static str;
    /// Method and path template below `/v1`.
    const HTTP: (Method, &'static str);

    type Input: DeserializeOwned + JsonSchema;
    type Output: Serialize + JsonSchema;

    fn examples() -> Vec<Example<Self::Input, Self::Output>>;
}
```

An `xtask`
target generates `openapi.json`, the TypeScript client, the CLI reference,
and an `llms.txt` — a one-page guide for agents that find the API by reading
it.

The catalog currently holds **37 entities and 142 operations**. (The plan
says "about 150". The discrepancy is recorded as unresolved decision U-13
rather than papered over — more on that below.)

What makes this more than code generation is the **parity test**: if an
operation is missing from any projection, the build fails. The REST surface,
the MCP surface, the CLI and the browser client cannot drift, because
drift is a compile-time error, not a ticket.

### Eight rules for an agent-facing API

The catalog follows eight rules, most of them born from watching models fail
against human-shaped APIs:

1. **Speak GitHub's vocabulary.** Models have priors for pulls, head, base,
   state. Inventing synonyms taxes every prompt.
2. **Task-shaped reads.** `pull_request.get_context` returns the description,
   changed files, open review threads, failing checks and permitted actions
   in one call, because that is the unit of work an agent actually does.
3. **The caller chooses the size.** `view=summary|standard|full`, line
   ranges, `max_bytes` — and when output is truncated, the response says so
   and carries a `next_cursor`.
4. **Everything in the body.** Cursors, rate-limit state, warnings: JSON
   fields. If the harness never shows the model the headers, the headers do
   not exist.
5. **Return what can be done next.** Every resource carries `actions`: each
   operation allowed now, or the `blocked_by` reasons it is not.
6. **Make errors teach.** RFC 9457 problem-details with a stable code, field
   errors listing allowed values, a `hint` naming the next useful call, a
   `retryable` flag, and a `request_id`.
7. **Retries and races are safe.** Every write accepts an `idempotency_key`,
   an `expected_version`, and a `dry_run`.
8. **Never make an agent poll.** `event.wait` blocks until a matching event
   or a timeout — for example, CI finishing. The model sleeps for free
   instead of burning turns checking.

Rule 8 is the one I am least likely to give up. A forge that makes its agents
poll is a forge billing them by the wasted minute.

### Agents are first-class actors

The data model has an `Actor` entity with kinds `human`, `agent`,
`service` and `system` — Jenkins is a service actor, not a special case.
Every non-human actor has a **responsible owner** through an `AgentProfile`,
so "the agent merged it" always resolves to a person. A `Run` groups every
write and event from one agent task, so the UI can show what an agent did
and why, the way a commit shows what a human did.

Tokens are scoped to operations and repositories. Agents merge like any
other actor with write access, and per-repository rulesets can require human
approval — **off by default**, a decision that is recorded in the plan with
the exact date it was made. The plan is written to be audited by the agents
it governs.

### Prompt injection is a design constraint

A public forge has a particular threat shape: anyone can write an issue, and
agents can merge. So the interface separates channels by contract. Guidance
lives only in `hint` and `actions` fields; user-supplied text lives only in
`body` fields. Every piece of content carries `author.kind` and
`author_association` — owner, member, collaborator, contributor, or none —
so a model can weigh an instruction by who wrote it. Agent tokens on public
repositories run `trusted_only` on by default, which drops content from
actors without write access. GitHub's own MCP server ships something similar
in "lockdown mode"; it is no longer an exotic idea, which is the point.

### A frontend that is only allowed to draw

The split rule: **the API owns every decision, the frontend owns every
pixel.** The API emits no markup; the frontend decides nothing. The
enforcement is structural, not cultural:

1. The API has no HTML responses, and a dependency ban keeps templating
   crates out of the backend workspace.
2. The frontend's only network path is the generated client; a lint rule
   bans `fetch` anywhere else.
3. Frontend state is a cache of server responses plus transient UI state.
4. **Contradiction tests** feed the UI nonsensical API answers —
   `merge.allowed: true` on a closed pull request — and the UI must follow
   the API, because in production the API is the only thing it can trust.
5. Every UI e2e test has a CLI twin that runs without a browser.

The frontend is a static SolidJS app — I chose the 2.0 release candidate,
about 7 KB min+gzip, over React at roughly 45, Leptos was ruled out because
shared Rust types would invite logic across the boundary, and htmx-style
templating was ruled out because it would make the API emit markup. Public
pages are server-rendered by a small Node service that calls the REST API
with **no token**, so a rendered page can only ever show what an anonymous
visitor may see. Private data cannot leak into the sitemap.

### Scaffolded, not implemented

The honest status, as the repository says: the contracts are formalized and
validated in CI; the nine Rust crates and the SolidJS frontend are compiling
stubs with dependency rules enforced; no git logic runs yet. The modeling
layer — JSON Schema 2020-12, Mermaid state diagrams, `operations.json`,
`errors.json` — is checked by a validator that resolves `$refs` under Ajv
strict mode, confirms the 37 entities and ID prefixes against the plan,
checks the CLI exit codes, validates every example, and verifies
`x-source` anchors back to the plan headings. Where an artifact restates the
plan, the validator checks the two agree: one meaning in two places, rather
than two meanings.

Thirty-six open decisions are numbered U-01 through U-36 — including the
example ID that looks like truncated Crockford base32 instead of a UUID, and
a crate-dependency line that reads literally differently from what anyone
means. They are modeled as `unresolved`, not silently decided by whichever
document was written last.

The implementation plan is 38 weeks of focused solo work across six phases,
"an estimate, not a commitment", and each phase ends with a **gate scenario
an agent must pass without help** — for phase 3: an agent takes an issue to a
merged pull request on its own, branch, commits, review comments, fixes,
merge. The verification layer has a rule I have never seen stated more
clearly anywhere: **when the small model fails a task the others pass,
change the interface** — a description, an error hint, or a missing context
operation — and read the failed transcripts. The interface is the product.
The model is the customer.

## The solution

What exists in the repository today is the contract, and the contract is the
interesting part. `PLAN.md` is 648 lines and authoritative for requirements;
the modeling layer formalizes it into machine-checkable artifacts; the Rust
workspace defines the crate graph — a contract crate with no I/O and no
logic, a core crate with one handler per operation, git and storage
adapters, three projection crates, a server, and the `xtask` generator; the
SolidJS shell sits on the other side of the generated client. CI runs the
model checks on a self-hosted runner in the dev lab, the way everything else
I run does.

The stack underneath the catalog is deliberately boring: axum and tokio,
Postgres through sqlx with `LISTEN/NOTIFY` waking `event.wait`, the stock git
binary for transport and merges — the most battle-tested parser for untrusted
pack data — gitoxide for reads with a subprocess fallback per function,
`object_store` for blobs, passkeys instead of passwords, and Jenkins reached
through webhooks or the branch-source plugin, because its plain git source
knows branches, not pull requests. Git stays standard throughout: smart HTTP
at `/{owner}/{repo}.git`, PR heads at `refs/pull/{number}/head`, and push
output that prints the next step, because agents read it.

What does not exist yet is everything else. Phase 0 is three weeks of no
business logic: the contract crate, the generated artifacts, a mock server
that answers every operation with its own examples, the permission matrix,
the error catalog, the frontend shell, and an evaluation harness with the
first 20 agent tasks. After that comes the long part, one gate scenario at
a time.

## Conclusion

**An interface is a contract the agent signs.** If the REST, MCP, CLI and
browser surfaces are four hand-maintained copies of one capability set, they
will disagree, and the model will notice before the humans do. Generate all
four from one typed catalog and disagreement becomes a build failure.

**Design for the harness, not the protocol.** Headers that the harness never
shows the model are not part of the API. Polling is not a feature the model
needs; it is a tax the API imposes. Every rule in the catalog is a small
answer to a failure mode that only appears when the client has no eyes.

**The small model is the acceptance test.** When three models run the same
task through the same interface and only the small one fails, the interface
has a bug. The transcripts say where.

**Record the open decisions.** Thirty-six numbered "unresolved" entries are
worth more than thirty-six quietly picked defaults, because the defaults
will be found out anyway — better to find them in a validator than in
production.
