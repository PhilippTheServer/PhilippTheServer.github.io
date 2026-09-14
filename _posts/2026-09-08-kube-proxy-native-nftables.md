---
layout: post
title: "A VPN Agent's Two Firewall Rules, and the Week They Broke Every Service on a Node"
subtitle: "kube-proxy manages its chains with iptables-nft, which cannot parse rules that another program wrote directly to nftables."
date: 2026-09-08 09:00:00 +0200
tags: [kubernetes, networking, linux, reliability]
description: >-
  A WireGuard-based mesh agent restarted on a running cluster node and added two
  native nftables rules to the forward chain that kube-proxy manages through
  iptables-nft. From that moment every service sync on the node aborted, and the
  node's rules went stale for two weeks without a single error in the cluster's
  usual places. This article reproduces the mechanism and the fix that recovers
  a node in one sync cycle.
---

## The problem

A cluster node's service endpoints stopped updating. Not all of them, and not
everywhere: on one worker, roughly a third of the endpoints behind each service
were missing, and the node's kube-proxy had restarted over sixty times while
still reporting nothing unusual. The cluster looked healthy. The service worked
for clients that happened to be routed to a healthy endpoint, and failed for the
rest. There was no event, no failed pod, no log line that named the cause.

The node in question also ran a WireGuard-based mesh agent — the thing that
gives every machine a stable address across sites. The mesh agent had been
restarted on that node a few days earlier, for an unrelated reason, and nobody
connected the two. That is the shape of this failure: the thing that broke the
node is a normal, expected, documented action (restart the agent), and the
thing it broke lives in a layer that no one thinks of as connected to a VPN.

The mechanism is worth understanding, because it is not exotic. On modern
systems, `iptables` and `nftables` are two frontends over the same kernel
tables. kube-proxy, in its default iptables mode, speaks to those tables
through the `iptables-nft` translation layer, which re-reads the chain it
manages before it rewrites it. That re-read assumes it understands every rule
it finds there. It does not, if another program has written rules into the same
chain in a form that has no iptables equivalent.

## Working through it

### What the mesh agent actually does to the firewall

A WireGuard-based mesh agent that manages its own firewall adds accept rules
for its tunnel interface whenever it (re)initialises while the machine is
running. On a node that already has a filter FORWARD chain, it does not create
a new table; it appends two rules to the existing chain, in native nftables
syntax:

```
nft list chain ip filter FORWARD
table ip filter {
    chain FORWARD {
        type filter hook forward priority 0; policy accept;
        iifname "wt0" accept
        oifname "wt0" ct state established,related accept
    }
}
```

Nothing in those two rules is wrong. They say: traffic coming in on the tunnel
interface is accepted, and traffic going back out of it in an established
connection is accepted. On a machine where that interface routes real traffic,
they are necessary. On a cluster node where the mesh interface is used only for
node-to-node reachability and nothing is routed through it, they carry no
traffic at all — but they are still *there*, in a chain that kube-proxy owns.

### Why kube-proxy cannot see them

kube-proxy in iptables mode does not write raw nftables. It shells out to
`iptables-nft`, which maintains a translation between the legacy iptables rule
model and the kernel's nftables representation. Before a sync, `iptables-nft`
dumps the current contents of the chain it is about to rewrite, parses them,
and reconciles. A rule that was written in native nftables and has no iptables
counterpart — for example, one whose match expression does not map back onto
the legacy set of matches — fails that parse:

```
iptables-nft-save
# Warning: ignoring unsupported match ...
```

or, depending on the version and the exact rule shape, an outright error.
Either way the sync aborts. The kube-proxy process notices, backs off, and
retries. It aborts again. It retries again. The chain is never rewritten, so
the endpoints it is supposed to install never change, and the node keeps
forwarding service traffic according to rules that were correct two weeks ago.

The important part of this failure mode is the *direction* of the blindness.
kube-proxy is not broken, and it is not misconfigured. It is doing exactly what
it is designed to do: refuse to touch a chain it cannot read. The defect is a
cohabitation problem — two programs, one chain, no agreement about who may
write to it and in what form.

### Why the cluster did not raise its voice

A node whose kube-proxy has stopped syncing does not leave a loud trace. The
node is Ready. The kube-proxy pod (or service, in an RKE2-style installation)
is Running. `kubectl get endpoints` shows the endpoints as they were last
successfully written, which is a plausible-looking subset. What is missing is
the delta: the endpoints that should have appeared and did not. Detecting that
requires comparing the endpoints a service *has* against the endpoints its
selector *says it should have*, per node, which is not something the default
tooling surfaces.

In the incident this article describes, the gap sat at 35 of 87 endpoints
missing on the one node, for seventeen days, discovered only because a client
kept landing on that node and kept failing.

### The fix: remove the foreign rules, let the next sync heal the chain

