---
layout: post
title: "Turning Alertmanager Webhooks Into Retained MQTT State"
subtitle: "Bridging fire-and-forget alert events into current state a late subscriber can still read."
date: 2026-07-17 09:00:00 +0200
tags: [alerting, redis, observability, api-design]
description: >-
  Alertmanager's webhook delivers an event to whoever happens to be listening
  at the moment it fires, which is no help to a system that connects later
  and wants to know what is currently active. This builds a small bridge that
  keeps current alert state in Redis and republishes it to MQTT as retained
  messages, with a complete setup that runs on a laptop.
---

## The problem

Alertmanager's webhook receiver is a plain HTTP POST, delivered once, to whatever is
listening at that exact moment:

```json
{
  "status": "firing",
  "alerts": [
    { "labels": { "alertname": "DiskSpaceLow", "instance": "host-a" }, "status": "firing" }
  ]
}
```

That is fine for a system that is always up and always listening — a paging service, an
incident tool. It is not fine for a system that connects intermittently and wants to know
current state on connect, which describes a lot of downstream consumers: a dashboard opened
after the alert already started firing, a physical indicator light that reboots and needs to
know whether to be red or green, a small IoT device that only wakes up periodically. None of
these witnessed the original webhook, because they were not listening when it fired, and a
plain webhook has no concept of "and here is what you missed."

MQTT's retained-message flag looks like the obvious answer — a broker holds the last message
published to a topic and delivers it immediately to any new subscriber — but a webhook-to-
MQTT bridge that just republishes each webhook payload as a retained message has its own
gap: when an alert resolves, nothing tells the broker to clear the retained message, so a
resolved alert's retained state sits on the topic forever, and every new subscriber connects
believing an alert is still firing that stopped firing hours ago.

## Working through it

### Separate "what happened" from "what is true right now"

A webhook event is a fact about a point in time: this alert transitioned to firing at this
moment. Retained MQTT state needs to answer a different question: is this alert currently
firing, right now, regardless of when it started. Those are not the same data, and
conflating them is why a naive bridge leaks stale retained messages — it treats every
webhook delivery as something to republish rather than as an instruction to update a
current-state record.

### Keep current state somewhere durable and query-able, independent of the broker

Redis, keyed by each alert's fingerprint (Alertmanager includes a stable `fingerprint` per
alert in the webhook payload), is the source of truth for "what is currently firing". On a
`firing` event, `SET` the alert's data with the fingerprint as key. On a `resolved` event,
`DEL` it. This state survives an MQTT broker restart, and it means the bridge can answer
"what is firing right now" without depending on MQTT's retained messages ever having been
set correctly in the first place — the broker's retained state becomes a reflection of
Redis, not the record itself.

### Clear retained state explicitly on resolve, don't just stop publishing

A retained message is deleted from a topic in MQTT by explicitly publishing an empty payload
with the retain flag set to that same topic — there is no "let it expire" or "stop sending
and it goes away" behaviour, retained messages persist until something overwrites or clears
them. So the resolve path in the bridge has two required side effects, and skipping either
one reintroduces the exact staleness this design is meant to prevent: delete the Redis key,
and publish an empty retained message to the corresponding MQTT topic.

### Give every alert a stable, predictable topic

Deriving the topic from the alert's labels rather than from something the bridge invents
lets a subscriber know in advance which topic to watch, without first asking the bridge what
topics exist:

```
alerts/{alertname}/{instance}
```

Fingerprint-keyed Redis storage and label-derived MQTT topics are doing different jobs on
purpose: the fingerprint is what makes an update-or-delete in Redis unambiguous even if two
alerts happen to share a name and instance under unusual label combinations; the label-based
topic is what makes the MQTT side predictable and human-readable for a subscriber who has
never seen the bridge's internals.

## The solution

A complete bridge service, an Alertmanager webhook config pointed at it, and a
`docker-compose.yml` running Alertmanager, Redis, an MQTT broker, and the bridge, runnable
on a laptop.

```python
# bridge.py
import json
from http.server import BaseHTTPRequestHandler, HTTPServer

import paho.mqtt.client as mqtt
import redis

r = redis.Redis(host="redis", port=6379, decode_responses=True)
mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
mqtt_client.connect("mosquitto", 1883)
mqtt_client.loop_start()


def topic_for(labels: dict) -> str:
    alertname = labels.get("alertname", "unknown")
    instance = labels.get("instance", "unknown")
    return f"alerts/{alertname}/{instance}"


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        payload = json.loads(self.rfile.read(length))

        for alert in payload.get("alerts", []):
            fingerprint = alert["fingerprint"]
            topic = topic_for(alert.get("labels", {}))

            if alert["status"] == "firing":
                r.set(f"alert:{fingerprint}", json.dumps(alert))
                mqtt_client.publish(topic, json.dumps(alert), qos=1, retain=True)
            else:  # resolved
                r.delete(f"alert:{fingerprint}")
                mqtt_client.publish(topic, payload=None, qos=1, retain=True)

        self.send_response(200)
        self.end_headers()

    def log_message(self, format, *args):
        pass


if __name__ == "__main__":
    HTTPServer(("0.0.0.0", 8000), Handler).serve_forever()
```

