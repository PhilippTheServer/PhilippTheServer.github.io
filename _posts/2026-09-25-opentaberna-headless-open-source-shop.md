---
layout: post
title: "OpenTaberna: A Headless, Open-Source Web Shop You Run Yourself"
subtitle: "Why a friend and I are building a shop that is an API first and a website second, and why it is free to run on your own server."
date: 2026-09-25 00:10:00 +0200
tags: [architecture, api-design, docker, databases]
description: >-
  OpenTaberna is a headless, Apache-2.0 web shop: one FastAPI backend, PostgreSQL
  and replaceable frontends, self-hosted with Docker Compose.
---

## The problem

*Taberna* is Latin for a shop. Depending on the century, it is also Latin for a pub, and
the first version of [an earlier article on this site](/posts/opentaberna/) called
OpenTaberna a hospitality system. The Latin supports that reading. The code does not, and
the article has since been corrected. It is a web shop, and I am one of its two founders,
together with [Malte Kottmann](https://github.com/maltonoloco).

The idea came out of watching small shops choose their software. At the scale of a few
hundred orders a month, the choice usually comes down to two options, and neither is good:

**Rent a shop.** A hosted platform works on day one. It charges a monthly fee, and often a
share of every sale on top. Its data model is the platform's, its checkout is the
platform's, and the export you get when you leave is a CSV that assumes you are not going
to do anything clever with it. None of that matters until the shop grows. Then all of it
matters at once.

**Install a shop.** A self-hosted shop system avoids the rent, but it gives you a monolith.
The storefront, the admin, the business logic and the templating language all live in one
process and one upgrade cycle. Changing how a product page looks means learning that
system's theme engine. Changing what happens after "buy" means writing a plugin against
hooks that the next major version renames. Many of these systems are also open core: the
features a growing shop needs sit in the edition with the licence fee.

Both options make the same assumption: the website *is* the shop. I think that is the
mistake. The website is one client of the shop. The shop itself is what knows the price,
holds the stock, takes the money, and gets a parcel to the right door.

## Working through it

### Headless, meaning the shop is an API

"Headless" gets used loosely enough to mean nothing, so here is what it means for us. The
shop is an HTTP API with an OpenAPI description, and *everything* goes through it. The
storefront customers see is a client of that API. So is the back office. So is anything
you write next. Nothing is rendered on the server, and there is no template language to
learn. If you can make an HTTP request, you can build a frontend.

The sceptical reply is that this is just a monolith with extra steps. The difference shows
up when you change something. In a coupled shop, the look and the logic sit in the same
deployable, so every change to either one risks both. When the shop is an API, you can
redesign the storefront every week without touching the code that decrements stock, and a
bug in your CSS cannot double-charge anyone.

### Open source, meaning actually open

The licence is Apache 2.0, for the backend and for both frontends. There is no community
edition with the useful parts moved into a paid one. Checkout, stock reservations, DHL
labels, returns and the back office are all in the repository, because a shop without them
is a demo.

The business model is the one that stays honest with that licence: we sell our time, not
the code. You can run OpenTaberna yourself for free. You can pay us to install it on your
server. Or you can pay us to run it on servers in Germany and be the ones who get paged at
three in the morning. Whichever you choose, the software is the same.

### Boring storage, on purpose

Everything the shop knows lives in PostgreSQL, in tables with ordinary names. There is no
proprietary blob, no key-value soup and no binary format a future version might stop
reading. The exit strategy is `pg_dump`, which I would argue is the best exit strategy any
software can offer. The less you are locked in, the easier it is to decide to stay.

## The solution

OpenTaberna today is one API, one worker and two frontends:

| Part | What it is |
| --- | --- |
| API | FastAPI on Python 3.14, versioned under `/v1`, OpenAPI at `/openapi.json` |
| Worker | ARQ on Redis: label generation, reservation expiry, the outbox sweep |
| Database | PostgreSQL 17 |
| Identity | Keycloak, with separate clients for the storefront and the back office |
| Files | S3-compatible object storage for product images and shipping labels |
| Payments | Stripe, confirmed by webhook |
| Shipping | DHL, behind a carrier interface |
| Documents | Paperless-ngx, behind the admin API |
| Frontends | Angular 22 and Tailwind 4: a storefront and an admin |

The whole development stack is one Compose file in the
[API repository](https://github.com/OpenTaberna/fastapi). Clone it, copy the example
environment, and bring it up. I started it on my laptop while writing this article, and
the API described itself back:

```bash
git clone https://github.com/OpenTaberna/fastapi.git opentaberna-api
cd opentaberna-api
cp .env.example .env
docker compose -f docker-compose.dev.yml up -d

curl -s localhost:8000/health
# {"status":"ok","timestamp":"2026-09-25T00:06:15.772558Z"}

curl -s localhost:8000/openapi.json | python3 -c '
import json, sys
d = json.load(sys.stdin)
ops = [(p, m) for p, v in d["paths"].items() for m in v]
admin = [o for o in ops if o[0].startswith("/v1/admin")]
print(d["info"]["title"], d["info"]["version"])
print(len(d["paths"]), "paths,", len(ops), "operations,", len(admin), "of them back office")
'
# OpenTaberna API 0.1.0
# 51 paths, 71 operations, 47 of them back office
```

That last line is the thesis of the [next article](/posts/opentaberna-order-processing-first/)
in one number. Two thirds of the API deals with what happens *after* someone clicks "buy".

The data is just as easy to inspect. Everything the shop knows fits in fourteen tables,
and not one of them needs explaining:

```bash
docker exec opentaberna-db psql -U opentaberna -d opentaberna -Atc \
  "select table_name from information_schema.tables
   where table_schema = current_schema() order by 1"
# addresses
# customers
# frontend_errors
# inventory_items
# items
# order_items
# orders
# outbox_events
# payments
# returns
# shipments
# stock_reservations
# storefront_events
# webhook_events
```

`outbox_events` and `webhook_events` are the two tables that keep the shop honest when a
network is not. They get their own articles:
[deduplicating payment webhooks](/posts/stripe-webhook-idempotency/) and
[the transactional outbox](/posts/transactional-outbox/). Stock reservations, which stop
two customers from buying the last mug at the same moment, are covered in
[a check constraint that refuses to oversell](/posts/stock-reservation-check-constraint/).

## Conclusion

**The website is a client, not the shop.** Once the shop is an API, the storefront can be
thrown away and rebuilt without risking the part that handles money. For a small business,
being able to redesign the site without a migration project is the whole point of going
headless.

**An open-source shop has to include the unglamorous parts.** A free catalogue with a paid
checkout is a sales funnel with a licence file attached. Payments, stock, labels and
returns are in the Apache-2.0 repository because those are what a shop needs to be a shop.

**Portability is a feature you ship on day one.** Plain PostgreSQL, a Compose file and a
published OpenAPI document mean nobody has to trust us to stay a good vendor. That makes it
a lot easier to trust us. If you want to look for yourself, the code is at
[github.com/OpenTaberna](https://github.com/OpenTaberna) and the project lives at
[opentaberna.de](https://opentaberna.de).
