---
layout: post
title: "WireGuard Route Selection: Per-Host /32 Versus Subnet Routes With NAT"
subtitle: "Why a subnet route to a NAT gateway breaks when some of its hosts are peers too."
date: 2026-05-29 09:00:00 +0200
tags: [networking, linux]
description: >-
  WireGuard's AllowedIPs sets both which packets a peer may send and which
  route the kernel installs for it, and overlapping AllowedIPs resolve by
  longest-prefix-match rather than by which peer you meant. This walks
  through a reproducible case where that silently breaks the return path,
  and how to make the route explicit on both ends.
---

## The problem

WireGuard has one knob for routing: `AllowedIPs`. It does two unrelated jobs at once.
First, it is a packet filter — a peer's `AllowedIPs` is the set of source addresses
WireGuard will accept from an encrypted packet coming from that peer, and the set of
destination addresses this host is willing to encrypt *for* that peer. Second, on Linux,
`wg-quick` (or a manual `ip route` step) uses the same list to install kernel routes, so
`AllowedIPs` also decides which interface and which peer a locally-originated packet goes
out through.

That overload is fine as long as `AllowedIPs` across your peers do not overlap. It stops
being fine the moment one peer is a NAT gateway advertising a whole subnet, and another
peer is a single host that happens to live inside that subnet.

Concretely: peer A (a gateway) advertises `AllowedIPs = 10.10.0.0/24`. Peer B is a real
WireGuard peer at `10.10.0.5/32`, reachable directly, not through A. Both entries exist on
the same host at the same time. The kernel resolves overlapping routes by
longest-prefix-match, so a packet to `10.10.0.5` takes the `/32` route to B directly. That
part looks correct — B gets your packet.

The asymmetry shows up on the way back. If B's own configuration, or a router between you
and B, expects that its subnet-mates reach it *via* A — because A is the box everyone else
in `10.10.0.0/24` uses for that peer, or because B's WireGuard config was written assuming
its `/24` peer-list matches what its peers use — the return traffic goes through A instead
of back to you directly. You sent to B directly; B answers via A; A does not recognise the
flow, or re-encapsulates it, or simply forwards it out an interface where your kernel does
not expect it and drops it as a martian or answers with the wrong source address.

The result is a connection that never establishes, or one that establishes and then hangs
on the first larger packet, and `ping` alone will not tell you why: an ICMP echo can
succeed in one direction and vanish in the other, and `ping` only reports "no reply", not
which leg failed. It is easy to get wrong because nothing about the WireGuard config on
either end looks broken in isolation — peer A's `/24` is a perfectly normal gateway
config, and peer B's `/32` is a perfectly normal direct-peer config. The bug is only
visible when you look at both configs together and ask which route the kernel actually
installs when they coexist, which is not something `wg show` or `wg-quick` will flag for
you.

## Working through it

### AllowedIPs is crypto-key routing, not IP routing

WireGuard's own term for this is cryptokey routing: a received packet is only accepted if
its source IP falls inside the `AllowedIPs` of the peer whose key decrypted it, and an
outgoing packet is only encrypted for a peer if its destination falls inside that peer's
`AllowedIPs`. This lookup happens in the WireGuard driver itself, independent of the
kernel routing table.

`wg-quick` then does a second, unrelated thing: for every `AllowedIPs` entry, it runs `ip
route add <prefix> dev <wg-iface>` (falling back to a table lookup and split /1 routes
only when it needs to override an existing default route). That is what makes a locally
generated packet reach the interface at all. Two peers with overlapping `AllowedIPs`
therefore both compete for kernel route installation, and only one entry wins per
destination — whichever route has the longest matching prefix, per standard FIB lookup
rules. The WireGuard driver's own cryptokey-routing table has no such ambiguity; it is the
kernel forwarding decision layered on top that introduces one.

### Longest-prefix-match picks the peer, not the topology

