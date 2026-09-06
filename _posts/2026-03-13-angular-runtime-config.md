---
layout: post
title: "Runtime Configuration for an Angular Container Without Rebuilding"
subtitle: "One built image, several backends, decided by an environment variable at start"
date: 2026-03-13 09:00:00 +0200
tags: [frontend, docker, infrastructure-as-code, networking]
description: >-
  A frontend that reads its backend URL from an environment variable at build time needs
  a full rebuild for every deployment target, which defeats the point of building an
  image once. This article generates the frontend's runtime configuration from a
  container-start entrypoint instead, with a complete Dockerfile and compose setup that
  proves one image serves two different backends without being rebuilt.
---

## The problem

Angular's build-time `environment.ts` files feel like configuration, and they are — but
they are baked into the JavaScript bundle at `ng build` time, which means "which backend
does this frontend talk to" becomes a property of the build, not the deployment:

```typescript
// environment.prod.ts — Broken as a deployment mechanism, not as TypeScript
export const environment = {
  backendUrl: "https://api.example.com",
};
```

Every environment that needs a different backend URL — staging, a second customer
deployment, a local demo — needs its own build, because the URL is a string literal by
the time webpack is done with it. This is the same problem a compiled binary has with
hardcoded configuration, except it arrives disguised as "just an environment file",
which makes it easy not to notice until the fourth deployment target shows up and someone
asks why there are four separate CI build jobs producing four otherwise-identical
images. The image you tested in staging is, by construction, not the image you deploy to
production — they were built from different source, even if only one file differed.

The fix is not a bigger `environment.ts`. It is moving the backend URL out of the build
entirely, into something read at container start, so the same image — the one artefact
that was actually tested — is what every environment runs.

## Working through it

### Why build time is the wrong time for this decision

A build produces one artefact meant to be promoted unchanged from staging to production.
Anything that has to differ between those environments cannot live in the artefact —
it has to be supplied at the point the artefact is run. This is the same principle
behind twelve-factor configuration for a backend service; Angular's build step just
makes it easy to forget that a frontend bundle is also, from a deployment point of view,
just another artefact.

### Fetching configuration instead of compiling it in

The mechanism is: ship a small `config.json` alongside the built assets, have the app
fetch it before rendering anything that needs it, and generate that file's contents at
*container start* rather than at build time. Angular's `APP_INITIALIZER` injection token
is built for exactly this — it lets you register a function that must resolve before the
application bootstraps, so the fetched configuration is guaranteed available to every
component and service that needs it:

```typescript
// config.service.ts — the pattern to lift into a real Angular app
import { Injectable } from "@angular/core";

export interface AppConfig {
  backendUrl: string;
  environment: string;
}

@Injectable({ providedIn: "root" })
export class ConfigService {
  private config?: AppConfig;

  async load(): Promise<void> {
    const res = await fetch("/assets/config.json");
    this.config = (await res.json()) as AppConfig;
  }

  get(): AppConfig {
    if (!this.config) {
      throw new Error("ConfigService.load() was not awaited before use");
    }
    return this.config;
  }
}
```

```typescript
// app.config.ts — registering it as an APP_INITIALIZER
import { APP_INITIALIZER, ApplicationConfig } from "@angular/core";
import { ConfigService } from "./config.service";

export const appConfig: ApplicationConfig = {
  providers: [
    {
      provide: APP_INITIALIZER,
      useFactory: (config: ConfigService) => () => config.load(),
      deps: [ConfigService],
      multi: true,
    },
  ],
};
```

### Generating config.json at container start, not at image build

The container needs to turn environment variables it receives at `docker run` time into
that `config.json`. `envsubst`, from the `gettext` package, does exactly this: it
replaces `${VAR}` placeholders in a template with the current environment's values. The
official `nginx` image already runs every executable script under
`/docker-entrypoint.d/` before starting nginx, which is a convenient, well-defined hook
for this rather than replacing the image's own entrypoint.

### Restricting envsubst to the variables you actually mean

Called with no arguments, `envsubst` replaces every `${VAR}`-shaped token it finds,
including any that happen to appear literally in your JSON (unlikely here, but a real
risk in a template with more content). Passing the explicit list of variable names to
substitute is one argument and removes that risk entirely.

## The solution

A complete image and compose setup: one build, two containers, two different backend
URLs, generated at start rather than baked in.

```html
<!-- dist/index.html — stands in for real `ng build` output -->
<!doctype html>
<html>
<head><title>runtime config demo</title></head>
<body>
  <div id="app">loading configuration…</div>
  <script src="/app.js"></script>
</body>
</html>
```

```javascript
// dist/app.js — what the compiled ConfigService + APP_INITIALIZER reduce to at runtime
fetch("/assets/config.json")
  .then((r) => r.json())
  .then((cfg) => {
    document.getElementById("app").textContent =
      `backend: ${cfg.backendUrl} (${cfg.environment})`;
  });
```

```json
// dist/assets/config.template.json
{
  "backendUrl": "${BACKEND_URL}",
  "environment": "${ENVIRONMENT}"
}
```

```bash
#!/bin/sh
# generate-config.sh — installed as an nginx docker-entrypoint.d hook
set -eu
envsubst '${BACKEND_URL} ${ENVIRONMENT}' \
  < /usr/share/nginx/html/assets/config.template.json \
  > /usr/share/nginx/html/assets/config.json
```

```dockerfile
# Dockerfile
FROM nginx:1.27-alpine

RUN apk add --no-cache gettext

COPY dist/ /usr/share/nginx/html/
COPY generate-config.sh /docker-entrypoint.d/40-generate-config.sh
RUN chmod +x /docker-entrypoint.d/40-generate-config.sh
```

```yaml
# docker-compose.yml — one image, two runtime configurations
services:
  frontend-staging:
    build: .
    environment:
      BACKEND_URL: https://staging-api.example.com
      ENVIRONMENT: staging
    ports:
      - "8081:80"

  frontend-prod:
    build: .
    environment:
      BACKEND_URL: https://api.example.com
      ENVIRONMENT: production
    ports:
      - "8082:80"
```

```bash
docker compose up --build -d

curl -s localhost:8081/assets/config.json
curl -s localhost:8082/assets/config.json

docker compose images frontend-staging frontend-prod
```

```
{"backendUrl":"https://staging-api.example.com","environment":"staging"}
{"backendUrl":"https://api.example.com","environment":"production"}
```

The `docker compose images` output confirms both containers are running from the same
image ID — one build, `COPY`-ed and tagged once, running twice with two different
`config.json` files generated after the container started, not before it was built.

```bash
docker compose down
```

## Conclusion

**Anything that legitimately differs between environments does not belong in the build.**
A build-time environment file is fine for values that are genuinely constant across every
deployment; a backend URL that changes per environment is not one of those values, no
matter how much it looks like ordinary configuration in the source tree.

**A hook that runs at container start is a better place for this than a custom
entrypoint.** Using the base image's own `/docker-entrypoint.d/` convention keeps the
image's actual startup behaviour — logging, signal handling, the parts nginx's official
image already gets right — untouched, and adds exactly one well-scoped step.

**Prove the claim with two containers from one image, not one container tested twice.**
"The same image works everywhere" is only demonstrated by running it more than once,
side by side, with different environment variables, and checking that the two outputs
actually differ. Anything less is trusting the mechanism instead of testing it.