The rules carry no traffic on this estate, so the correct response is not to
teach kube-proxy to tolerate them or to move the mesh agent's firewall to a
different table — it is to delete the two foreign rules from the chain. The
next kube-proxy sync, which happens within its normal sync period, then reads a
chain it can parse, rewrites it, and the missing endpoints appear. No restart
of kube-proxy is needed, and nothing else on the node changes.

The deletion has to be done by rule handle, because the rules have no
distinguishing comment and their position in the chain is not stable:

```bash
# Find the handles of the mesh interface's rules in both address families
for family in ip ip6; do
  nft -a list chain $family filter FORWARD 2>/dev/null \
    | grep -E '^\s*(iifname|oifname) "wt0"' \
    | grep -oE 'handle [0-9]+' | awk '{print $2}'
done
```

and then, for each handle found:

```bash
nft delete rule ip filter FORWARD handle 2239
```

The second family matters, and is the part that is easy to miss: a dual-stack
kube-proxy manages `ip` *and* `ip6`, and the mesh agent wrote the identical
pair into both chains. Fixing only `ip` leaves the next sync failing on `ip6`
in exactly the same way, one sync later.

### Making the fix a role, not a runbook

A node will not stay clean on its own. Every future restart of the mesh agent
on a running node re-inserts the pair, and the next person who hits this will
again spend a week bisecting a healthy-looking cluster. The durable answer is a
small task in the node-convergence playbook that runs on every play: list the
foreign rules in both families, delete the ones it finds, and report nothing if
there were none. It is idempotent by construction — a second run finds nothing
to delete — and it turns the incident's recovery into a property the estate
has, rather than a procedure someone remembers.

{% raw %}
```yaml
# tasks/kube-proxy.yml — delete the mesh agent's native rules so kube-proxy can sync
- name: Find native wt0 rules in the filter FORWARD chains
  ansible.builtin.shell: >-
    set -o pipefail;
    nft -a list chain {{ item }} filter FORWARD 2>/dev/null
    | grep -E '^\s*(iifname|oifname) "wt0"'
    | grep -oE 'handle [0-9]+' | awk '{print $2}' || true
  args:
    executable: /bin/bash
  loop: [ip, ip6]
  register: wt0_forward
  changed_when: false
  check_mode: false

- name: Remove them so kube-proxy can sync again
  ansible.builtin.command: >-
    nft delete rule {{ item.0.item }} filter FORWARD handle {{ item.1 }}
  loop: "{{ wt0_forward.results | subelements('stdout_lines') }}"
  loop_control:
    label: "{{ item.0.item }} handle {{ item.1 }}"
```
{% endraw %}

The task is deliberately written to be boring when there is nothing to do:
empty `stdout_lines` means the loop body never runs, and the play reports no
change. When there *is* something to do, the change is reported, the node
recovers on its next sync, and the log line says which family and which handle
was removed — which is exactly the information the incident post-mortem needed
and did not have.

## The solution

The complete state of the fix, as it lives in the node playbook:

1. On every play, for both `ip` and `ip6`: list `filter FORWARD` with handles,
   select the rules matching the mesh interface name, and collect their handles.
2. Delete each collected rule by handle.
3. Let kube-proxy's own sync period do the rest — no restart, no flush.

And the two invariants that make it safe to run unattended:

- The rules are identified by their match on the mesh interface name, not by
  position or by absence of anything else, so a different program's rule in the
  same chain is never touched.
- The task is a no-op when the chains are clean, so it costs nothing on the
  ninety-nine runs where nothing is wrong and it is the only thing that fixes
  the one where something is.

The general lesson is the part that transfers to other estates: when two
programs share a kernel object — a netfilter chain here, but the same shape
applies to a cgroup, a sysctl namespace, a mount point — the failure does not
happen where either of them is, it happens in the cohabitation, and it is
silent until a client notices. The cheap, durable answer is not to make the
two programs smarter about each other; it is to have one named process whose
job is to keep the shared object in the form the more fragile of the two
requires, and to run it on every convergence pass so that drift is measured in
play cycles, not in weeks.

## Conclusion

A VPN agent doing a completely normal restart broke a cluster node for two
weeks, because it wrote two native nftables rules into a chain that kube-proxy
manages through a translation layer that cannot read them. The node stayed
Ready, the service stayed Running, and the missing endpoints were invisible to
everything that does not compare what a service has against what its selector
says it should have.

The fix is three lines of intent: find the foreign rules by their interface
match, in both address families, and delete them by handle. kube-proxy's next
sync then recovers the chain on its own.

The discipline worth keeping from this: a shared kernel object needs a named
owner of its shape, and the check that enforces that shape belongs in the
recurring convergence pass, not in the runbook that gets written after the
incident and read, at best, once.