`ip route get 10.10.0.5` after installing both `10.10.0.0/24 dev wg0` (peer A) and
`10.10.0.5/32 dev wg0` (peer B) will report the `/32` route, because that is what
longest-prefix-match always does — the most specific prefix wins regardless of which
route was installed first or which peer is "supposed" to own that address on the wider
network. This is exactly the standard, well-understood behaviour of the Linux FIB. The bug
is not that Linux resolved it wrongly; the bug is that the two route sources disagree
about who is authoritative for `10.10.0.5`, and nothing enforces that they agree.

Route install *order* matters only for which entry ends up logged by `wg-quick`, not for
which one wins the lookup — the FIB re-sorts by prefix length regardless of insertion
order. Where order does matter is on the peer that owns the return leg: if B or the router
serving `10.10.0.0/24` was configured before you added B as a direct peer, its routing
table can still send B's answer to A, because nothing on B's side told it that this
particular correspondent is now reachable more directly.

### Reproducing it on one machine

You do not need two physical hosts to see this. Three network namespaces on one Linux box,
joined by veth pairs, are enough to build a "client", a "gateway peer", and a "direct
peer" and watch the asymmetry happen.

```
client (10.10.0.1)
  \
   \--- veth --- gw (10.10.0.2)         (gw also has AllowedIPs 10.10.0.0/24 towards client)
    \
     \--- veth --- peer (10.10.0.5)      (peer also has AllowedIPs 10.10.0.5/32 towards client)
```

The script below sets up client, gw and peer as three WireGuard endpoints in separate
namespaces, gives the client both a `/24` route via `gw` and a `/32` route via `peer`, and
then deliberately configures `peer` to answer back through `gw` rather than directly — the
same situation you get in the field when a host's own routing was written assuming its
subnet-mates come through the gateway.

Requires a Linux kernel with WireGuard support in-tree (5.6+, or any distribution kernel
with the module) and `wireguard-tools` 1.0.20210914 or newer for `wg`/`wg-quick`.

### Fixing it

There are exactly two consistent fixes, and they are two versions of the same idea: make
the more specific route explicit and identical on every host that has an opinion about it.

1. **Exclude the /32 from the subnet route.** On the gateway's peer configuration for the
   client — and everywhere else the `/24` is advertised — replace `10.10.0.0/24` with the
   `/24` minus the /32s that are independently reachable, listed as explicit smaller
   prefixes. WireGuard has no "subnet minus host" syntax, so this means listing the
   surrounding ranges by hand (or splitting the subnet at the point of the excluded host)
   in `AllowedIPs`.
2. **Make the direct /32 route explicit and consistent on both ends.** Add `10.10.0.5/32`
   as a peer route on every host that talks to B, including the ones on the far side of
   the gateway, so nothing anywhere still expects a subnet route to catch B's traffic.
   This is simpler when the number of directly-peered hosts is small.

In practice option 2 is easier to reason about and keep correct over time: option 1
requires recomputing the excluded ranges every time a new host graduates from
"subnet-only" to "also a direct peer", which is exactly the kind of manual bookkeeping
that silently drifts.

## The solution

The full reproduction, in one script. It creates three namespaces, wires them with veth
pairs (standing in for whatever underlay actually carries the WireGuard UDP traffic),
brings up WireGuard on each, and shows the broken and fixed states in sequence.

