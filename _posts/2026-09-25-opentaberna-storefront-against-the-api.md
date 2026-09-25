---
layout: post
title: "Building a Storefront Against a Finished Shop API"
subtitle: "When the shop is an API with an OpenAPI document, the frontend is an ordinary web project: a typed client, a few pages, and no theme engine."
date: 2026-09-25 00:30:00 +0200
tags: [frontend, api-design, testing]
description: >-
  Adapting an OpenTaberna storefront: a 37-line API client, TypeScript types
  generated from OpenAPI, and a compiler that catches the typo before users do.
---

## The problem

Every shop eventually wants to look like itself. With a coupled shop system, that wish
turns into a project. You learn the theme engine and its template language, find out which
blocks can be overridden and which ones only the vendor gets to touch, and then you
discover that the product page you want needs data the template context does not expose.
The fix is a plugin. The plugin needs a hook. The hook changes in the next major version,
and the upgrade becomes a second project.

Worse, the frontend and the logic ship together, so every redesign is also a deployment of
checkout. Anyone who has watched a CSS change break payment on a Friday afternoon knows
what that coupling costs.

In the [first article](/posts/opentaberna-headless-open-source-shop/) I argued that the
website is a client of the shop, not the shop itself. This article is the practical half of
that argument. If OpenTaberna's API is finished, and the
[order handling behind it](/posts/opentaberna-order-processing-first/) is already
automated, how much work is left for someone who wants their own storefront?

## Working through it

### The contract is published, not implied

The API describes itself. FastAPI generates an OpenAPI 3 document from the same Pydantic
models that validate requests, so the description cannot disagree with the code. It is
built from the code. [A contract test](/posts/openapi-docs-contract-test/) keeps the prose
in it honest too. Every storefront-facing operation lives under `/v1`, and the admin
operations under `/v1/admin` need a token issued to the admin client, so a storefront
cannot wander into the back office even by accident.

A storefront needs a surprisingly small slice of this:

| Need | Endpoint |
| --- | --- |
| Product list and detail | `GET /v1/items/`, `GET /v1/items/{item_uuid}` |
| Product images | `GET /v1/items/{item_uuid}/image` |
| Customer profile and addresses | `/v1/customers/me`, `/v1/customers/me/addresses` |
| Cart to order | `POST /v1/orders/` |
| Pay | `POST /v1/orders/{order_id}/checkout`, returns a Stripe `client_secret` |
| Returns | `POST /v1/orders/{order_id}/returns` |

Login is standard OIDC with PKCE against Keycloak, and payment is Stripe Elements with the
client secret the checkout call returns. Neither is ours to reinvent, and we did not.

### The reference storefront is small on purpose

