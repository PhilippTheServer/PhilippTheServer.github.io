---
layout: post
title: "Multicast Service Discovery on a Host With a WireGuard Interface"
subtitle: "A discovery library started one server per IPv4 address, including the mesh VPN's. Kernel WireGuard answers every multicast send with ENOKEY, and the server restarted 68,805 times before anyone asked it why."
date: 2026-09-22 09:00:00 +0200
tags: [networking, linux, cpp, embedded, testing]
description: >-
  "Required key not available" (ENOKEY) on multicast sendto: kernel WireGuard has
  no peer for 239.0.0.0/8. Filter interfaces on IFF_MULTICAST and IFF_POINTOPOINT.
---

## The problem

The edge devices in this estate find their peripherals the way devices on a LAN have
found each other since the nineties: they shout. A small header-only C++ library called
slook sends a query to the multicast group `239.255.13.37:7331`, and anything on the
segment that offers a service answers with where to find it. There is no registry to
keep alive and no DNS to configure, which is exactly what you want on a box that is
shipped to a customer and plugged in by someone who has better things to do.

To make "the segment" mean "every segment", slook's host implementation walks the
machine's IPv4 addresses and starts one discovery server per address. That was correct
for years, because for years the only interfaces on these devices were Ethernet and
loopback, and loopback was already skipped.

Then the devices joined a [NetBird mesh](/posts/netbird-vpn/), and each one grew a
`wt0` interface with a `100.x` address. slook, being thorough, started a server on that
one too. The logs of the service embedding it began to look like this, at a steady
rhythm of once per second:

```text
slook: starting on 100.66.x.x
slook: Required key not available
slook: starting on 100.66.x.x
slook: Required key not available
```

Nothing was visibly broken. Discovery on Ethernet kept working, because the servers are
independent and only the `wt0` one was failing. That is the uncomfortable kind of bug:
the kind that works. By the time someone looked, one device had restarted that server
68,805 times, and its `wt0` interface had counted 774,830 transmit errors in 72 hours.
It was, by some distance, the most persistent process on the machine.

## Working through it

"Required key not available" is `strerror(ENOKEY)`, errno 126. It belongs to the kernel
keyring, and it is the last error anyone expects from a UDP socket. It is also what
kernel WireGuard returns when it is asked to send a packet it has no peer for.

That is the whole mechanism. WireGuard is a layer-3 point-to-point device with
cryptokey routing: every outgoing packet is matched against the peers' `AllowedIPs`,
and the matching peer's key encrypts it. A packet with no matching peer has no key, so
the transmit path drops it and hands `-ENOKEY` back up the stack. A multicast group is
never in anyone's `AllowedIPs`: there is no single peer to encrypt `239.255.13.37` for,
and WireGuard does not replicate packets to several peers. So every multicast send out
of `wt0` fails, forever, and "forever" at one retry per second adds up.

The interface tells you this if you ask it. Here is a container with one kernel
WireGuard interface next to its ordinary Ethernet interface, on Linux 6.12:

```bash
docker network create --subnet 172.30.0.0/24 mcdemo
docker run --rm --network mcdemo --ip 172.30.0.2 --cap-add NET_ADMIN \
  -v "$PWD/enokey.py:/enokey.py:ro" python:3.13-alpine sh -c '
    apk add -q iproute2
    ip link add wt0 type wireguard
    ip addr add 10.99.0.1/32 dev wt0
    ip link set wt0 up
    ip -o link show wt0  | cut -d" " -f2-3
    ip -o link show eth0 | cut -d" " -f2-3
    python /enokey.py'
docker network rm mcdemo
```

`enokey.py` sends one datagram to the group through each interface, selecting the
outgoing interface by its address, which is what a per-address discovery server does:

```python
import socket

GROUP, PORT = "239.255.13.37", 7331

for iface_ip in ("10.99.0.1", "172.30.0.2"):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(iface_ip))
    try:
        s.sendto(b"who is there?", (GROUP, PORT))
        print(f"{iface_ip}: sent")
    except OSError as e:
        print(f"{iface_ip}: {e}")
```

The output:

```text
wt0: <POINTOPOINT,NOARP,UP,LOWER_UP>
eth0@if746: <BROADCAST,MULTICAST,UP,LOWER_UP>
10.99.0.1: [Errno 126] Required key not available
172.30.0.2: sent
```

The flags are the answer, printed before the question. `eth0` says `MULTICAST`. `wt0`
does not, and says `POINTOPOINT` instead. The kernel knew all along that this interface
cannot carry a multicast group. slook never asked, because its interface walk read the
address out of each `ifaddrs` entry and ignored `ifa_flags` sitting in the same struct.