```bash
#!/usr/bin/env bash
# repro.sh - WireGuard subnet-route vs. /32-peer-route asymmetry, reproduced
# entirely in network namespaces on one machine. No physical network needed.
#
# Tested with wireguard-tools 1.0.20210914 (Linux 5.15+, in-tree wireguard.ko).
set -euo pipefail

WG=/usr/bin/wg
WGQ=wg-quick
NS=(client gw peer)
DIR=$(mktemp -d)
trap 'cleanup' EXIT

cleanup() {
  for n in "${NS[@]}"; do
    ip netns exec "$n" wg-quick down wg0 2>/dev/null || true
    ip netns del "$n" 2>/dev/null || true
  done
  rm -rf "$DIR"
}

for n in "${NS[@]}"; do
  ip netns add "$n"
done

# Underlay: client<->gw and client<->peer veth pairs, carrying WireGuard's UDP.
ip link add veth-c-gw type veth peer name veth-gw-c
ip link add veth-c-pe type veth peer name veth-pe-c

ip link set veth-c-gw netns client
ip link set veth-gw-c netns gw
ip link set veth-c-pe netns client
ip link set veth-pe-c netns peer

ip netns exec client ip addr add 192.168.50.1/24 dev veth-c-gw
ip netns exec client ip link set veth-c-gw up
ip netns exec gw     ip addr add 192.168.50.2/24 dev veth-gw-c
ip netns exec gw     ip link set veth-gw-c up

ip netns exec client ip addr add 192.168.60.1/24 dev veth-c-pe
ip netns exec client ip link set veth-c-pe up
ip netns exec peer   ip addr add 192.168.60.5/24 dev veth-pe-c
ip netns exec peer   ip link set veth-pe-c up

for n in "${NS[@]}"; do
  ip netns exec "$n" ip link set lo up
  umask 077
  ip netns exec "$n" wg genkey > "$DIR/$n.key"
  ip netns exec "$n" wg pubkey < "$DIR/$n.key" > "$DIR/$n.pub"
done

CLIENT_PUB=$(cat "$DIR/client.pub")
GW_PUB=$(cat "$DIR/gw.pub")
PEER_PUB=$(cat "$DIR/peer.pub")

# --- client: two peers, overlapping AllowedIPs on purpose ---
cat > "$DIR/client-wg0.conf" <<EOF
[Interface]
PrivateKey = $(cat "$DIR/client.key")
Address = 10.10.0.1/24
ListenPort = 51820

[Peer]
# gateway: advertises the whole subnet
PublicKey = $GW_PUB
Endpoint = 192.168.50.2:51820
AllowedIPs = 10.10.0.0/24
PersistentKeepalive = 5

[Peer]
# direct peer: advertises only itself, more specific than the line above
PublicKey = $PEER_PUB
Endpoint = 192.168.60.5:51820
AllowedIPs = 10.10.0.5/32
PersistentKeepalive = 5
EOF

# --- gw: thinks it owns the whole subnet, including .5 ---
cat > "$DIR/gw-wg0.conf" <<EOF
[Interface]
PrivateKey = $(cat "$DIR/gw.key")
Address = 10.10.0.2/24
ListenPort = 51820

[Peer]
PublicKey = $CLIENT_PUB
Endpoint = 192.168.50.1:51820
AllowedIPs = 10.10.0.1/32
PersistentKeepalive = 5
EOF

# --- peer: BROKEN state first - routes its replies via the gateway's /24,
#     not directly back to the client, because it only lists the client
#     as reachable through the /24-shaped view of the world.
cat > "$DIR/peer-wg0-broken.conf" <<EOF
[Interface]
PrivateKey = $(cat "$DIR/peer.key")
Address = 10.10.0.5/24
ListenPort = 51820

[Peer]
# wrong: this makes 10.10.0.0/24 (including the client's 10.10.0.1) route
# out towards the gateway's underlay address instead of the client directly
PublicKey = $GW_PUB
Endpoint = 192.168.50.2:51820
AllowedIPs = 10.10.0.0/24
PersistentKeepalive = 5
EOF

# --- peer: FIXED state - explicit /32 back to the client, consistent
#     with what the client itself uses for peer.
cat > "$DIR/peer-wg0-fixed.conf" <<EOF
[Interface]
PrivateKey = $(cat "$DIR/peer.key")
Address = 10.10.0.5/24
ListenPort = 51820

[Peer]
PublicKey = $CLIENT_PUB
Endpoint = 192.168.60.1:51820
AllowedIPs = 10.10.0.1/32
PersistentKeepalive = 5
EOF

install -m600 "$DIR/client-wg0.conf" "$DIR/gw-wg0.conf"
ip netns exec client bash -c "cp $DIR/client-wg0.conf /etc/netns/client-wg0.conf 2>/dev/null || true"

# wg-quick reads a named config from /etc/wireguard by default; point it at
# our tempdir copies instead so nothing on the real machine is touched.
run_wg_quick() { ip netns exec "$1" wg-quick up "$2"; }

cp "$DIR/client-wg0.conf" "$DIR/wg-client.conf"
cp "$DIR/gw-wg0.conf"     "$DIR/wg-gw.conf"
cp "$DIR/peer-wg0-broken.conf" "$DIR/wg-peer.conf"

ip netns exec client wg-quick up "$DIR/wg-client.conf"
ip netns exec gw     wg-quick up "$DIR/wg-gw.conf"
ip netns exec peer   wg-quick up "$DIR/wg-peer.conf"

echo "=== BROKEN: route the client selects for 10.10.0.5 ==="
ip netns exec client ip route get 10.10.0.5

echo "=== BROKEN: ping client -> peer (expect loss or one-way) ==="
ip netns exec client ping -c 3 -W 2 10.10.0.5 || true

echo "--- swapping peer to the fixed config ---"
ip netns exec peer wg-quick down "$DIR/wg-peer.conf"
cp "$DIR/peer-wg0-fixed.conf" "$DIR/wg-peer.conf"
ip netns exec peer wg-quick up "$DIR/wg-peer.conf"

echo "=== FIXED: route the client selects for 10.10.0.5 (unchanged, still /32) ==="
ip netns exec client ip route get 10.10.0.5

echo "=== FIXED: ping client -> peer ==="
ip netns exec client ping -c 3 -W 2 10.10.0.5
```

