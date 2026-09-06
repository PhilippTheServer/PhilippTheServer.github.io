---
layout: post
title: "Prometheus HTTP Service Discovery Backed by a Live Inventory"
subtitle: "Replacing a hand-edited targets file with an endpoint that cannot drift from reality."
date: 2026-07-07 09:00:00 +0200
tags: [observability, automation, networking]
description: >-
  A static Prometheus targets file is correct on the day someone last edited
  it and wrong every day after that infrastructure changes. This walks
  through Prometheus's HTTP service discovery mechanism, backed by a live
  inventory rather than a file, with a complete example that runs on a
  laptop.
---

## The problem

Prometheus needs to know what to scrape. The simplest way to tell it is a static config:

```yaml
# prometheus.yml — the part that drifts
scrape_configs:
  - job_name: node
    static_configs:
      - targets:
          - "host-a.example.internal:9100"
          - "host-b.example.internal:9100"
```

This file is correct exactly once: the moment someone finishes editing it after the last
infrastructure change. A new host is provisioned and nobody remembers to add it here. A
host is decommissioned and the entry lingers, so Prometheus dutifully scrapes a target
that no longer exists and shows it as `down`, which trains everyone to ignore `down`
targets as noise — which is precisely when a real outage on a real target stops standing
out. The failure is quiet in both directions: missing monitoring on a new host produces no
alert, because there is no target to be unreachable. It is not a bug that announces
itself; it is an absence that only shows up when someone goes looking for a host that
should have metrics and does not.

The list of what to scrape and the list of what actually exists are two different sources
of truth the moment either changes independently, and a hand-edited file guarantees they
will.

## Working through it

### Point Prometheus at a source of truth that already changes when infrastructure changes

Somewhere in most infrastructures there is already a system that knows the current set of
hosts — an Ansible inventory, a CMDB, a cloud provider's API, a database a provisioning
tool writes to. Prometheus's `http_sd_configs` lets it poll an HTTP endpoint for the
current target list instead of reading a static file, which means the fix is not "edit the
targets file less carelessly" but "stop hand-maintaining a target list that duplicates
data that already exists somewhere live".

```yaml
scrape_configs:
  - job_name: node
    http_sd_configs:
      - url: http://sd.example.internal:8080/targets
        refresh_interval: 30s
```

### Match the exact contract, not an approximation of it

`http_sd` expects a specific JSON shape: a list of objects, each with a `targets` array of
`host:port` strings and an optional `labels` map. Prometheus is not forgiving about this —
get the shape wrong and the whole scrape config silently discovers zero targets, with a log
line easy to miss rather than a hard failure.

```json
[
  {
    "targets": ["host-a.example.internal:9100"],
    "labels": {
      "environment": "production",
      "role": "web"
    }
  }
]
```

The `labels` map is where the inventory's own metadata (environment, role, rack, whatever
your inventory already tracks) becomes Prometheus labels, for free, without a second
system to keep those labels in sync.

### Choose `refresh_interval` as a trade-off, not a default

A short interval means new hosts are discovered quickly and decommissioned ones drop out
quickly, at the cost of hitting the endpoint more often. A long interval is cheaper and
slower to react. Thirty seconds to a minute is a reasonable default for most
infrastructure — hosts do not usually appear and disappear faster than that, and the
endpoint itself is cheap enough that polling it every 30 seconds costs nothing. Set it
shorter only if your infrastructure genuinely churns that fast, and if it does, ask
whether something downstream also needs to know that quickly, because Prometheus is
probably not the first system that should be finding out.

### Design for the endpoint being briefly unreachable

This is the detail that makes `http_sd` safe to depend on rather than a new single point of
failure: when Prometheus cannot reach the discovery endpoint, it keeps the last successful
target list rather than blanking it. A restart of the discovery service, a deploy, a
transient network blip — none of it drops monitoring coverage, because Prometheus does not
throw away targets it already knows about just because the most recent poll failed. It
does mean a genuinely stale inventory (a host decommissioned an hour ago, an endpoint down
since) is not immediately visible as a change in behaviour, which is a reasonable trade —
stale-but-known beats empty-and-unmonitored — but it means the discovery endpoint's own
health needs its own monitoring, ideally scraped through a static target rather than
through itself.

### Keep the endpoint boring

