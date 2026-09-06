---
layout: post
title: "Powering and Discovering Peripherals over PoE with Multicast Announcements"
subtitle: "One cable for power and data, and a beacon that removes manual addressing."
date: 2025-10-14 09:00:00 +0200
tags: [networking, embedded]
description: >-
  Running separate power cabling to every sensor multiplies installation
  cost, and without some form of discovery, every new module has to be
  manually addressed and registered before anything can use it. This covers
  what Power over Ethernet actually buys you, and builds a small
  multicast-based announcement protocol so peripherals can be plugged in and
  found automatically, with a working announcer and collector you can run in
  two containers.
---

## The problem

A sensor that needs both power and a network connection is, on paper, two problems: run
mains or low-voltage power to it, and run Ethernet or Wi-Fi to it. In practice, running two
separate cables to the same enclosure roughly doubles the installation labour and doubles
the things that can fail — a power brick dies, a PoE injector's fan dies, a length of
cable gets damaged and now you're troubleshooting which of two paths broke.

Power over Ethernet (802.3af, up to about 15.4 W, or 802.3at, up to about 30 W at the
source) solves the cabling half by carrying both over one Ethernet cable. That part is
well understood and mostly a hardware decision: a PoE switch or injector, a PD
(powered-device) controller on the peripheral, done.

The half that is easy to skip is what happens *after* the cable is plugged in. Without
some form of discovery, a newly connected sensor is an unknown DHCP lease with no
indication of what it is, and someone has to go find its IP address and manually add it to
whatever system is supposed to talk to it. That defeats a large part of the point of
making installation cheap: the cabling got easier, but commissioning a new unit is still a
manual, error-prone step, and it is the step that scales with the number of sensors, not
the number of cable runs.

## Working through it

### Why multicast rather than a central registry

The peripheral could POST its presence to a known collector address, but that requires the
peripheral to know the collector's address in advance — which is exactly the kind of
manual configuration discovery is supposed to remove, and it means a peripheral moved to a
different site needs reconfiguring before it can be found again.

Multicast inverts that: the peripheral announces itself to a well-known group address that
needs no per-site configuration, and anything listening on that group — one collector,
several, or a debugging laptop — hears it. The peripheral doesn't need to know who is
listening, or how many listeners there are.

### Picking the announcement content

The beacon needs to carry enough for a collector to act without a follow-up request:
a stable device identifier, a type, and the address to actually talk to the device on
(multicast tells you a device exists; it is not how you'd exchange the actual sensor
data, which usually wants a normal unicast connection). Keeping it as flat JSON keeps the
listener trivial and makes the protocol easy to describe in one paragraph, which matters
because every future device type has to implement the sending half correctly.

### The caveat that only shows up on real switch hardware

This is the part worth stating plainly rather than glossing over: multicast that works
perfectly on a laptop, in Docker, and on an unmanaged switch can silently stop working the
moment it crosses a managed switch with IGMP snooping enabled and no IGMP querier
configured. IGMP snooping is on by default on most managed switches, and its entire
purpose is to stop flooding multicast to every port — which is correct behaviour for video
streaming, and exactly wrong for a discovery beacon, because the switch will prune the
group to ports it has seen an IGMP join from, and without a querier present, a passive
listener that never sends a join can simply stop receiving. If discovery works in testing
and then stops working once devices move to the production switch, this is the first thing
to check, not the application code.

### Choosing a TTL and a repeat interval deliberately

A multicast TTL of 1 keeps the beacon on the local segment by default, which is usually
what you want — a discovery protocol that leaks across a router into an unrelated network
is a problem waiting to be found. Repeating the announcement periodically, rather than
once at boot, means a collector that started after the peripheral still discovers it, and
a peripheral that changed address (a DHCP lease renewing to a different value) is
re-announced within one interval rather than going stale silently.

## The solution

