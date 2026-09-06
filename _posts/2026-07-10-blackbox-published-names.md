---
layout: post
title: "Probing the Names You Publish, Not the Services You Run"
subtitle: "Why an internal healthz endpoint cannot see the failures that actually take a service down."
date: 2026-07-10 09:00:00 +0200
tags: [observability, dns]
description: >-
  A service that checks only its own process health can report perfectly
  healthy while nobody outside it can reach it at all. This covers why
  external, black-box probing against the published name catches an entire
  class of failure that internal health checks structurally cannot, with a
  complete blackbox_exporter setup that runs on a laptop.
---

## The problem

A typical health check looks like this:

```
GET /healthz -> 200 OK
```

The process answers, the database connection pool is warm, background workers are alive.
Every internal signal says the service is fine. And yet a user hits `app.example.com` and
gets nothing: the DNS record for `app.example.com` points at an IP address that changed
last week and nothing updated the record; or the reverse proxy in front of the service has
a routing rule that broke in a config change and now returns 502 for that hostname
specifically; or the TLS certificate expired six hours ago and every client refuses the
connection before a request is ever sent; or a firewall rule introduced upstream now blocks
the port from outside while everything inside the boundary talks to everything else just
fine.

None of these show up in `/healthz`, because `/healthz` runs inside the exact boundary that
broke. DNS resolution, routing, TLS, and network reachability from outside are all things
that happen *before* a request reaches the process being checked, so a check that only the
process can answer is structurally blind to all of them. This is not a check that is
implemented badly — it is a check answering a question ("is the process alive") that is
not the question that actually matters to a user ("can I reach it").

## Working through it

### Probe from where the failure actually happens

The fix is to test the same thing an external client tests: resolve the published name,
open the connection, negotiate TLS, send the request, read the response — from outside the
service's own process, ideally from outside its own network boundary entirely. Prometheus's
`blackbox_exporter` does exactly this: it is handed a target and a module (HTTP, TCP, DNS,
ICMP), it performs the real operation against that target, and it exposes the result as
metrics, chiefly `probe_success` (1 or 0) plus module-specific detail.

### Treat the published name as the thing under test

The target for a blackbox probe is not the service's internal address — it is the name a
real client would type or resolve: the public hostname, the URL a user bookmarks. Probing
an internal service address instead defeats the entire point, because it recreates exactly
the internal-boundary blindness this technique exists to escape. If DNS is broken for the
published name, the probe has to actually do DNS resolution against that name to notice.

### Use the relabelling pattern, because the target and the exporter's address are different things

`blackbox_exporter` itself listens on one address; the thing it is told to probe is a
completely different address. Prometheus's scrape config for blackbox always looks slightly
unusual for this reason — it rewrites `__address__` after scraping, so the actual URL being
probed travels as a label rather than as the scrape target:

```yaml
- source_labels: [__address__]
  target_label: __param_target
- source_labels: [__param_target]
  target_label: instance
- target_label: __address__
  replacement: blackbox-exporter:9115
```

This is the one part of a blackbox setup that looks like magic until you have written it
once. `static_configs` lists the URLs to probe. Relabelling copies each one into
`__param_target` (which becomes the `?target=` query parameter blackbox_exporter reads),
then overwrites `__address__` with blackbox_exporter's own address, so Prometheus actually
connects to the exporter and asks it to probe the target, rather than trying to scrape the
target directly as if it were a Prometheus endpoint.

### Read the specific metrics, not just `probe_success`

`probe_success` answers "did the whole probe succeed", which is useful for alerting but
useless for diagnosis. The module-specific metrics say why: `probe_http_status_code` tells
you whether it was a 4xx from routing or a 5xx from the origin; `probe_ssl_earliest_cert_expiry`
gives you a number of seconds until a certificate expires, which lets you alert *before*
expiry rather than after; `probe_dns_lookup_time_seconds` being present but slow tells you
DNS resolved but is unhealthy, versus its complete absence telling you resolution failed
outright. Alert on `probe_success == 0` for immediate pages; alert on the certificate expiry
metric days in advance, because that failure is entirely predictable and there is no reason
to let it become an outage.

