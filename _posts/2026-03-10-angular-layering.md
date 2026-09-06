---
layout: post
title: "Layering an Angular Application: core, shared, features and the Rule That Holds"
subtitle: "A folder convention nobody enforces is a convention nobody actually follows"
date: 2026-03-10 09:00:00 +0200
tags: [frontend, architecture]
description: >-
  Splitting an Angular application into core, shared and feature folders is common
  advice, but without something enforcing the dependency direction between them an API
  call ends up inside a presentational component the first time someone is in a hurry.
  This article states the one rule that actually needs to hold, and enforces it with a
  lint check a reader can run and watch fail.
---

## The problem

`core/`, `shared/` and `features/` is a folder layout most Angular teams arrive at
independently, because it maps cleanly onto real distinctions: singleton services that
exist once for the whole app, presentational building blocks reused across screens, and
the screens themselves. Written down as a diagram it looks settled.

It stays settled exactly until someone is fixing a bug against a deadline and the
fastest path is to call `HttpClient` directly from inside a component that is meant to be
a dumb, reusable table:

```typescript
// Broken. Do not copy this.
// shared/components/orders-table/orders-table.component.ts
@Component({ selector: "app-orders-table", template: `...` })
export class OrdersTableComponent {
  orders: Order[] = [];
  constructor(private http: HttpClient) {
    this.http.get<Order[]>("/api/orders").subscribe((o) => (this.orders = o));
  }
}
```

Nothing in the framework, the compiler, or a code review skim objects to this. The
component still renders, the feature still works, and the change looks exactly as
reasonable as every other change in the diff. What is lost is the property that made
`shared/` worth having: a component that only knows how to render data can be reused
anywhere, tested with a plain array, and changed without touching a network layer. A
`shared` component with its own HTTP call cannot be any of those things, and now the next
screen that needs "a table like that one" cannot reuse it either — it fetches the wrong
endpoint. The folder still says `shared`. It has stopped meaning that.

This does not get caught by "we agreed not to do this", because the agreement is not
written down anywhere a computer checks it, and a reviewer skimming a component template
has no reason to notice one extra constructor parameter.

## Working through it

### There is exactly one rule, and everything else is a consequence of it

Dependencies flow one way: `features` may depend on `core` and `shared`; `core` and
`shared` may not depend on `features` or on each other's internals; one feature may not
reach directly into another feature. Everything people mean by "core/shared/features
architecture" — services live in core, dumb components live in shared, screens live in
features — is a restatement of this single direction, not a separate set of rules to
remember.

### Why the direction, and not just "keep folders tidy"

`core` and `shared` are meant to be the stable, low-level layer everything else is built
from. If they are allowed to import from `features`, there is no layer left that is safe
to depend on without dragging in the rest of the application — a "shared" component that
imports a feature service is one `npm run build` away from every feature being
transitively coupled to every other one through it. The direction is what keeps `core`
and `shared` actually shared.

### Why the folder alone cannot enforce it

TypeScript's module system does not know that `src/app/shared` is supposed to mean
anything different from `src/app/features/orders` — an import is an import. Enforcing
the direction needs a tool that understands the folders as an architectural layer, not
just a path. `eslint-plugin-boundaries` does exactly this: you declare which folder
glob is which layer, then declare which layers may import which, and it becomes a lint
error, not a hoped-for convention.

### Making the violation visible in code, not just in review

The value of encoding the rule is that it turns "did the reviewer happen to notice" into
"did the pipeline pass". A lint rule catches the violation on the machine of whoever
wrote it, before it is anyone else's problem to spot.

## The solution

A complete, minimal project: three layers, a boundaries-enforcing ESLint config, and one
deliberate violation to prove the rule actually fires.

```
.
├── package.json
├── tsconfig.json
├── .eslintrc.json
└── src/app/
    ├── core/
    │   └── api.service.ts
    ├── shared/
    │   └── orders-table.component.ts
    └── features/
        ├── orders/
        │   └── orders.component.ts
        └── invoices/
            └── invoices.component.ts   # the violation
```

