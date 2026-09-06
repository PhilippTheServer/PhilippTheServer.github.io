---
layout: post
title: "How a VPN Client Silently Shrinks Your Pod Network MTU"
subtitle: "When a lower-MTU hop appears after the CNI has already picked 1500."
date: 2026-05-08 09:00:00 +0200
tags: [kubernetes, networking]
description: >-
  A CNI that auto-detects MTU from the node's default-route interface bakes
  in that value once, at start-up, and never revisits it. When a VPN client
  later puts a lower-MTU hop in the path, the mismatch fails silently rather
  than with an error, and this article shows how to reproduce, diagnose and
  fix it.
---

## The problem

A cluster node has a physical NIC with the usual Ethernet MTU of 1500. The CNI plugin
starts, looks at the interface holding the default route, sees 1500, and configures the
pod overlay to use it. Every pod on that node inherits an MTU of 1500 for its `eth0`.

Some time later, that node joins a WireGuard-based mesh VPN — for remote access, or to
reach a resource on another network. A WireGuard tunnel does not carry 1500-byte packets
end to end: the outer UDP/IP header and the encryption overhead eat into the payload, and
the usable MTU across the tunnel typically settles around 1420, sometimes lower. Nothing
tells the CNI. It configured the pod network once, when its daemon started, and does not
watch the routing table for changes afterwards.

Traffic that stays inside 1420 bytes works. A `curl` to a small JSON endpoint works. A
`ping` works — it sends 56-byte payloads by default, nowhere near either MTU. A health
check doing a `GET /healthz` reports everything fine. Then someone pulls a large file or
does a bulk database dump across that path, and it is slow — not failed, just slow, with
retransmits and timeouts that look exactly like an overloaded backend.

This is what makes it hard to notice: the failure is size-dependent, so it only shows up
under traffic no health check exercises, and it is silent rather than explicit. TCP has a
mechanism for exactly this situation, Path MTU Discovery, and it depends on an ICMP
message that VPN clients and firewalls routinely drop. The sender never learns the path
is narrower than it thinks, and instead of one clean error you get an indefinite string
of retransmissions that eventually time out.

## Working through it

### MTU auto-detection is a snapshot, not a policy

Flannel, Calico and most other CNI plugins offer to auto-detect the MTU for the pod
overlay by inspecting whichever interface currently holds the node's default route. That
is a reasonable default: most nodes have one NIC, and matching its MTU avoids
fragmentation on the common path. But "auto-detect" here means exactly one read, done
when the CNI's node agent starts. It is not a control loop, and it does not re-run when a
`wg0` or `tun0` interface appears later and takes over — or shares — the routing decision
for some destinations. Whatever value the agent read at start-up is what every pod gets,
until the agent itself restarts, which happens on a node reboot or a manual restart of
the CNI's daemonset pod, not as a response to network changes.

The practical consequence: the MTU Kubernetes reports for pod interfaces reflects the
state of the node's networking at one moment in the past, with no signal anywhere in
`kubectl` telling you whether that moment is still representative.

### Path MTU Discovery is not a safety net here

