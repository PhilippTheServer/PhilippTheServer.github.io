---
layout: post
title: "A Dead-Man's Switch for the Monitoring System Itself"
subtitle: "Detecting the one outage that never generates an alert through the normal path."
date: 2026-07-14 09:00:00 +0200
tags: [alerting, observability, reliability]
description: >-
  A monitoring pipeline that is completely down looks identical to one where
  everything is fine, because both states produce zero alerts. This explains
  the dead-man's-switch pattern that closes that gap, and gives a complete,
  self-hosted example that a reader can run and deliberately trip on a
  laptop.
---

## The problem

Alertmanager is silent. That is either the best possible state — nothing is wrong — or the
worst one — Alertmanager itself is down, or Prometheus stopped evaluating rules, or the
network path between the monitoring stack and wherever alerts get delivered is broken. From
the outside, "no alerts firing" and "the alerting pipeline is entirely dead" look exactly
the same: nothing arrives, nobody is paged, and the silence itself is the only symptom, and
silence is not something anyone notices until they go looking for it.

This is not a hypothetical edge case, it is a structural property of the design: an
alerting system alerts by sending something when a condition is true. If the sender is what
broke, there is nothing left to send the "I am broken" message, through the same path that
is broken. A rule that says "alert when Prometheus is down" evaluated *by that same
Prometheus* cannot fire once Prometheus stops evaluating anything. A route through
Alertmanager to notify "Alertmanager is unreachable" cannot notify anyone once Alertmanager
is the thing that is unreachable.

## Working through it

### Invert the check: alert on the absence of a signal, from outside the system

The dead-man's-switch pattern solves this by moving the failure detection outside the
system being watched. Instead of Prometheus alerting when something is wrong, it fires an
alert that is *always* true — a trivial expression like `vector(1) == 1` that can never
evaluate to false — and routes it, continuously, to a completely separate service. That
external service's only job is to notice when the expected, continuous stream of "I am
fine" pings stops arriving, and alert a human when it does. The monitoring system is no
longer trying to report on its own death through its own voice; an independent watcher is
listening for silence instead.

### Choose the grace period as a function of your evaluation and repeat interval, not a guess

Alertmanager repeats firing notifications for an active alert at `repeat_interval`. The
heartbeat receiver needs to expect a ping no less often than that, plus enough slack for
one missed cycle to not be a false alarm — network jitter, a slow scrape, a brief GC pause
in Alertmanager should not trip the switch. A grace period of roughly two to three times the
expected ping interval is a reasonable starting point: tight enough that an actual outage is
caught quickly, loose enough that ordinary variance does not.

### Decide what "the switch itself is unreachable" means, deliberately

The heartbeat receiver is now a second thing that can fail, and if it fails silently you
have moved the blind spot rather than removed it. It needs its own liveness signal that
something else checks — even something as blunt as a separate, infrequent uptime check
against the receiver's own HTTP endpoint from a completely different vantage point. There is
no way to fully eliminate "what watches the watcher" with a finite number of layers; the
goal is to make each additional layer simpler and less likely to fail for the same reason as
the layer below it, not to chase an impossible zero.

### Do not rely on a third party you have not verified you can reach

Services built for exactly this purpose exist and are a reasonable choice in production. For
an article meant to be run and understood without an external account, the example below is
self-hosted: a small receiver that expects a ping on an interval and raises its own alert
(here, a log line and a webhook call you can point anywhere) when one is missed. The
principle transfers directly to a hosted heartbeat service if you use one instead.

## The solution

Four pieces: a Prometheus rules file with the always-firing alert, an Alertmanager config
routing it to a webhook receiver, the receiver itself, and a `docker-compose.yml` to run it
all on a laptop.

```yaml
# alert_rules.yml
groups:
  - name: dead-mans-switch
    rules:
      - alert: Heartbeat
        expr: vector(1) == 1
        labels:
          severity: none
        annotations:
          summary: "Always-firing heartbeat for the dead-man's switch"
```

```yaml
# prometheus.yml
global:
  scrape_interval: 15s
  evaluation_interval: 15s

rule_files:
  - alert_rules.yml

alerting:
  alertmanagers:
    - static_configs:
        - targets: ["alertmanager:9093"]
```