```python
# announcer.py
"""Runs on a peripheral. Periodically announces its presence over multicast."""
from __future__ import annotations

import json
import socket
import sys
import time
import uuid

MULTICAST_GROUP = "239.10.10.10"
MULTICAST_PORT = 9100
ANNOUNCE_INTERVAL_SECONDS = 5


def make_socket() -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
    return sock


def announce_forever(device_id: str, device_type: str, service_port: int) -> None:
    sock = make_socket()
    my_ip = socket.gethostbyname(socket.gethostname())

    while True:
        payload = json.dumps(
            {
                "device_id": device_id,
                "device_type": device_type,
                "address": my_ip,
                "port": service_port,
            }
        ).encode("utf-8")
        sock.sendto(payload, (MULTICAST_GROUP, MULTICAST_PORT))
        print(f"announced {device_id} at {my_ip}:{service_port}", file=sys.stderr)
        time.sleep(ANNOUNCE_INTERVAL_SECONDS)


if __name__ == "__main__":
    device_id = f"sensor-{uuid.getnode():012x}"
    announce_forever(device_id, device_type="temperature-sensor", service_port=8080)
```

```python
# discovery.py
"""Runs on a collector. Listens for announcements and keeps an inventory."""
from __future__ import annotations

import json
import socket
import struct
import time

MULTICAST_GROUP = "239.10.10.10"
MULTICAST_PORT = 9100
STALE_AFTER_SECONDS = 20


def make_listener_socket() -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("", MULTICAST_PORT))

    group = socket.inet_aton(MULTICAST_GROUP)
    membership_request = struct.pack("4sL", group, socket.INADDR_ANY)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, membership_request)
    return sock


def listen_forever() -> None:
    sock = make_listener_socket()
    inventory: dict[str, dict] = {}

    while True:
        sock.settimeout(1.0)
        try:
            data, _ = sock.recvfrom(4096)
        except socket.timeout:
            _drop_stale(inventory)
            continue

        announcement = json.loads(data.decode("utf-8"))
        announcement["last_seen"] = time.monotonic()
        is_new = announcement["device_id"] not in inventory
        inventory[announcement["device_id"]] = announcement

        if is_new:
            print(f"discovered {announcement['device_id']} at "
                  f"{announcement['address']}:{announcement['port']}")


def _drop_stale(inventory: dict[str, dict]) -> None:
    now = time.monotonic()
    stale = [
        device_id
        for device_id, entry in inventory.items()
        if now - entry["last_seen"] > STALE_AFTER_SECONDS
    ]
    for device_id in stale:
        print(f"lost {device_id} (no announcement in {STALE_AFTER_SECONDS}s)")
        del inventory[device_id]


if __name__ == "__main__":
    listen_forever()
```

```dockerfile
# Dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY announcer.py discovery.py ./
```

```yaml
# docker-compose.yml
services:
  sensor-1:
    build: .
    command: ["python", "announcer.py"]
    networks:
      - discovery-net

  sensor-2:
    build: .
    command: ["python", "announcer.py"]
    networks:
      - discovery-net

  collector:
    build: .
    command: ["python", "discovery.py"]
    networks:
      - discovery-net

networks:
  discovery-net:
    driver: bridge
```

Running it:

```bash
docker compose up --build
# collector-1   | discovered sensor-02420ac12345 at 172.19.0.3:8080
# sensor-1-1    | announced sensor-02420ac12345 at 172.19.0.3:8080
# collector-1   | discovered sensor-0242ac120004 at 172.19.0.4:8080
# sensor-2-1    | announced sensor-0242ac120004 at 172.19.0.4:8080

docker compose stop sensor-1
# after ~20s:
# collector-1   | lost sensor-02420ac12345 (no announcement in 20s)
```

Docker's default bridge network is a single-host virtual switch with no IGMP snooping
applied, which is exactly why this works cleanly in Compose and is not, by itself,
evidence that it will work unmodified across a real access-layer switch — see the caveat
above before assuming production behaves the same way.

## Conclusion

The interesting design decision here was not "use multicast" — that part is standard — it
was choosing what the beacon carries (just enough to open a real connection, nothing that
belongs in the connection itself) and being explicit about staleness, since a discovery
system with no expiry just accumulates devices that were unplugged months ago.

Three points generalise beyond sensors and PoE specifically:

**A protocol that "just works" in every test environment can still fail in production for
reasons entirely outside the application.** IGMP snooping without a querier is a switch
configuration issue, and no amount of debugging the Python will find it.

**Discovery and data transport are different concerns and should use different
mechanisms.** Multicast is well suited to "does anything like this exist" and poorly
suited to actually moving sensor readings, which want a normal, addressed, unicast
connection once discovery has supplied the address.

**Anything that announces presence should also expire.** A registry that only grows is not
an inventory, it is a log — staleness handling is what turns the first into the second.