The correct behaviour for a TCP sender that emits a too-large packet is to have it
rejected by whichever router along the path cannot forward it at that size, with the
router sending back an ICMP "Fragmentation Needed and DF set" (or ICMPv6 "Packet Too
Big"). The sender's stack uses that message to lower its idea of the path MTU and
retransmits at the smaller size. This is Path MTU Discovery, and in principle it makes
MTU mismatches self-correcting.

In practice, it depends on that ICMP message surviving the trip back to the sender, and a
lot of infrastructure discards ICMP by default: VPN clients that only route the tunnel's
own traffic, firewalls with blanket ICMP-deny rules, and NAT implementations that do not
associate the ICMP error back to the connection that triggered it. When the message is
dropped, the sender never finds out anything is wrong. It keeps sending the same
oversized packet, it keeps getting silently dropped, and the connection stalls rather
than failing. This is well-known enough to have a name — "black-hole" PMTUD — but it is
easy to forget that your own infrastructure can cause it, not just some far-off network
you do not control.

So PMTUD is not a fix for a foreseeable low-MTU hop; it is a best-effort recovery for
MTUs you did not predict. Where you know in advance that a VPN or any other
encapsulation is in the path, configure the correct MTU explicitly rather than hope
discovery papers over the gap.

### Pin the MTU instead of trusting detection

Every mainstream CNI that supports auto-detection also supports an explicit override, and
the fix is to use it once you know a lower-MTU hop exists anywhere downstream of the pod
network. The exact field differs by plugin and version — Flannel exposes an `MTU` key
inside its backend configuration, historically under `net-conf.json` in the
`kube-flannel` ConfigMap; Calico exposes it through its installation configuration or a
`CALICO_IPV4POOL_MTU`-style environment variable depending on how it is installed — so
treat these as pointers, not gospel, and confirm the current key against your CNI
version's own documentation before changing anything.

The number to set is not "slightly less than 1500" as a guess. It is the true minimum
MTU across every hop the packet will actually cross, minus the overhead of the pod
network's own encapsulation if it has one (VXLAN, IPIP and similar all add their own
header). If you do not know the VPN's effective MTU precisely, measure it — the
demonstration below is that measurement, done with tools you already have.

## The solution

### Reproducing the mismatch with network namespaces

This needs only root on a Linux machine — no cluster, no VPN, no VM. It needs three
namespaces, not two: MTU is enforced on the *sending* interface only, so a bare veth pair
with mismatched ends cannot show the ICMP behaviour Path MTU Discovery depends on. You
need something in the middle actually forwarding, as a real intermediate hop would.
`podside` stands in for a pod whose interface still believes the MTU is 1500, because
that is what the CNI configured before the VPN existed; `router` stands in for the
node's egress point, where the tunnel actually takes over; `vpnside` stands in for
whatever sits on the far side of it.

```bash
# Namespaces
sudo ip netns add podside
sudo ip netns add router
sudo ip netns add vpnside

# podside <-> router
sudo ip link add veth-pod type veth peer name veth-r1
sudo ip link set veth-pod netns podside
sudo ip link set veth-r1 netns router

# router <-> vpnside
sudo ip link add veth-r2 type veth peer name veth-vpn
sudo ip link set veth-r2 netns router
sudo ip link set veth-vpn netns vpnside

# Addressing: two documentation-safe /30s either side of the router
sudo ip netns exec podside ip addr add 192.0.2.1/30 dev veth-pod
sudo ip netns exec router  ip addr add 192.0.2.2/30 dev veth-r1
sudo ip netns exec router  ip addr add 198.51.100.1/30 dev veth-r2
sudo ip netns exec vpnside ip addr add 198.51.100.2/30 dev veth-vpn

# Bring everything up
for ns in podside router vpnside; do
  sudo ip netns exec "$ns" ip link set lo up
done
sudo ip netns exec podside ip link set veth-pod up
sudo ip netns exec router  ip link set veth-r1 up
sudo ip netns exec router  ip link set veth-r2 up
sudo ip netns exec vpnside ip link set veth-vpn up

# Routing: podside and vpnside each only know the router
sudo ip netns exec podside ip route add 198.51.100.0/30 via 192.0.2.2
sudo ip netns exec vpnside ip route add 192.0.2.0/30 via 198.51.100.1

# The router actually forwards between the two links
sudo ip netns exec router sysctl -qw net.ipv4.ip_forward=1

# The stale CNI value: the pod-side link, and the router's near side, still believe 1500
sudo ip netns exec podside ip link set veth-pod mtu 1500
sudo ip netns exec router  ip link set veth-r1 mtu 1500

# The real ceiling: the router's far side, standing in for the VPN tunnel interface
sudo ip netns exec router  ip link set veth-r2 mtu 1420
sudo ip netns exec vpnside ip link set veth-vpn mtu 1420
```

Confirm connectivity works for small packets, exactly as a health check would see it:

```bash
sudo ip netns exec podside ping -c 2 198.51.100.2
```

