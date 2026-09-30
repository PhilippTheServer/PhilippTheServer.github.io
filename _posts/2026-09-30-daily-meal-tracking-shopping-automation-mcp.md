---
layout: post
title: "Tracking Meals and Automating Shopping With an MCP Server"
subtitle: "daily is a diet system that a language model operates and I only look at. The prototype turned planned meals and a live pantry into a shopping list in eleven days. The rebuild stops storing the pantry at all."
date: 2026-09-30 01:00:00 +0200
tags: [agents, automation, api-design, architecture, python]
description: >-
  An MCP server for meal tracking and grocery shopping automation: planned meals
  minus live stock, and why the v2 rebuild derives stock from an event journal.
---

## The problem

Nobody abandons a food tracker because the maths is wrong. They abandon it because typing
"oats, 37 g" into a phone at seven in the morning, before coffee, is a small act of
hostility they are asked to repeat four times a day, forever. Every tracking app I have
used worked perfectly for about eleven days. After that it kept working perfectly for
nobody.

So the first requirement for *daily*, my personal health system, was that I do not type.
I tell a language model what I ate, in whatever words I have at that hour, and the model
logs it through an [MCP](https://modelcontextprotocol.io) server. A PWA exists so I can
see the result and fix things, but the primary user of the API is a language model.

Most of the time that model is Qwen3.8, self-hosted on atlas, the inference server from
[an earlier article](/posts/atlas-agentic-ops/). A food diary, a symptom log and the
state of one's digestion are about as personal as data gets, and they are exactly the
kind of context that should not leave the building by default. Sometimes it is Claude,
through the claude.ai connector. Both talk to the same MCP server,
and neither needs to know the other exists. That is rather the point of a protocol.

The second requirement followed from the first. Once the system knows what I eat, what I
plan to eat and what is in the kitchen, the shopping list is no longer a creative task.
It is subtraction. The design document puts the goal in one sentence I am still quite
fond of: planning, stock and shopping happen on their own, *so Philipp only cooks*.

This article covers how both halves work, what the first version got right and wrong,
and what the rebuild changes.

## Working through it

### Tracking with a language model as the input device

The prototype, daily v1, is a FastAPI application that serves a REST API for the PWA and
an MCP server at `/mcp` from the same process. Both authenticate against Keycloak. Both
are thin adapters over one layer of use cases, so the rules about what a valid meal looks
like are written once.

A meal is a list of `{off_code, grams}` items. The code is an
[Open Food Facts](https://world.openfoodfacts.org/) barcode, resolved and cached locally
on first use. Each item's nutrients are snapshotted at the moment of logging, so fixing a
product's figures later does not rewrite what I ate last Tuesday. The model searches for the
food, picks the usable hit, and logs it. I say "the usual breakfast, but half the oats",
and it copies yesterday's meal with one item changed.

Connecting the claude.ai side cost one evening and one line. The MCP authorization flow
reads the server's OAuth protected-resource metadata. If that metadata does not list
`scopes_supported`, claude.ai asks Keycloak for every scope the realm has, and Keycloak
answers `invalid_scope`. Advertising exactly `["openid"]` fixes it. I mention this mostly
because the error message points at Keycloak and the cause is in your own metadata.

### Shopping as subtraction

The shopping automation in v1 is built from four ideas.

**Stock is live.** The pantry holds grams per food. Every action that logs a meal deducts
the eaten grams from stock *in the same commit*: logging a meal, adding an item, copying
a meal, logging a recipe or logging a planned meal. It takes from the exact food first,
then from other foods with the same normalised name, and never goes below zero. A food
that reaches zero leaves the pantry.

**Names are keys, brands are noise.** The recipe says "Skyr", the fridge holds a
supermarket brand's skyr, and the barcode says those are different products. For
shopping they are not. Matching uses a deliberately stupid key:

```python
def food_key(name: str) -> str:
    return " ".join(name.casefold().split())
```

"Bananen" and " bananen " are now the same fruit. This fixed a real bug where a recipe
was reported as missing an ingredient that sat in the fridge under a different brand.

**Plans and batches say what will be eaten.** A plan is a recipe on a day, in a meal
slot, with a factor. A batch is meal prep: one recipe cooked once for up to fourteen
consecutive days. Cooking a batch deducts the stock for all remaining portions at once,
and eating a portion later deducts nothing more, because the rice was already used on
Sunday.

**The shopping list is plans minus stock.** By default the window runs from today to the
end of the next uncooked batch in each meal slot, which means one shopping trip per
prep. Each line has what is needed, what is on hand, what to buy, the number of packs
rounded up and their cost if the food has a price. A "bought" button adds the purchased
packs back to stock in one commit, so the loop closes without anyone typing grams.

Rounding up packs has one trap worth naming. In IEEE 754, `100 * 1.1` is
`110.00000000000001`, so `math.ceil` on a quotient that should be exactly one says two.
The prototype subtracts an epsilon before rounding up. Otherwise the list would buy an
extra pack of something every time a recipe had a factor of 1.1. It would be a
small tax on floating point, paid in yoghurt.

### Where the prototype creaked

v1 went from an empty repository to release 1.4.0 in eleven days and 34 squash-merged
pull requests. It worked, which is the most dangerous thing a prototype can do, because
working is the argument everyone uses for not rebuilding it. Three problems were clear
by the end.

**Stock was a number patched in place.** The README says it plainly: editing or deleting
a meal does not put stock back. That was a reasonable prototype decision, because
reversing a deduction that took from two rows of two brands is not trivial. It also
means every correction leaves the pantry a little further from the kitchen. The
shopping list is only as good as the stock it subtracts from, so a slowly drifting stock
makes a slowly wrong list.

**The tool list was shaped like the database.** v1 exposes 41 MCP tools: create, read,
update and delete for meals, drinks, feelings, sleep, steps, supplements, stock, plans,
batches, and so on. Each tool is a hand-written adapter that assembles arguments and
calls a use case built by a factory in `deps.py`, which grew to 1,498 lines. Adding a
feature meant a use case, a REST route, an MCP tool and a factory, and the MCP tool was
the one that got forgotten. A model does not care about tables: breakfast with a coffee
and a vitamin D tablet was `log_meal`, `log_drink` and `log_supplement`, three round
trips and three chances to get the timestamp slightly different each time.

**Derived state lived next to facts.** Stock, day totals and meal nutrients were all
stored, and each had its own rules about when it changed. Any of them could disagree with
the log it came from, and nothing would notice.

## The solution

daily2 is the rebuild, and it starts from one principle: **facts are append-only, and
everything else is derived.** What happened is stored as an event and never overwritten.
A correction is a new event that supersedes the old one, and a deletion is a retraction.
Day summaries, gauges and later stock and shopping lists are computed from the facts.

The events form correction chains. Each event carries the `chain_id` of its original and
the `supersedes` id of the version it replaces. Only the head of a chain counts. A
correction sent against a version that is no longer the head is refused with
`409 stale_head`, so the model and I cannot overwrite each other's edits without noticing.

For the pantry, this removes the drift problem entirely. Stock is no longer a number that
has to be kept correct. It is purchases minus consumption, computed over the heads of the
chains. Correcting breakfast from 80 g of oats to 40 g corrects the stock too, because
there was never a second copy to update. Here is the difference as a runnable script
(Python 3.13, standard library only):

```python
"""Stock as a projection of facts, and a shopping list as arithmetic on top of it."""

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class Event:
    id: int
    kind: str  # "purchase" or "intake"
    food: str
    grams: float
    supersedes: int | None = None


def food_key(name: str) -> str:
    return " ".join(name.casefold().split())


def heads(events: list[Event]) -> list[Event]:
    replaced = {e.supersedes for e in events if e.supersedes is not None}
    return [e for e in events if e.id not in replaced]


def stock(events: list[Event]) -> dict[str, float]:
    have: dict[str, float] = {}
    for e in heads(events):
        sign = 1 if e.kind == "purchase" else -1
        have[food_key(e.food)] = have.get(food_key(e.food), 0.0) + sign * e.grams
    return {k: max(0.0, g) for k, g in have.items()}


def ceil_div(amount: float, size: float) -> int:
    return max(0, math.ceil(amount / size - 1e-9))


RECIPE = {"Oats": 80, "Skyr": 150, "Banana": 120}  # grams per portion
PACKS = {"oats": (500, 1.29), "skyr": (500, 1.49)}  # grams per pack, euros


def shopping_list(events: list[Event], portions: float) -> list[tuple]:
    have = stock(events)
    lines = []
    for food, grams in RECIPE.items():
        key = food_key(food)
        need = grams * portions
        buy = max(0.0, need - have.get(key, 0.0))
        pack = PACKS.get(key)
        packs = ceil_div(buy, pack[0]) if pack else None
        cost = round(packs * pack[1], 2) if pack else None
        lines.append((key, need, have.get(key, 0.0), buy, packs, cost))
    return lines


journal = [
    Event(1, "purchase", "Oats", 500),
    Event(2, "purchase", "Skyr", 250),
    Event(3, "purchase", " banana ", 240),
    Event(4, "intake", "Oats", 80),
    Event(5, "intake", "Oats", 40, supersedes=4),  # it was half a portion
]

# The v1 way: a number, decremented on logging, left alone on correction.
pantry = {"oats": 500.0}
pantry["oats"] -= 80
print(f"patched in place: oats {pantry['oats']:.0f} g")
print(f"derived from facts: oats {stock(journal)['oats']:.0f} g")

print(f"\n{'food':8}{'need':>6}{'have':>6}{'buy':>6}{'packs':>7}{'cost':>7}")
for key, need, have, buy, packs, cost in shopping_list(journal, portions=4):
    packs, cost = ("-", "-") if packs is None else (packs, f"{cost:.2f}")
    print(f"{key:8}{need:6.0f}{have:6.0f}{buy:6.0f}{packs:>7}{cost:>7}")

assert stock(journal)["oats"] == 460
assert ceil_div(100 * 1.1, 110) == 1, "float noise must not buy a second unit"
assert math.ceil(100 * 1.1 / 110) == 2
```

```bash
docker run --rm -v "$PWD/pantry.py:/pantry.py:ro" python:3.13-alpine python /pantry.py
```

```text
patched in place: oats 420 g
derived from facts: oats 460 g

food      need  have   buy  packs   cost
oats       320   460     0      0   0.00
skyr       600   250   350      1   1.49
banana     480   240   240      -      -
```

The patched pantry is forty grams short of the real one after a single correction. Over
a few weeks of "actually it was half" that gap turns into a shopping list that buys oats
you already have. The derived pantry cannot drift, because it is recomputed from the same
journal the day view reads.

### One operation, two doors

The second change is how the API reaches the model. In daily2 every write is a named
**command** and every read a named **query**, declared once in a feature's `routers.py`:

```python
query(
    "get_context",
    ContextQuery,
    ContextOut,
    functions.get_context,
    "START HERE in every conversation. The owner's day (default today): summary with the "
    "four gauges (kcal, protein, carbs, fat vs targets), timeline, latest weight and "
    "14-day trend, symptoms of the last 3 days, sync status and warnings.",
    view="today",
)
```

That one declaration becomes a REST route (`GET /api/v2/views/today`), an MCP tool with
the input model's fields as its arguments, a JSON Schema, and OpenAPI documentation for
every error it declares. Queries are marked read-only for MCP clients, and destructive
commands say so. The description is written for the model: it says *when* to use the
tool, not what the function is called. The server's instructions add one rule for the
whole conversation: start with `get_context`, and never guess a food id.

The machinery around each operation is where the MCP integration became what it should
have been from the start:

- **24 tools instead of 41**, shaped like tasks rather than tables. `log_events` takes
  up to 50 facts of any kind in one call, so a breakfast, a coffee and a supplement are
  one round trip.
- **Every command returns `effects`**, the days it changed, and warnings. The model knows
  what to re-read without guessing.
- **Every command accepts an `idempotency_key`.** A repeated call returns the stored
  response instead of logging lunch twice. Models retry, just like networks.
- **The source is taken from the token**, never from the body. An event logged through
  MCP says `claude`, one from the PWA says `app`, and the history of an entry reads
  "corrected by claude at 14:02". The label comes from the MCP client, not from the
  model behind it, so whatever the local model logs over MCP is filed under `claude`
  as well. It is the least
  accurate word in the codebase, and I have made my peace with it.
- **Errors have one shape**, `{code, message, field?}`, over both doors. Over MCP they
  come back as `isError` with the same body, so the model sees `stale_head` rather than a
  stack trace.

"The UI and the model can do exactly the same things" is a sentence that is easy to put in
a design document and hard to keep true. So a contract test logs one event of every kind
through REST and the same events through MCP, reads the day back through each, and
asserts the two views are identical except for the source. A second test pins the exact
tool list, so a tool that disappears fails CI rather than a conversation.

### What is and is not built

To be precise about the state of things: daily2's first sub-project, Core, is done and
deployed next to v1. It has the journal, the versioned food catalogue, profile and
targets, the gym-bro workout sync and the MCP server described above. The kitchen ledger,
the planner and procurement are sub-projects two to four, each with its own spec. Until
they ship, the shopping list in this article is v1's, and v1 keeps running beside v2.

The migration script that moves v1 data into v2 says so in its docstring. It writes every
record through the v2 command handlers, with an idempotency key per source row, so a
second run creates nothing. It deliberately leaves stock, plans and batches behind. A
pantry that has been patched in place for a month is exactly the kind of state the
rebuild exists to stop trusting.

The loop v2 is built towards is in the design document, and I like it enough to quote it:

```text
Goal → Targets → Plan → Requirements → Shortfall (Requirements − Stock) → Order
  ↑                                                                        ↓
Adjust ← Evaluate ← Day summary ← Intake ← Eat portion ← Cook batch ← Stock ← Delivery
```

Every box on the lower row is a fact. Every box on the upper row is derived from them.

## Conclusion

**Make the model the input device, and tracking survives week two.** The value of daily
did not come from better nutrition maths. It came from logging becoming a sentence
instead of a form. MCP is what makes that possible without building a chat interface of
my own, and because it is a protocol rather than a vendor feature, the same server serves
a self-hosted Qwen for everyday logging and Claude through claude.ai.

**Shopping is subtraction, as long as you trust both operands.** Planned meals, batches
and pack sizes make the list precise. A pantry kept as a mutable number makes it
precisely wrong, slowly. Deriving stock from an append-only journal is the only version
where a correction to breakfast also corrects Saturday's shopping.

**Design the MCP surface for tasks, and generate it.** Forty-one hand-written CRUD tools
were a mirror of the schema, and a mirror is exactly as wide as the thing it reflects.
Declaring each operation once and serving it as both REST and MCP took the tool count
from 41 to 24, and put idempotency, effects and a single error shape on every call without anyone
remembering to add them. A parity test keeps "the model can do what the app can" true.

The prototype took eleven days and answered the only question a prototype should: would
I actually use this? I did, every day, which is also how I found out where it was wrong.
