---
layout: post
title: "MQTT Persistent Sessions and Retained Messages for Offline Subscribers"
subtitle: "Keeping a client session and its last-known values alive across a disconnect."
date: 2025-12-30 09:00:00 +0200
tags: [redis, reliability, embedded]
description: >-
  A subscriber that drops off the network for a minute can miss every message
  published in that window, and a newly-connecting client sees nothing at all
  until the next publish. Persistent sessions and retained messages fix both,
  and this walks through the broker configuration and the client flags that
  make it work, plus a small Redis-backed cache for consumers that never speak
  MQTT at all.
---

## The problem

MQTT looks like it should just work across a flaky connection: the client reconnects, the
broker resumes, messages keep flowing. The default behaviour is not that.

A subscriber that connects with `clean_session=True` (or, in MQTT 5, `clean_start=True`
with no session expiry) tells the broker to forget it the moment it disconnects. Every
subscription is dropped. Every message published while it was offline, at any QoS, is
gone — there is no session left to queue it against. When the client reconnects it has to
resubscribe, and the broker has nothing to catch it up with.

A second, separate problem shows up even when a subscriber has been connected the whole
time: a client that subscribes to `sensors/+/temperature` for the first time sees nothing
until the next publish. MQTT is a message bus, not a store — a topic carries no memory of
its own by default, so a freshly-started dashboard shows a blank field until something
happens to publish again.

Both failures are easy to miss in development, because a laptop running a broker and a
client in two terminals rarely has either process actually go away. They show up in
production, on the first restart of a subscriber during a deploy, or the first time a
device drops off Wi-Fi for thirty seconds — and the failure mode is silence, not an error,
which makes it hard to notice until someone asks why a value never updated.

## Working through it

### Session state versus message content

MQTT actually offers two independent mechanisms here, and conflating them is the most
common mistake.

A **persistent session** is broker-side bookkeeping tied to a client ID: which topics this
client is subscribed to, and which QoS 1/2 messages are queued for it while it is offline.
It survives a disconnect only if the client asks for that at connect time and reconnects
with the same client ID.

A **retained message** is broker-side storage of the *last* message published to a topic,
independent of any client. Any client that subscribes afterwards — for the first time or
the hundredth — receives it immediately, before any new publish happens.

They solve different problems: persistent sessions catch messages published *during* an
outage, for a client that already existed. Retained messages give a *new* subscriber
current state without waiting for the next event.

### Getting persistence right at both ends

Persistence only works if three things line up, and losing any one silently degrades to
the default behaviour.

**The client must ask for it and use a stable ID.** `clean_session=False` (MQTT 3.1.1) or
`clean_start=False` with a non-zero `session_expiry_interval` (MQTT 5) tells the broker to
keep state. If the client ID changes between connections — a common bug when the ID is
derived from a random value generated at process start rather than something stable like
a hostname or serial number — the broker sees a brand new client with no session to
resume, and the flag does nothing useful.

**The broker must be configured to persist sessions across its own restarts**, not just
across a client's reconnect. Mosquitto keeps sessions in memory by default; a broker
restart during a deploy loses them unless `persistence` is enabled and pointed at a
volume.

**Messages must be QoS 1 or 2.** QoS 0 is fire-and-forget — the broker never queues it for
an offline client, persistent session or not, because there is nothing to redeliver.

### What retained messages cost

A retained message is stored once per topic and delivered to every future subscriber
until replaced or explicitly cleared (an empty payload with `retain=True` clears it). That
is cheap for a handful of state topics — `device/<id>/status`, `device/<id>/last-reading`
— and expensive as a design if applied to every message on a high-churn topic, because the
broker is now holding one retained value per distinct topic string forever, and a topic
that includes something unbounded (a request ID, a timestamp) leaks memory in the broker.
Retain state topics; do not retain event topics.

### Why bring Redis into an MQTT article

Persistent sessions solve the offline-subscriber problem only for clients that hold an
MQTT session — a service that queries current state without maintaining a live MQTT
connection has no session to be resumed. A small bridge process that subscribes once and
writes every value into Redis gives any consumer a synchronous, always-current read
without it having to speak MQTT or manage reconnect logic itself. This is a deliberate
trade: an extra moving part and one more place state can drift, in exchange for a plain
key lookup for consumers that have no business holding a persistent broker session.

## The solution

A complete, runnable setup: a Mosquitto broker with persistence enabled, a Redis cache, a
bridge that copies retained-worthy values from MQTT into Redis, and a publisher/subscriber
pair that demonstrate both persistent sessions and retained messages.