```
2 packets transmitted, 2 received, 0% packet loss
```

Now send a full-size packet with the Don't Fragment bit set, matching what a large TCP
segment does. 1472 bytes of ICMP payload plus the 28-byte IP/ICMP header is exactly 1500,
the size podside's own interface believes it can emit:

```bash
sudo ip netns exec podside ping -M do -s 1472 -c 2 198.51.100.2
```

```
From 192.0.2.2 icmp_seq=1 Frag needed and DF set (mtu = 1420)
From 192.0.2.2 icmp_seq=2 Frag needed and DF set (mtu = 1420)
```

This is Path MTU Discovery working correctly. Podside's interface is large enough to emit
the packet, so nothing stops it locally; the router is the first point that actually
cannot forward it, and says so, with the true MTU, over ICMP. A sender that heeds this
lowers its estimate and retries smaller. The failure this article opened with is not
"PMTUD is broken" — it is "PMTUD's message never arrived".

Prove that by making the router drop the very message it just sent, which is what a VPN
client or a firewall with a blanket ICMP-deny rule does in practice:

```bash
sudo ip netns exec router iptables -A OUTPUT -p icmp --icmp-type destination-unreachable -j DROP

sudo ip netns exec podside ping -M do -s 1472 -c 3 -W 2 198.51.100.2
```

```
3 packets transmitted, 0 received, 100% packet loss
```

Nothing local rejects the oversized packet, the router silently drops the ones that do
not fit and swallows its own warning, and podside gets nothing back — not an error, a
hang. This is the black hole described earlier, reproduced on purpose. Revert the rule
and align podside's own MTU to the router's real ceiling, which fixes it without
depending on any message ever arriving:

```bash
sudo ip netns exec router iptables -D OUTPUT -p icmp --icmp-type destination-unreachable -j DROP
sudo ip netns exec podside ip link set veth-pod mtu 1420

sudo ip netns exec podside ping -M do -s 1392 -c 2 198.51.100.2
```

```
2 packets transmitted, 2 received, 0% packet loss
```

1392 is 1420 minus the 28-byte header — the largest ICMP payload that now fits without
fragmentation, on a path where nothing would otherwise tell you when it stops fitting.
Clean up:

```bash
sudo ip netns del podside
sudo ip netns del router
sudo ip netns del vpnside
```

### Checking a real cluster

Against an actual cluster, run two pods and probe between them with the same
`ping -M do -s <size>` technique, sized just above your suspected true MTU minus 28:

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: mtu-probe
spec:
  containers:
    - name: probe
      image: busybox:1.36
      command: ["sleep", "3600"]
```

```bash
kubectl apply -f mtu-probe.yaml
kubectl exec -it mtu-probe -- ip link show eth0   # confirm the MTU Kubernetes reports
kubectl exec -it mtu-probe -- ping -M do -s 1472 -c 3 <other-pod-ip>
```

If that fails or times out while a smaller `-s` value succeeds, the pod network's
advertised MTU is larger than something in the real path can carry — the same problem,
reproduced on your own infrastructure rather than in a namespace lab.

## Conclusion

**PMTUD is not a safety net you can rely on for a hop you already know about.** It exists
to recover from mismatches nobody predicted, and it depends on ICMP messages that VPNs
and firewalls commonly filter. Where you know a lower-MTU hop exists, configure for it
explicitly.

**Auto-detected MTU is a value read once, not kept correct.** The CNI agent reads the
default-route interface's MTU at start-up and never revisits it, so anything that
changes the node's effective path MTU afterwards — a VPN client chief among them —
leaves a stale value in place indefinitely.

**Size-dependent failures point the investigation the wrong way by default.** A problem
that only appears on large transfers and presents as slowness reads as an application or
database issue. Treat "works for small requests, degrades for large ones" as a specific,
recognisable network-layer symptom before spending time in application profiling.

**When you know the ceiling, set it.** Measure the true minimum MTU across the whole
path — the namespace lab above is that technique, portable to any pair of hosts — and
pin the CNI's MTU to it rather than trust a mechanism that only ever runs once.