```json
// package.json
{
  "name": "angular-layering-demo",
  "private": true,
  "devDependencies": {
    "typescript": "5.4.5",
    "eslint": "8.57.0",
    "@typescript-eslint/parser": "7.8.0",
    "@typescript-eslint/eslint-plugin": "7.8.0",
    "eslint-plugin-boundaries": "4.2.2"
  },
  "scripts": {
    "lint": "eslint src/app --ext .ts"
  }
}
```

```json
// tsconfig.json
{
  "compilerOptions": {
    "target": "ES2022",
    "module": "ES2022",
    "moduleResolution": "bundler",
    "experimentalDecorators": true,
    "strict": true
  }
}
```

```json
// .eslintrc.json
{
  "parser": "@typescript-eslint/parser",
  "plugins": ["@typescript-eslint", "boundaries"],
  "settings": {
    "boundaries/elements": [
      { "type": "core", "pattern": "src/app/core/**" },
      { "type": "shared", "pattern": "src/app/shared/**" },
      { "type": "feature", "pattern": "src/app/features/*/**", "capture": ["feature"] }
    ]
  },
  "rules": {
    "boundaries/element-types": [
      "error",
      {
        "default": "disallow",
        "rules": [
          { "from": "feature", "allow": ["core", "shared"] },
          { "from": "shared", "allow": ["core"] },
          { "from": "core", "allow": [] }
        ]
      }
    ]
  }
}
```

```typescript
// src/app/core/api.service.ts
export class ApiService {
  get<T>(path: string): Promise<T> {
    return fetch(`/api/${path}`).then((r) => r.json());
  }
}
```

```typescript
// src/app/shared/orders-table.component.ts
export interface Order {
  id: string;
  total: number;
}

// A shared component takes data as input. It does not know where data comes from.
export class OrdersTableComponent {
  orders: Order[] = [];
}
```

```typescript
// src/app/features/orders/orders.component.ts
import { ApiService } from "../../core/api.service";
import { OrdersTableComponent, Order } from "../../shared/orders-table.component";

export class OrdersComponent {
  table = new OrdersTableComponent();

  constructor(private api: ApiService) {}

  async load() {
    this.table.orders = await this.api.get<Order[]>("orders");
  }
}
```

```typescript
// src/app/features/invoices/invoices.component.ts
// Deliberate violation: one feature reaching directly into another feature's internals.
import { OrdersComponent } from "../orders/orders.component";

export class InvoicesComponent {
  relatedOrders = new OrdersComponent();
}
```

```bash
npm install
npm run lint
```

```
/src/app/features/invoices/invoices.component.ts
  2:1  error  No rule allows the dependency from feature 'invoices' to feature 'orders'
             (checked from the boundaries/element-types rule)  boundaries/element-types

✖ 1 problem (1 error, 0 warnings)
```

Delete the violating import and the `relatedOrders` field, and `npm run lint` passes.
Try the same experiment the other way — import `InvoicesComponent` from inside
`core/api.service.ts` — and the same rule fires for the reverse direction, because
`core`'s allow-list is empty: it may depend on nothing above it.

## Conclusion

**Name the rule as one sentence, not as three folder descriptions.** "Dependencies flow
one way: features depend on core and shared, never the reverse, and features do not
depend on each other" is one rule. "Services go in core, dumb components go in shared"
is a description of what that rule produces, and remembering the description without the
rule is how the description stops matching reality.

**A convention that is not checked by a tool degrades under time pressure, not
negligence.** Nobody who added the HTTP call to a shared component thought they were
breaking the architecture; they were fixing a bug on a deadline, and nothing in their
path told them this fix crossed a line that mattered.

**Put the enforcement where the violation is introduced, not where it is discovered.** A
lint rule that runs on save or in CI catches this in the same sitting it was written in.
An architecture review three sprints later catches it after four other features have
copied the pattern.