Two more details shaped the fix.

First, `IFF_MULTICAST` alone is not a sufficient test. Nothing stops a userspace tunnel
implementation from setting it on a TUN device that still cannot deliver a group to
anyone. A point-to-point link has exactly one other end, so "multicast" on it means
"unicast with extra steps" at best. The filter therefore has to reject
`IFF_POINTOPOINT` even when `IFF_MULTICAST` is set.

Second, slook did not only take addresses from `getifaddrs`. It also resolved the host's
own name and added whatever came back. On a host whose name resolves to the mesh
address, that path would put `wt0` straight back in. Fixing the walk and leaving the
resolver alone would have been a fix with a side door.

## The solution

The interface test is one function over the flags, kept in its own header so it can be
tested without a network:

```cpp
#include <net/if.h>

// Point-to-point tunnels (WireGuard/NetBird wt0) cannot deliver the discovery group: the
// kernel rejects every send with ENOKEY, and the server restarts into the same error.
inline bool canCarryMulticast(unsigned int flags) {
    return (flags & IFF_UP) != 0 && (flags & IFF_MULTICAST) != 0
        && (flags & (IFF_LOOPBACK | IFF_POINTOPOINT)) == 0;
}
```

The address walk now skips every `ifaddrs` entry that fails that test, and the
addresses from name resolution are kept only if they belong to an interface that passed
it. If `getifaddrs` itself fails, the resolver's answer is used unfiltered, as before:
degrading to the old behaviour is better than degrading to no discovery at all.

The check that holds it is a plain C++ program with no test framework, which is the
right amount of ceremony for five boolean cases:

```cpp
#include <net/if.h>
#include <cstdio>

inline bool canCarryMulticast(unsigned int flags) {
    return (flags & IFF_UP) != 0 && (flags & IFF_MULTICAST) != 0
        && (flags & (IFF_LOOPBACK | IFF_POINTOPOINT)) == 0;
}

int failures = 0;

void check(bool ok, char const* what) {
    std::printf("%s %s\n", ok ? "ok  " : "FAIL", what);
    failures += ok ? 0 : 1;
}

int main() {
    check(!canCarryMulticast(IFF_UP | IFF_RUNNING | IFF_POINTOPOINT | IFF_NOARP),
          "kernel WireGuard is skipped");
    check(!canCarryMulticast(IFF_UP | IFF_RUNNING | IFF_POINTOPOINT | IFF_NOARP | IFF_MULTICAST),
          "userspace tunnel that claims IFF_MULTICAST is skipped");
    check(!canCarryMulticast(IFF_UP | IFF_RUNNING | IFF_LOOPBACK), "loopback is skipped");
    check(!canCarryMulticast(IFF_BROADCAST | IFF_MULTICAST), "an interface that is down is skipped");
    check(canCarryMulticast(IFF_UP | IFF_BROADCAST | IFF_RUNNING | IFF_MULTICAST), "ethernet is used");
    return failures == 0 ? 0 : 1;
}
```

```bash
g++ -std=c++20 -Wall -Wextra -Werror interface_filter_test.cpp -o interface_filter_test
./interface_filter_test
```

The first check is not invented: its flags mirror what `ip link` printed for `wt0`
above. The library had no tests and no CI before
this change. It now has both, which says less about this bug than about how long
"it works" can pass for "it is tested".

The device image picked up the new slook revision, and its README now says which
interfaces discovery runs on, so the next person who adds a tunnel to these devices
finds the answer written down instead of in a log that scrolls at one line per second.

## Conclusion

The bug was one ignored field. `getifaddrs` returns an address and the interface's
flags in the same struct, and the flags already said, in two separate ways, that `wt0`
cannot carry multicast. Reading the address and skipping the flags is an easy thing to
write, and it stays correct for exactly as long as every interface on the machine is
Ethernet.

The error message did its best to mislead. ENOKEY sounds like a keyring or a TLS
problem, and in a sense it is a key problem: WireGuard has no key for a packet
addressed to everyone. Once you know that WireGuard reports "no peer for this
destination" as "no key", the message is precise. It is just precise in WireGuard's
vocabulary rather than yours.

The broader lesson is about code that enumerates interfaces. A mesh VPN, a container
bridge or a hypervisor bridge can appear on a host long after that code was written,
and anything that iterates "all interfaces" inherits them without a code change or a
review. Deciding what an interface is *for*, from the flags the kernel already
provides, turns a VPN rollout from something that quietly generates 774,830 errors
into something the service does not notice at all.