```yaml
# alertmanager.yml
route:
  receiver: heartbeat
  routes:
    - match:
        alertname: Heartbeat
      receiver: heartbeat
      repeat_interval: 30s
      group_wait: 0s
      group_interval: 10s

receivers:
  - name: heartbeat
    webhook_configs:
      - url: http://heartbeat-receiver:8000/heartbeat
        send_resolved: false
```

```python
# heartbeat_receiver.py
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

GRACE_PERIOD_SECONDS = 90  # roughly 3x the 30s repeat_interval above
last_ping = time.time()
lock = threading.Lock()


def watchdog():
    while True:
        time.sleep(5)
        with lock:
            elapsed = time.time() - last_ping
        if elapsed > GRACE_PERIOD_SECONDS:
            print(
                f"ALERT: no heartbeat received in {elapsed:.0f}s "
                f"(grace period is {GRACE_PERIOD_SECONDS}s) — "
                "the monitoring pipeline may be down",
                flush=True,
            )


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        global last_ping
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)  # drain the Alertmanager webhook payload
        with lock:
            last_ping = time.time()
        print(f"heartbeat received at {time.strftime('%X')}", flush=True)
        self.send_response(200)
        self.end_headers()

    def log_message(self, format, *args):
        pass


if __name__ == "__main__":
    threading.Thread(target=watchdog, daemon=True).start()
    HTTPServer(("0.0.0.0", 8000), Handler).serve_forever()
```

```dockerfile
# Dockerfile.heartbeat
FROM python:3.12-slim
COPY heartbeat_receiver.py /heartbeat_receiver.py
CMD ["python3", "/heartbeat_receiver.py"]
```

```yaml
# docker-compose.yml
services:
  prometheus:
    image: prom/prometheus:v2.55.1
    volumes:
      - ./prometheus.yml:/etc/prometheus/prometheus.yml:ro
      - ./alert_rules.yml:/etc/prometheus/alert_rules.yml:ro
    ports:
      - "9090:9090"

  alertmanager:
    image: prom/alertmanager:v0.27.0
    volumes:
      - ./alertmanager.yml:/etc/alertmanager/alertmanager.yml:ro
    ports:
      - "9093:9093"

  heartbeat-receiver:
    build:
      context: .
      dockerfile: Dockerfile.heartbeat
    ports:
      - "8000:8000"
```

Run it and watch the receiver's log:

```bash
docker compose up -d
docker compose logs -f heartbeat-receiver
```

Expected output, a line roughly every 30 seconds as long as everything is healthy:

```
heartbeat receiver_1  | heartbeat received at 14:02:03
heartbeat receiver_1  | heartbeat received at 14:02:33
heartbeat receiver_1  | heartbeat received at 14:03:03
```

Now trip the switch deliberately by taking Alertmanager down, simulating exactly the
failure this pattern exists to catch:

```bash
docker compose stop alertmanager
```

Wait past the grace period and the receiver's own log shows the detection:

```
ALERT: no heartbeat received in 95s (grace period is 90s) — the monitoring pipeline may be down
```

Note what this proves: the receiver noticed Alertmanager's outage without Alertmanager
having to tell it anything, which is the entire point — the detector does not depend on the
thing it is detecting the failure of.

## Conclusion

A monitoring pipeline cannot be trusted to report its own total failure through the channel
that failure took down. The only way to close that gap is a second, independent path whose
signal is presence rather than content — not "here is what's wrong" but simply "I am still
here", checked by something that lives outside the boundary of what it is watching.

Two points generalise beyond this specific setup:

**Silence is not evidence of health in any system that only speaks when something is
wrong.** If "nothing happened" and "the reporter is dead" produce the same observable state,
add a heartbeat, because no amount of care with the primary alerting logic closes that gap.

**Put the watcher genuinely outside the boundary it watches.** A heartbeat receiver running
in the same cluster, on the same network, behind the same power circuit as the thing it
watches shares failure modes with it. The value of this pattern is proportional to how
independent the watcher actually is.
