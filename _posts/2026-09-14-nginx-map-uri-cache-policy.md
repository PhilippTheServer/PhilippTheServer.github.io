---
layout: post
title: "An nginx Config That Refused to Start, and Where Per-Path Cache Policy Actually Belongs"
subtitle: "A regex location cannot carry a proxy_pass with a URI part — and splitting cache policy by routing is the wrong shape anyway."
date: 2026-09-14 09:00:00 +0200
tags: [docker, networking, observability, ci-cd]
description: >-
  A reverse proxy in front of a package feed crash-looped on every deploy
  because a regex location block carried a proxy_pass with a URI part, which
  nginx refuses to start with. The block existed only to mark two files as
  no-cache, and the fix — a map on $uri deciding the Cache-Control header —
  is also the shape the policy should have had from the start.
---

## The problem

A small nginx deployment, a single pod fronting an object-storage bucket that
served a Debian package feed, crash-looped on its first rollout. Not degraded,
not slow — nginx refused to start:

```
[emerg] "proxy_pass" cannot have URI part in location given by regular
expression, or containing variables
nginx: configuration file /etc/nginx/conf.d/default.conf test failed
```

The offending block was not the main one. It was a second, small location,
added for a legitimate reason: the feed's index files must never be served
stale, because they are the only thing a package client reads to decide what
exists. The author's instinct was to give those files their own location with
its own `Cache-Control` header — and that instinct is where the problem starts,
for two independent reasons.

## Working through it

### Why a regex location cannot rewrite the request

nginx locations come in a small number of shapes, and they do not all have the
same capabilities. A prefix location (`location /packages/`) may carry a
`proxy_pass` with a URI part, because nginx can compute the rewrite: strip the
matched prefix, append the rest of the request URI to the upstream URI. A
regex location (`location ~ \.deb$`) cannot, because a regular expression does
not define a stable prefix to strip — the match can begin anywhere in the URI,
so there is no well-defined remainder to pass upstream. nginx encodes this as
a startup error rather than a runtime guess:

```
location ~ /Packages(\.gz)?$ {
    proxy_pass http://rgw/apt-feed/;   # URI part in a regex location: emerg
}
```

The error is correct and it is absolute — there is no flag that makes it go
away. A regex location's `proxy_pass` may name an upstream only, with no path,
in which case the request URI is passed through unchanged.

### Why splitting cache policy by routing was the wrong shape

Even setting the startup error aside, the design had the decision in the wrong
place. Cache policy is a property of the *response*, and it varies by *path* —
but it is not a routing decision. The request goes to the same upstream either
way; the only thing that differs is the header the proxy puts on the way back.
Modelling that difference as a second location means duplicating the upstream
configuration in two blocks, and it runs into the regex limitation the moment
the path you want to treat specially is not a clean prefix.

The construct nginx provides for "decide a value per request path" is the
`map` block: a table from a variable (here, `$uri`) to a value (here, the
`Cache-Control` string), matched by exact string or by regex, evaluated per
request, and usable anywhere a string is usable — including in `add_header`.
One location, one upstream, and the policy lives in the one place a per-path
decision belongs.

### What the feed actually needs cached

The policy itself is worth stating, because it is not "cache everything" or
"cache nothing":

- `Packages`, `Packages.gz`, `Release` and `Release.gpg` are the index. A
  package client reads them to build its view of what exists. A stale copy
  does not return an error — it returns a *plausible* list that is missing
  freshly published packages, which is strictly worse than a failure, because
  the client believes it has seen everything. These must be `no-cache`.
- The `.deb` files carry their full version in the filename. A given filename
  is immutable for the life of the feed: once `foo_1.2.3_arm64.deb` exists, it
  never changes. That is the textbook case for aggressive caching, and it is
  worth having when a fleet of machines pulls the same set of packages.

### The upstream's own headers lose to the proxy's

One more detail that bites in exactly this setup: the object storage behind
the proxy sets its own `Cache-Control` header on every object. If the proxy
adds its own without dealing with that one, the client receives two
`Cache-Control` headers, and the behaviour of two conflicting cache directives
is not the behaviour you wrote. The proxy has to hide the upstream's header
before adding its own:

```
proxy_hide_header Cache-Control;
add_header Cache-Control $apt_cache_control always;
```

The `always` matters: without it, `add_header` does not apply to error
responses, and a 404 for a missing package would carry no cache policy at all.

## The solution

The complete configuration, as it should ship:

```nginx
# /etc/nginx/conf.d/default.conf
upstream rgw {
    server 192.0.2.10:80 max_fails=3 fail_timeout=10s;
}

# Cache policy by path. A regex location cannot carry a proxy_pass with a URI
# part — nginx refuses to start, which is exactly how the crash-loop happened —
# so the split happens in a variable, not in the routing.
map $uri $apt_cache_control {
    default                        "public, max-age=3600";
    "~/Packages(\.gz|\.bz2|\.xz)?$" "no-cache";
    "~/Release(\.gpg)?$"            "no-cache";
}

server {
    listen 80;
    server_name _;

    location / {
        proxy_pass http://rgw/apt-feed/;
        proxy_set_header Host $proxy_host;
        proxy_http_version 1.1;
        proxy_set_header Connection "";

        # The bucket sets its own caching headers; ours is the one that should win.
        proxy_hide_header Cache-Control;
        add_header Cache-Control $apt_cache_control always;

        # A dead backend should cost one retry, not the whole request.
        proxy_next_upstream error timeout http_502 http_503 http_504;
        proxy_connect_timeout 5s;
        proxy_read_timeout 300s;
    }

    location = /healthz {
        access_log off;
        return 200 "ok\n";
    }
}
```

One location. One upstream. The per-path decision is a table with three rows,
and adding a fourth path later is a row, not a second routing block.

### Why this should not have reached the cluster

The config lived in a Kubernetes ConfigMap, and the pipeline validated the
manifest — schemas, names, labels — with the usual tooling. That validation
knows nothing about the *contents* of a ConfigMap: to it, the nginx
configuration is an opaque string, and a string that makes its consumer refuse
to start is still a valid string. The check that would have caught this is the
consumer's own config test, run against the exact configuration that ships, in
the exact image that runs it:

```bash
# Extract the config from the ConfigMap, test it with the real binary
kubectl get configmap apt-feed -o jsonpath='{.data.default\.conf}' \
  | docker run -i --name conf nginx:1.27-alpine \
    sh -c 'cat > /etc/nginx/conf.d/default.conf && nginx -t'
```

Run against the broken configuration, that command fails with the same
`[emerg]` line the pod printed, on the laptop, before the rollout. Run against
the fixed one, it passes. The property it pins is not "this config is good"
but "the configuration that ships is one nginx will start", which is the
property the crash-loop proved was missing.

## Conclusion

A regex location cannot carry a `proxy_pass` with a URI part, and nginx makes
that a startup error rather than a runtime surprise — so a config that uses
that shape crash-loops on every deploy instead of degrading. The block that
triggered it existed to split cache policy by path, which is a response
property, not a routing decision, and the construct that matches that shape is
a `map` on `$uri` feeding one `add_header` in one location.

Two rules transfer from this. First: when the same upstream serves paths that
need different headers, the difference belongs in a variable, not in a second
location. Second: a configuration that is the *content* of a manifest — nginx
config in a ConfigMap, a systemd unit in a file, a cron spec in a string — is
not validated by anything that validates the manifest, and the only check that
catches a consumer's refusal to start is the consumer's own config test, run
against the exact content that ships.