```yaml
# docker-compose.yml
services:
  mosquitto:
    image: eclipse-mosquitto:2.0.18
    ports:
      - "1883:1883"
    volumes:
      - ./mosquitto.conf:/mosquitto/config/mosquitto.conf:ro
      - mosquitto-data:/mosquitto/data

  redis:
    image: redis:7.2.4
    ports:
      - "6379:6379"

volumes:
  mosquitto-data:
```

```ini
# mosquitto.conf
listener 1883
allow_anonymous true

persistence true
persistence_location /mosquitto/data/
```

```python
# requirements.txt
# paho-mqtt==1.6.1
# redis==5.0.1

# bridge.py — subscribes once, writes last-known values into Redis.
import json
import paho.mqtt.client as mqtt
import redis

TOPIC_FILTER = "sensors/+/temperature"
BROKER_HOST = "localhost"
BRIDGE_CLIENT_ID = "state-bridge-01"

r = redis.Redis(host="localhost", port=6379, decode_responses=True)


def on_connect(client, userdata, flags, rc):
    print(f"bridge connected, rc={rc}, session_present={flags.get('session present')}")
    client.subscribe(TOPIC_FILTER, qos=1)


def on_message(client, userdata, msg):
    device_id = msg.topic.split("/")[1]
    r.set(f"state:{device_id}:temperature", msg.payload.decode())
    print(f"cached {msg.topic} -> {msg.payload.decode()}")


client = mqtt.Client(client_id=BRIDGE_CLIENT_ID, clean_session=False)
client.on_connect = on_connect
client.on_message = on_message
client.connect(BROKER_HOST, 1883, keepalive=60)
client.loop_forever()
```

```python
# publisher.py — publishes with retain=True so late subscribers get current state.
import time
import paho.mqtt.publish as publish

for i in range(3):
    value = 20.0 + i
    publish.single(
        topic="sensors/device-a/temperature",
        payload=str(value),
        qos=1,
        retain=True,
        hostname="localhost",
    )
    print(f"published {value}")
    time.sleep(2)
```

```python
# subscriber.py — a persistent-session subscriber; run it, kill it, run it again.
import paho.mqtt.client as mqtt

CLIENT_ID = "dashboard-01"  # must stay the same across reconnects


def on_connect(client, userdata, flags, rc):
    print(f"connected, session_present={flags.get('session present')}")
    client.subscribe("sensors/+/temperature", qos=1)


def on_message(client, userdata, msg):
    print(f"received {msg.topic} = {msg.payload.decode()}")


client = mqtt.Client(client_id=CLIENT_ID, clean_session=False)
client.on_connect = on_connect
client.on_message = on_message
client.connect("localhost", 1883, keepalive=60)
client.loop_forever()
```

Running it:

```bash
docker compose up -d
pip install paho-mqtt==1.6.1 redis==5.0.1

python subscriber.py &
SUBPID=$!
sleep 1
python publisher.py         # subscriber prints three "received" lines

kill $SUBPID                # simulate the subscriber going offline
python publisher.py         # two more values published while it's down

python subscriber.py        # reconnects with the SAME client ID
# -> connected, session_present=True
# -> received sensors/device-a/temperature = 22.0   (queued while offline)
# -> received sensors/device-a/temperature = 23.0
```

Starting a brand new subscriber that has never connected before still sees current state
immediately, because the last publish was retained:

```bash
python -c "
import paho.mqtt.subscribe as subscribe
msg = subscribe.simple('sensors/device-a/temperature', hostname='localhost')
print(msg.topic, msg.payload.decode())
"
# -> sensors/device-a/temperature 23.0
```

And the Redis bridge gives any process a synchronous read with no MQTT client of its own:

```bash
python bridge.py &
redis-cli GET state:device-a:temperature
# -> "23.0"
```

## Conclusion

Two mechanisms, two different failures, and neither is a substitute for the other.
Persistent sessions are about a *known client* catching up on what it missed; retained
messages are about *any client* getting current state without waiting.

**A stable client ID is the whole mechanism.** `clean_session=False` on a client ID that
changes every process start persists nothing, and the bug is silent — the broker just
treats every connection as new.

**QoS 0 and persistence do not mix.** If a message has to survive an offline subscriber,
it has to be published at QoS 1 or 2; the persistence flags on the client have no effect
on a message the broker never queued.

**Retain state, not events.** A retained message is a standing cost on the broker until
something replaces or clears it — fine for `last-known-value` topics, a liability on
topics with unbounded key space.

**A bridge into a plain store is a legitimate design, not a workaround.** Not every
consumer should have to be an MQTT client managing reconnects and session state; a small
number of services doing that once, on behalf of everything else, is a reasonable trade
for a large number of simple synchronous reads.
