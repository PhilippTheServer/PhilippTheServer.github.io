---
layout: post
title: "daily 2.0: The Kitchen, Picnic, and the Human Money Loop"
subtitle: "The v1 prototype grew a kitchen: physical stock with expiry dates, confirmed cooking, stock-aware shopping arithmetic, and Picnic cart preparation. Sixteen releases in a week. I still do the checkout."
date: 2026-11-16 11:00:00 +0200
tags: [python, agents, automation, architecture, fastapi]
description: >-
  daily 2.0 adds a kitchen with physical, expiring stock, confirmed cooking,
  stock-aware shopping, and approval-first Picnic cart preparation — with
  the owner still doing the final checkout.
---

## The problem

[Last time](/posts/daily-meal-tracking-shopping-automation-mcp/)
I built the first version of *daily*, my personal health and diet system: a
language model operates it, I only look at it, and the shopping list is
planned meals minus a live pantry, with the pantry derived from an event
journal instead of being stored as a mutable number.

What v1 did not have was a **kitchen**. The pantry in v1 was an abstraction
that behaved politely. A real pantry is physical. Things in it expire. I
cook with them, in batches, some of it for later. I eat what I cooked, not
what I bought. And when the real stock runs out, I order from
[Picnic](https://picnic.nl/), a grocery delivery — which means the moment
the system touches shopping money, a very different set of rules applies.

The requirements for 2.0, in order of how often I would have been wrong
without them:

1. Stock is physical, it has expiry dates, and it can be wrong.
2. Cooking is a fact that happens or does not happen.
3. Units are not interchangeable.
4. The agent may prepare a cart. The agent may not pay.

Between 4 and 10 November, *daily* went from release 0.1.1 to 0.1.16 —
sixteen releases, twenty-five pull requests, most of them small and boring,
which is exactly how I want a food system to evolve.

## Working through it

### Stock is physical

The kitchen records **only quantities actually at home**, as lots with
expiry dates where known. The rules read like a list of things I would
otherwise have gotten wrong at 7 a.m.:

- **Adjust, don't append.** Recounting a lot adjusts the existing lot.
  Recording it again would add stock that does not exist.
- **Record waste rather than silently ignoring it.** A spoiled yoghurt is
  an event, not a rounding error. The journal keeps the correction chain
  from v1, and waste is one of its values.
- **Expired stock cannot cover future cooking.** A lot past its date is not
  a negative number waiting for an offset.
- **Missing metadata is debt, not a guess.** If inventory metadata is
  missing, it becomes explicit reconciliation debt — not a fake receipt, and
  not a rejected eating fact.

Delivery days default to Tuesday and Saturday. An unspecified delivery time
means *end of day*, which cannot cover cooking that happens earlier.
Genuine coverage gaps stay visible on the plan instead of being smoothed
over with optimistic arrivals.

### Cooking is a fact

Cooking routines can prepare fresh or in batches, producing stable cooking
runs and planned meals within a bounded horizon. Calendar queries only read
occurrences; nothing is generated on the fly.

The load-bearing rule: **cooking is confirmed only when it happened**, with
the actual yield, storage and expiry. That confirmation consumes the
ingredient lots *once* and creates a batch with fixed nutrient snapshots.
Eating a meal linked to that batch consumes prepared portions, not the
ingredients again. Eating out never debits home stock, while eating fresh at
home can allocate raw stock.

Movements are allowed only where they are still true. Moving an unexecuted
cooking run moves its linked open meal block with the same IDs. Cooked and
eaten facts **cannot move** — a meal eaten Tuesday is not a meal eaten
Thursday. A meal has a stable ID that links multiple partial intake chains;
the active, unretracted journal heads determine consumed quantities, because
a mutable "eaten" flag would be another balance to keep consistent, and I
have enough of those. Corrections and retractions adjust active usage
without recalculating unchanged historical nutrition, and already-eaten
meals plus cooked batches are historical facts, not editable rows.

### Units are a trust boundary

Recipes and inventory keep native `{amount, unit}` with unit one of
`g`, `ml` or `piece`. A product spec can link multiple SKUs to one
ingredient with sourced pack quantities or approved conversions. The rule
that ties the whole thing together: **never equate ml with g, pieces with
grams, or gross produce weight with edible yield.**

That one sentence is why the shopping list does not contain 0.4 avocados.
Legacy gram fields keep their original interpretation and provenance, old
nutrient snapshots are not rewritten, and food versions declare their
nutrient basis. When a pack weight is unknown, it stays unknown. Inventing
a conversion from pieces to grams would be a small act of hostility toward
my own kitchen, committed by the system.

### The arithmetic of "enough"

Shopping subtracts, in chronological order: usable same-unit stock, real
prepared portions, and trustworthy incoming quantities. Expired stock does
not count. And then the part v1 could not have: an **unresolved purchase
commitment is neither zero nor proven coverage**.

If I have ordered milk and it has not arrived, the shopping screen does not
show "buy milk" a second time, and it does not show "milk covered" as if
the carton were in the fridge. It shows *already ordered — coverage
unresolved*, with the order reference and the delivery time. Late deliveries
and overdue receipts remain explicit states. Buying the same thing twice
because a system could not tell "ordered" from "owned" is the kind of bug
that costs money instead of failing a test.

### Picnic, with a human in the money loop

The Picnic integration is where the approval-first design earns its keep.
The flow is:

1. **Search by product name. Choose a result explicitly.** Search only
   reads. Pack labels, article counts and multipacks are never converted
   automatically, and the assistant confirms the actual pack amount in an
   explicit unit — g, ml or piece. Unknown prices remain unknown;
   unavailable results cannot be selected.
2. **Generate a draft basket. Send it explicitly.** I review the draft, and
   only an explicit command sends it to the Picnic cart. Existing cart
   items I added myself are preserved as the baseline. No basket minimum or
   fee is hard-coded, and no filler items are added to reach one.
3. **I check out in the Picnic app.** The owner reviews the whole cart and
   completes the payment where the money actually moves. Nothing in *daily*
   checks out, pays, clears the cart, or retries an uncertain mutation.
4. **Read the evidence, don't assume it.** After checkout, the system can
   read Picnic's delivery summaries and per-delivery detail — the current
   list, not the complete order history. Product quantities there are
   Picnic article counts and display text: **never proof of received
   grams.** A completed upstream status does not prove actual delivered
   quantities.
5. **Confirm the receipt per line**, including shortages and substitutions.
   Arrival confirmation alone does not mark every line — or the local order
   — as fully received, and repeating the same receipt must not add stock
   twice.

Two operational details that matter more than they look. First, the Picnic
API is unofficial and changeable, so a timeout *after* a possible cart
write is reported as **UNCERTAIN**: the recovery path is to inspect the cart
and then act explicitly, never to retry concurrently. Second, the auth token
lives in an operator-owned file outside the repository, mode 0600, in a
mounted directory so atomic token replacement is visible to the process,
read on each use so a replacement needs no restart. The system is careful
with my grocery money in exactly the places where a retry loop would spend
it.

Raw address, payment and auth payloads are not stored as delivery evidence,
and address and payment fields are excluded from the reads. The delivery
evidence that is stored is sanitised — order references, status, window,
product lines.

### Sixteen releases

The week ran: CI on a self-hosted runner in the dev lab (the first release
was mostly that), the German mockups and logos that the redesign was
measured against, steady-activity planning targets, the kitchen with
stock-aware planning, portion planning, reviewed baskets, safe product
selection and the first meal forecasting, confirmed deliveries, the mockup
redesign as its own release, dated meals with recipe navigation, and the
pull that connected the whole workflow — planning, cooking, receipts,
consumption — into one chain. The release train is boring on purpose: each
release is one reviewable slice, and the shopping corrections got their own
fix releases because dated forecasts and safety notices are not the kind of
thing you ship "good enough".

## The solution

*daily* 2.0 is now what the title says it is: an **agent-operated,
human-viewed** system. The Python backend declares each operation once, and
it is reachable as a REST command or view for the Angular PWA and as an MCP
tool for the language model — the same single-source-of-truth move as
[GitGentic](/posts/gitgentic-an-agent-first-git-forge-from-one-operation-catalog/),
applied to a food system. The PWA renders every form from server schemas,
logs in with PKCE, and treats service-worker updates as a non-modal notice
that never reloads the form I am filling in.

The end-to-end shape: a recurring meal and prep template drives stock-aware
shopping. The kitchen records physical lots with expiry, cooking is
confirmed as fact, prepared portions are eaten, the shopping list subtracts
what is real, unresolved orders stay visibly unresolved, the Picnic basket
is prepared and sent on my explicit say-so, I pay in the Picnic app,
deliveries are read back as evidence, and receipts are confirmed per line.
The targets are steady, activity-based plans — steps and gym sessions
averaged over the week — with the honest caveat built in: all calorie
figures are rough estimates, not measured expenditure, to be compared with
the weight trend.

To be precise about the state of things: the Picnic API is unofficial and
can change under me, live German-account compatibility still needs
account-side validation rather than test suites that would place real
orders, and the search parsing fails closed on unknown response formats —
it would rather not find the product than guess a product ID.

## Conclusion

**The agent plans, the human pays.** An approval-first shopping system is
not a weaker automation; it is the only automation whose failure mode is an
empty cart instead of a duplicate order. The final checkout stays in the
Picnic app on purpose, and so does the final decision about every gram.

**Record the waste.** "Record waste rather than silently ignoring it" is the
single most useful sentence in the design. A system that cannot see its
mistakes in its own pantry will compound them, quietly, one yoghurt at a
time.

**Uncertainty is a state, not an exception.** UNCERTAIN mutations,
unresolved coverage, unknown prices, missing metadata as reconciliation
debt: the system has a named place for "I do not know yet", and that place
is where the money stays safe.

**Never equate ml with g.** Unit honesty is a trust boundary. Every
conversion the system is allowed to make is sourced, and every one it is not
is left as a visible gap.

**Sixteen releases in a week is a workflow, not a hero story.** Small,
boring, reviewable slices — with fix releases for the forecasting bugs —
beat one big kitchen in every way that matters, including the way I notice
what changed.
