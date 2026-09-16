---
layout: post
title: "nginx: The Map Variable That Fixes the URI in proxy_pass"
subtitle: "A location regex plus a proxy_pass with a URI is a configuration error nginx refuses at reload. The fix is a map on $uri that turns the cache policy into a variable, and a variable in proxy_pass changes how nginx handles the request path."
date: 2026-09-14 09:00:00 +0200
tags: [docker, networking, observability, ci-cd]
description: >-
  nginx: [emerg] invalid URI prefix in proxy_pass: a regex location needs a bare
  upstream, and a map on $uri keeps a path-specific Cache-Control header.
---

## The problem

A Docker build for a small internal service failed at its final step, the
`nginx -t` plus reload inside the image's entrypoint, with:

```
nginx: [emerg] invalid URI prefix in proxy_pass in
        /etc/nginx/conf.d/default.conf:23
```

Line 23 was the reverse-proxy rule for the service's API. The configuration
had not changed in a way anyone could point to — the image had built fine for
weeks — until a new `location` block was added to serve a static asset
directory, and the build started failing on every push.

The rule that failed:

```nginx
location ~ ^/api/ {
    proxy_pass http://127.0.0.1:8080/api/;
    proxy_set_header Cache-Control "no-store";
}
```

The new rule that was added above it:

```nginx
location /static/ {
    alias /usr/share/static/;
    expires 7d;
    add_header Cache-Control "public";
}
```

Nothing in the new rule touches the API rule. The error is on the API rule.
The reason is a rule of nginx that is easy to miss and that the two rules
together made impossible to ignore.

## Working through it

### The rule: a regex location cannot take a proxy_pass with a URI

nginx's `proxy_pass` has two forms, and which one is legal depends on the kind
of `location` it is in:

- In a **prefix location** (`location /api/`), `proxy_pass` may carry a URI:
  `proxy_pass http://backend/api/;`. nginx replaces the matched prefix with
  the URI in the directive, so a request to `/api/v1/users` is forwarded to
  `/api/v1/users` on the backend.
- In a **regex location** (`location ~ ^/api/`), `proxy_pass` must be a bare
  upstream with no URI: `proxy_pass http://backend;`. nginx forwards the
  original request URI unchanged.

The second form is a hard error, caught at configuration parse time, which is
why the failure is at `nginx -t` and not at request time. The `invalid URI
prefix in proxy_pass` message is nginx's way of saying exactly this: the
location is a regex, and the directive carries a URI.

The catch is that the API rule *was* written as a regex location, and it *did*
carry a URI, and it had built fine for weeks. It had built fine because the
image's nginx was an older major version, and in that version the combination
was accepted with a warning that nobody reads. The base image was bumped to a
newer nginx as part of a routine dependency update, the warning became an
error, and the build started failing on a line nobody had touched.

### The fix that was actually needed: a map on $uri

The rule needed to do two things: proxy `/api/` to the backend, and set
`Cache-Control: no-store` on the response. The second is the part that had
motivated the URI-carrying form in the first place, because the original
author wanted the cache header to apply only to proxied responses, not to the
static assets.

The fix is to move the cache policy out of the location and into a variable,
computed by a `map` on `$uri`:

```nginx
map $uri $api_cache_policy {
    ~^/api/   "no-store";
    default   "";
}

server {
    listen 80;

    location /static/ {
        alias /usr/share/static/;
        expires 7d;
        add_header Cache-Control "public";
    }

    location ~ ^/api/ {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        add_header Cache-Control $api_cache_policy always;
    }
}
```

Two things change, and each matters:

1. `proxy_pass` is now a bare upstream, which is the form a regex location
   requires. The original URI is forwarded unchanged, which is what the
   backend expects.
2. The cache policy is a variable, `$api_cache_policy`, computed by the map.
   For any URI under `/api/` it is `no-store`; for everything else it is the
   empty string, and `add_header` with an empty value adds no header.

The map is the part that is worth understanding, because it is the general
tool for "this header depends on the request path" and it is not specific to
caching. A `map` takes one or more source variables, applies a set of
matchers (literal, regex, or `default`), and produces a target variable. The
target is then usable anywhere a variable is usable, including in
`add_header`, `proxy_set_header`, and `return`.

### Why the variable in proxy_pass changes the path handling

There is a second, subtler consequence of the fix that is worth naming because
it is the part that bites people later. When `proxy_pass` contains a variable
— which it does not in the configuration above, but which it does in the
common follow-up where the upstream is itself a variable, e.g.
`proxy_pass http://$backend;` — nginx stops doing the prefix-replacement
behaviour and forwards the request URI exactly as it arrived.

In the configuration above, `proxy_pass` is a literal, so the rule is: regex
location, bare upstream, original URI forwarded. If someone later changes the
upstream to a variable to point at a different backend per request, the
forwarding behaviour is already "original URI", so nothing changes in the path
— but it is worth knowing the rule, because the two forms of `proxy_pass`
(literal with URI, variable or bare without URI) have different path semantics,
and mixing them up is a class of bug that produces 404s on the backend that
are invisible in the nginx access log, because the request was forwarded, just
to the wrong path.

### Keeping the reload honest with a build-time check

The failure mode that cost the build is a configuration error that nginx
catches at parse time, which means it is also caught at build time, for free,
by the `nginx -t` step that was already in the entrypoint. The build failed
for the right reason, at the right time, with the right message. The only
thing missing was a check that the configuration stays in a state nginx
accepts, so that a future edit that reintroduces the regex-plus-URI
combination fails the build rather than the next reload in production.

The check is the same `nginx -t` the entrypoint runs, run in the build:

```dockerfile
# Verify the configuration parses before it ships.
RUN nginx -t -c /etc/nginx/nginx.conf
```

This is not a new test in the pytest sense; it is the same parse check nginx
itself performs, run at a point where failing is cheap. The build already did
this in the entrypoint at container start; doing it again at image build time
moves the failure from "the container will not start" to "the image was not
built", which is where it belongs.

## The solution

The state of the fix:

1. The API `location` is a regex, and its `proxy_pass` is a bare upstream,
   which is the combination nginx requires.
2. The cache policy is a `map` on `$uri`, so the header is set only for
   proxied API responses and not for static assets, without coupling the two
   in a single location block.
3. The build runs `nginx -t` on the shipped configuration, so a configuration
   nginx rejects fails the build, not the next reload.

The general shape of the problem is a configuration rule that is legal in one
version of a tool and an error in the next, surfaced not by a change to the
configuration but by a change to the tool. The answer is the same in most
cases: make the configuration not depend on the lenient behaviour, and run
the tool's own parse check at build time so the next version bump fails the
build rather than the next deploy.

## Conclusion

`invalid URI prefix in proxy_pass` is nginx's way of saying that a regex
location and a URI-carrying `proxy_pass` are not a legal combination. The
configuration had that combination, and it had built for weeks, because the
older nginx accepted it with a warning. The base image bump turned the
warning into an error, and the build failed on a line nobody had touched.

The fix is two parts: a bare `proxy_pass` in the regex location, and a `map`
on `$uri` that carries the cache policy into a variable, so the header applies
where it should and nowhere else. The map is the general tool for headers that
depend on the request path, and it is not specific to caching.

The build already ran `nginx -t` at container start. Running it at image build
time moves the failure to where it belongs: the image is not built, not the
container that does not start.