The endpoint does not need to be clever. It needs to answer fast, answer correctly, and
fail loudly if its backing data is unavailable rather than silently returning an empty
list — an empty list from a broken endpoint looks identical to "there is genuinely nothing
to scrape", and Prometheus cannot tell the difference. A read-only view over the inventory,
cached briefly if the inventory read is expensive, is normally enough.

## The solution

A complete setup: a small Python service reading a YAML inventory file and serving it in
the `http_sd` JSON format, a Prometheus config using it, and a `docker-compose.yml` that
runs both on a laptop.

```python
# sd_server.py
import json
from http.server import BaseHTTPRequestHandler, HTTPServer

import yaml

INVENTORY_PATH = "/etc/sd/inventory.yaml"


def build_http_sd_response() -> list[dict]:
    with open(INVENTORY_PATH) as f:
        inventory = yaml.safe_load(f) or {}

    response = []
    for host in inventory.get("hosts", []):
        response.append({
            "targets": [f"{host['address']}:{host['port']}"],
            "labels": {
                "environment": host.get("environment", "unknown"),
                "role": host.get("role", "unknown"),
            },
        })
    return response


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/targets":
            self.send_response(404)
            self.end_headers()
            return

        try:
            body = json.dumps(build_http_sd_response()).encode()
        except Exception as exc:
            self.send_response(500)
            self.end_headers()
            self.wfile.write(str(exc).encode())
            return

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass  # keep container logs quiet for this example


if __name__ == "__main__":
    HTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
```

```yaml
# inventory.yaml
hosts:
  - address: node-exporter
    port: 9100
    environment: production
    role: web
```

```yaml
# prometheus.yml
global:
  scrape_interval: 15s

scrape_configs:
  - job_name: node
    http_sd_configs:
      - url: http://sd-server:8080/targets
        refresh_interval: 30s
```

```dockerfile
# Dockerfile.sd
FROM python:3.12-slim
RUN pip install --no-cache-dir pyyaml==6.0.2
COPY sd_server.py /sd_server.py
COPY inventory.yaml /etc/sd/inventory.yaml
CMD ["python3", "/sd_server.py"]
```

```yaml
# docker-compose.yml
services:
  sd-server:
    build:
      context: .
      dockerfile: Dockerfile.sd
    ports:
      - "8080:8080"

  node-exporter:
    image: prom/node-exporter:v1.8.2
    ports:
      - "9100:9100"

  prometheus:
    image: prom/prometheus:v2.55.1
    depends_on:
      - sd-server
      - node-exporter
    volumes:
      - ./prometheus.yml:/etc/prometheus/prometheus.yml:ro
    ports:
      - "9090:9090"
```

Run it and check discovery:

```bash
docker compose up -d --build
sleep 5
curl -s http://localhost:8080/targets
curl -s http://localhost:9090/api/v1/targets | python3 -m json.tool | grep -A3 health
```

Expected output from the discovery endpoint:

```json
[{"targets": ["node-exporter:9100"], "labels": {"environment": "production", "role": "web"}}]
```

Expected output from Prometheus's own targets API, confirming it discovered and scraped
the target through `http_sd`:

```json
    "health": "up",
```

To see the "endpoint briefly unreachable, targets not blanked" behaviour, stop the
discovery service and check Prometheus still lists the target:

```bash
docker compose stop sd-server
curl -s http://localhost:9090/api/v1/targets | python3 -m json.tool | grep -A3 health
```

The target still shows `"health": "up"` (or `"down"` only once the actual scrape starts
failing for its own reasons) — Prometheus kept the last known target list rather than
discovering zero targets.

## Conclusion

Static target files fail by omission, and omission does not page anyone. `http_sd` does
not remove the need for a source of truth about what infrastructure exists — it just
refuses to let Prometheus's view of that infrastructure be a second, hand-maintained copy
of it.

Two points generalise beyond Prometheus specifically:

**Whenever a monitoring system's configuration is a copy of state that lives somewhere
else, the copy will drift, and the fix is to serve it live rather than re-editing the copy
more carefully.** This applies to more than target lists — alert routing based on
ownership, dashboards keyed to a service catalogue, anything derived from infrastructure
that changes independently of the monitoring config.

**A discovery mechanism that fails closed (empty list on error) is worse than a hand-edited
file, because at least a stale file was deliberately wrong. Fail by keeping the last known
good state, and monitor the discovery path itself as a separate concern from what it
discovers.**