Run it as root (network namespaces and `wg-quick` both need `CAP_NET_ADMIN`):

```bash
sudo bash repro.sh
```

In the broken run, `ip route get 10.10.0.5` on the client already shows the correct
`/32` route out to `peer` directly — that half was never the problem. What fails is the
reply: `peer`'s own WireGuard config only knows the client as part of `10.10.0.0/24`
reached through `gw`'s address, so the encrypted reply is addressed and routed as if it
were going to any other host in that subnet, and it either never reaches `gw` correctly
attributed to this flow, or `gw` has no `/32` peer entry for the client's real endpoint
and drops it. The `ping` after the config swap succeeds because `peer` now has an explicit
`/32` entry pointing at the client's real endpoint, matching what the client itself already
has for `peer` — the two ends agree, and there is exactly one path in each direction.

Add a `traceroute` (or `tcptraceroute` for UDP-shy paths) alongside the ping in a real
deployment; in this loopback-only repro with two directly-connected veth hops there is
nothing for a traceroute to show, but on real infrastructure it is the fastest way to see
a reply coming back through a hop you did not expect.

## Conclusion

- `AllowedIPs` is doing two jobs — cryptokey routing inside WireGuard, and kernel route
  installation via `wg-quick` — and it is easy to reason about only one of them at a time.
  Any time you audit a WireGuard mesh, check both.
- Longest-prefix-match is not a bug to work around; it is the mechanism you are relying on
  to make the more specific peer reachable at all. The bug is always a disagreement
  between endpoints about which prefix is authoritative, not the lookup itself.
- A subnet route and a host route to a member of that subnet can only safely coexist if
  every host that has an opinion about the return path agrees on which one wins. That
  means the fix is never local to one config file — it has to be checked, and kept
  consistent, on the gateway, the direct peer, and anything else that carries part of the
  return leg.
- This is not specific to WireGuard. Any overlay that uses a single prefix list for both
  authorization and forwarding — most site-to-site VPN and SD-WAN route-distribution
  schemes included — inherits the same failure mode whenever a more specific route is
  added on one side without an equally specific route on the other.