Our own [storefront](https://github.com/OpenTaberna/frontend) is Angular 22 with Tailwind 4.
Its entire API client is one file of 37 lines with twelve methods, one per call it makes.
Two of them:

```typescript
items(skip = 0, limit = 50) { return this.http.get<ItemPage>(`${this.base}/items/`, { params: new HttpParams().set('skip', skip).set('limit', limit).set('status', 'active') }).pipe(map((page) => ({ ...page, items: page.items.map(withApiMedia) }))); }
checkout(customerId: string, orderId: string) { return this.http.post<CheckoutResponse>(`${this.base}/orders/${orderId}/checkout`, {}, { headers: { 'X-Customer-ID': customerId } }); }
```

There is no business logic in there, and that is deliberate. Totals, stock and order state
are the API's responsibility. The storefront shows what it is told and asks before it
acts. When our storefront gets something wrong, the worst outcome is an ugly page. The
payment side is not at risk.

The [admin frontend](https://github.com/OpenTaberna/admin_frontend) follows four rules that
are worth copying: services live in `core/`, presentational components in `shared/ui/`
never call the API, feature pages never touch `HttpClient` directly, and templates contain
no design literals. That layering has
[its own article](/posts/angular-layering/). Neither frontend is special, and that is the
point. You are free to replace either.

### Types for free, and a compiler that reads the contract

A published OpenAPI document means you do not have to write interface definitions by hand.
Generate them, and the compiler knows the API's shapes as precisely as the server does.

## The solution

Here is a storefront catalogue in the smallest form I could make that was still honest,
run against the development stack from the first article with two products in it. No
framework, no build step:

```javascript
// catalogue.mjs
const api = process.env.API ?? 'http://localhost:8000/v1';

const page = await fetch(`${api}/items/?status=active&limit=50`).then((r) => r.json());

for (const item of page.items) {
  const price = (item.price.amount / 100).toFixed(2);
  console.log(`${item.name.padEnd(28)} ${price} ${item.price.currency}`);
}
console.log(`${page.page_info.total} products`);
```

```bash
node catalogue.mjs
# Enamel mug, 350 ml           14.90 EUR
# Server log book, dot grid    9.90 EUR
# 2 products
```

That is a working product listing. Prices are integers in cents, so there are no floating
point surprises. Replace `console.log` with DOM output and you have a page. The rest of a
real storefront is the same idea repeated: a product page, a cart held in the browser, a
login redirect, and a Stripe Elements form.

For anything bigger than a demo, generate the types. `openapi-typescript` reads the running
API and writes one declaration file:

```bash
npx openapi-typescript@7.13.0 http://localhost:8000/openapi.json -o api.d.ts
# ✨ openapi-typescript 7.13.0
# 🚀 http://localhost:8000/openapi.json → api.d.ts [209.3ms]

wc -l api.d.ts
# 10292 api.d.ts
```

That is ten thousand lines of interfaces nobody had to write, in two tenths of a second.
Now the same catalogue, typed. It contains a deliberate typo of exactly the kind that
otherwise ships as an empty price column:

```typescript
// catalogue.mts
import type { components } from './api.js';

type ItemPage = components['schemas']['PaginatedResponse_ItemResponse_'];

const api = 'http://localhost:8000/v1';

export async function catalogue(): Promise<ItemPage['items']> {
  const res = await fetch(`${api}/items/?status=active&limit=50`);
  const page: ItemPage = await res.json();
  return page.items;
}

const items = await catalogue();
for (const item of items) {
  console.log(item.name, item.price.amount, item.price.curency);
}
```

```bash
npx -p typescript@7.0.2 tsc --noEmit --target es2022 --module nodenext \
  --strict --lib es2022,dom catalogue.mts
# catalogue.mts(15,56): error TS2551: Property 'curency' does not exist on type
#   '{ amount: number; currency: string; includes_tax: boolean;
#      original_amount?: number | null | undefined;
#      tax_class: "none" | "reduced" | "standard"; }'.
#   Did you mean 'currency'?
```

The compiler knows the API's price model, down to the three tax classes, because the API
told it. Fix the typo and `tsc` exits 0. Regenerate `api.d.ts` in CI, and a backend change
that would break your storefront breaks your build first, while it is still cheap.

That is the whole adaptation story. Pick any framework, or none. Point it at `/v1`, sign
in against Keycloak, and let Stripe handle the card field. Stock, payment confirmation,
labels, tracking emails and returns keep working whatever the page looks like, because the
page was never involved in any of them.

## Conclusion

**A finished API turns a redesign into a web project.** No theme engine, no template
language, no hooks to wait for. If you know how to build a website, you already know how
to build an OpenTaberna storefront.

**Keep the storefront stupid.** Our reference client is 37 lines because it contains no
decisions. Everything that can cost money lives behind the API, which is the only place it
can be tested once and trusted everywhere.

**Let the contract do the typing.** A generated `api.d.ts` makes the OpenAPI document the
single source of truth for both sides. The typo you would have found in production turns
up at compile time instead.

The storefront we ship is a starting point, not a requirement. If you build a different
one, I would honestly like to see it. The repositories are at
[github.com/OpenTaberna](https://github.com/OpenTaberna).