```dockerfile
# Dockerfile.bridge
FROM python:3.12-slim
RUN pip install --no-cache-dir redis==5.0.8 paho-mqtt==2.1.0
COPY bridge.py /bridge.py
CMD ["python3", "/bridge.py"]
```

```yaml
# alertmanager.yml
route:
  receiver: mqtt-bridge
  group_wait: 5s
  repeat_interval: 1h

receivers:
  - name: mqtt-bridge
    webhook_configs:
      - url: http://bridge:8000/
        send_resolved: true
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
# alert_rules.yml
groups:
  - name: demo
    rules:
      - alert: AlwaysFiringDemo
        expr: vector(1) == 1
        labels:
          severity: warning
        annotations:
          summary: "Demo alert used to exercise the MQTT bridge"
```

```yaml
# docker-compose.yml
services:
  redis:
    image: redis:7.4-alpine

  mosquitto:
    image: eclipse-mosquitto:2.0.18
    command: mosquitto -c /mosquitto-no-auth.conf
    ports:
      - "1883:1883"

  bridge:
    build:
      context: .
      dockerfile: Dockerfile.bridge
    depends_on:
      - redis
      - mosquitto

  prometheus:
    image: prom/prometheus:v2.55.1
    volumes:
      - ./prometheus.yml:/etc/prometheus/prometheus.yml:ro
      - ./alert_rules.yml:/etc/prometheus/alert_rules.yml:ro
    ports:
      - "9090:9090"

  alertmanager:
    image: prom/alertmanager:v0.27.0
    depends_on:
      - bridge
    volumes:
      - ./alertmanager.yml:/etc/alertmanager/alertmanager.yml:ro
    ports:
      - "9093:9093"
```

`eclipse-mosquitto:2.0.18` needs no-auth listening enabled explicitly from 2.0 onward; the
`command` override above tells it to use the image's built-in permissive default
configuration, which is fine for this local demonstration and not something to carry into a
real deployment.

Run it, subscribe to the retained topic, and let the always-firing demo alert reach the
bridge:

```bash
docker compose up -d
sleep 20
docker run --rm --network container:$(docker compose ps -q mosquitto) \
  eclipse-mosquitto:2.0.18 mosquitto_sub -h localhost -t 'alerts/#' -R -C 1
```

Expected output — the retained message for the firing alert, delivered immediately to a
subscriber that only just connected:

```json
{"status": "firing", "labels": {"alertname": "AlwaysFiringDemo", ...}, "fingerprint": "..."}
```

Confirm the same state exists in Redis:

```bash
docker compose exec redis redis-cli KEYS 'alert:*'
docker compose exec redis redis-cli GET 'alert:<fingerprint-from-above>'
```

Stop Prometheus to let the alert resolve, wait for the next Alertmanager notification cycle,
then check both stores again:

```bash
docker compose stop prometheus
sleep 90
docker compose exec redis redis-cli KEYS 'alert:*'   # empty
docker run --rm --network container:$(docker compose ps -q mosquitto) \
  eclipse-mosquitto:2.0.18 mosquitto_sub -h localhost -t 'alerts/#' -R -C 1 --retained-only
```

The Redis key is gone, and the retained MQTT message is empty — a subscriber connecting now
sees nothing on that topic, correctly, rather than a stale "firing" retained from before it
resolved.

## Conclusion

A retained message is only trustworthy if something explicitly clears it on the way out;
otherwise "retained" quietly becomes "permanently wrong the moment the underlying state
changes".

Two things generalise past this specific bridge:

**A fire-and-forget event stream and a queryable current-state store answer different
questions, and bridging one from the other means writing an explicit update-or-delete step,
not a blind republish.** Anywhere you convert events into state — webhooks into a cache,
a log into a materialised view — the delete path needs exactly as much attention as the
write path, and it is the one people forget because it produces no error when it's missing.

**Keep the durable source of truth outside the transport that carries it to consumers.**
Redis here is what makes the bridge's own state recoverable independent of MQTT broker
restarts; the retained MQTT messages are a projection of that state for subscribers, not the
record itself.