## The solution

A complete, runnable setup with an `http_2xx` module and a `dns` module, wired into
Prometheus with the relabelling pattern above, and a way to see both a passing probe and a
deliberately broken one.

```yaml
# blackbox.yml
modules:
  http_2xx:
    prober: http
    timeout: 5s
    http:
      valid_http_versions: ["HTTP/1.1", "HTTP/2.0"]
      valid_status_codes: [200, 201, 202]
      method: GET
      preferred_ip_protocol: ip4
      tls_config:
        insecure_skip_verify: false

  dns_lookup:
    prober: dns
    timeout: 5s
    dns:
      query_name: "example.com"
      query_type: "A"
      valid_rcodes:
        - NOERROR
```

```yaml
# prometheus.yml
global:
  scrape_interval: 15s

scrape_configs:
  - job_name: blackbox-http
    metrics_path: /probe
    params:
      module: [http_2xx]
    static_configs:
      - targets:
          - http://target-app:80/
          - http://target-app:80/this-path-does-not-exist
    relabel_configs:
      - source_labels: [__address__]
        target_label: __param_target
      - source_labels: [__param_target]
        target_label: instance
      - target_label: __address__
        replacement: blackbox-exporter:9115

  - job_name: blackbox-dns
    metrics_path: /probe
    params:
      module: [dns_lookup]
    static_configs:
      - targets:
          - 8.8.8.8:53
    relabel_configs:
      - source_labels: [__address__]
        target_label: __param_target
      - source_labels: [__param_target]
        target_label: instance
      - target_label: __address__
        replacement: blackbox-exporter:9115
```

```yaml
# docker-compose.yml
services:
  target-app:
    image: nginx:1.27-alpine
    # Serves 200 on / with nginx's default page; anything else 404s,
    # which is the deliberate failure case for the http_2xx module.

  blackbox-exporter:
    image: prom/blackbox-exporter:v0.25.0
    volumes:
      - ./blackbox.yml:/etc/blackbox_exporter/config.yml:ro
    ports:
      - "9115:9115"

  prometheus:
    image: prom/prometheus:v2.55.1
    depends_on:
      - blackbox-exporter
      - target-app
    volumes:
      - ./prometheus.yml:/etc/prometheus/prometheus.yml:ro
    ports:
      - "9090:9090"
```

Run it:

```bash
docker compose up -d
sleep 5
curl -s "http://localhost:9115/probe?target=http://target-app:80/&module=http_2xx" \
  | grep probe_success
curl -s "http://localhost:9115/probe?target=http://target-app:80/this-path-does-not-exist&module=http_2xx" \
  | grep probe_success
```

Expected output — the real path succeeds, the nonexistent path fails:

```
probe_success 1
probe_success 0
```

Through Prometheus itself, the same distinction shows up as a query:

```bash
curl -s 'http://localhost:9090/api/v1/query?query=probe_success' | python3 -m json.tool
```

which returns one time series per probed target, each with its own `instance` label carrying
the actual URL, and a `value` of `1` or `0` — the deliberately broken path reporting `0`
while the correct one reports `1`, all without either target's own process ever being asked
whether it feels healthy.

## Conclusion

An internal health check and an external probe answer different questions, and a system
needs both, but only one of them tells you what a user actually experiences.

Two points generalise beyond blackbox_exporter specifically:

**Anything that happens between a client and your process — DNS, TLS, a proxy, a
firewall — is invisible to a check that runs inside the process.** If the failure can
happen outside the boundary your health check runs in, the check cannot see it by
construction, no matter how thorough it is about everything inside that boundary.

**Probe the name a real client uses, not the address that is convenient to reach from
inside your own network.** A probe against an internal address is still useful as a
liveness check, but it is not the same measurement, and conflating the two is how
"everything internal looks fine" and "the service is down for everyone" end up
indistinguishable on the same dashboard.
