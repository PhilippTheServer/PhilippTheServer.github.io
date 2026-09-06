---
layout: post
title: "A DNS Canary CronJob as a Regression Test for Split-Horizon Resolution"
subtitle: "Catching a reintroduced DNS blackhole before a workload times out on it."
date: 2026-05-01 09:00:00 +0200
tags: [kubernetes, dns, observability, testing]
description: >-
  A pod's search domain or CoreDNS configuration can resolve a public name to
  an internal blackhole, and once that bug is fixed it tends to come back the
  next time someone touches the Corefile for an unrelated reason. This walks
  through a CronJob that resolves a small, deliberate set of names on the
  exact path real pods use, so the regression fails a Job instead of a
  customer's request.
---

## The problem

A pod's resolver configuration — the search list Kubernetes writes into
`/etc/resolv.conf`, combined with whatever CoreDNS is told to do with a name —
can make a perfectly good public hostname resolve to something useless
inside the cluster, or make an internal name leak out and resolve nowhere at
all. The mechanism is a search-domain and `ndots` interaction; it is not this
article's subject, and if you have not met it before it is worth reading up
on separately. What matters here is the shape of the failure once it exists:
it does not look like an outage. A pod that resolves `api.example.com` to a
loopback address, or to a service that used to sit at that name, does not
error. It connects to *something*, and whatever sits on the other end either
times out slowly or answers with the wrong thing. Nobody gets paged for
"DNS returned an address that isn't a lie, just wrong."

The genuinely awkward part is what happens after you fix it. You find the
bad search domain, or the Corefile rewrite rule someone added eight months
ago for a migration that finished, and you remove it. The fix works. Six
months later, someone edits the CoreDNS `ConfigMap` for a completely
unrelated reason — adding a stub-domain forwarder for a new internal zone —
and reintroduces a rewrite that shadows the same name, or changes the
upstream resolver order so an internal search entry wins again. Nothing in
the change review catches it, because the reviewer is thinking about the new
zone, not a bug fixed and forgotten. The regression is silent by
construction: DNS misresolution does not fail loudly, and there is no test
in most clusters that would notice.

A one-off `dig` from your workstation at incident time does not protect
against this. It proves the bug exists right now, on your machine, using
your resolver. It says nothing about whether a pod, using the cluster's own
`resolv.conf` and the cluster's own CoreDNS, will get the same answer
tomorrow after the next ConfigMap edit. The only test that actually covers
the failure mode is one that runs continuously, from inside the cluster, on
the exact resolution path a real workload uses.

## Working through it

### It has to run where the workloads run

The bug lives in the interaction between a pod's `/etc/resolv.conf` — its
`nameserver` line, its `search` list, its `ndots` value — and whatever
CoreDNS does with a query it receives. All of that is pod-local
configuration. A check running on an operator's laptop, or even on a
cluster node outside a pod network namespace, exercises none of it: it has
its own resolver, its own search list (usually none), and often a direct
route to the internet that no pod has. The only way to test the thing pods
actually experience is to be a pod. A `CronJob` gets you that for free —
same service account defaults, same DNS policy, same `resolv.conf` the
scheduler would hand to anything else in the namespace — and it runs on a
schedule without anyone remembering to trigger it.

### Expectations are data, not a script

The temptation is to write a shell script with a handful of `if` statements
bolted on as the list of things worth checking grows. That is how a DNS
check turns into an unreadable pile of special cases within a year. The
better shape is a small, explicit table: a hostname, and what a correct
answer looks like. Three rows cover the interesting failure directions and
no more are needed:

- an internal name, which must resolve to an address inside the cluster's
  own service CIDR — this catches CoreDNS failing to answer for the cluster
  itself, or a broken `kubernetes.default` record;
- a real external name, which must resolve to a specific, known address —
  this is the one the split-horizon bug breaks: an internal rewrite or a
  leaking search domain answers with something else entirely;
- optionally, a name that must **not** resolve — proving that CoreDNS is not
  answering everything with a wildcard, which is its own class of
  misconfiguration and just as silent.

Testing "all of DNS" is not a coherent goal and produces a check nobody can
read six months later. Testing these three specific directions, with named
expectations, is something a reviewer can look at and understand without
running it.

### A failed Job has to reach somewhere a human looks

Kubernetes already does the hard part here: a `Job` that fails increments
`status.failed` and, with `backoffLimit` set low, stops retrying and sits
visibly failed. That is not nothing — `kubectl get jobs` shows it, and
`kube-state-metrics` exports it as `kube_job_status_failed`, which anything
already scraping Prometheus can alert on with a one-line rule. Building a
bespoke notification path duplicates infrastructure most clusters already
run for this. The honest scope here is the test signal itself: a Job that
fails cleanly and specifically when the regression reappears. Wiring that
into a specific alerting stack is different, environment-specific work, and
reproducing one here would be a worse example than admitting the boundary.

## The solution

Everything below runs against a `kind` cluster with its default CoreDNS
install — nothing here depends on infrastructure beyond a laptop.

```bash
kind create cluster --name dns-canary
kubectl cluster-info dump | grep -m1 service-cluster-ip-range
# kind's default is 10.96.0.0/12 — use whatever your output shows below
```

The check script and its expectations table, as a `ConfigMap`:

```yaml
# dns-canary-configmap.yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: dns-canary-script
  namespace: default
data:
  check.sh: |
    #!/bin/sh
    set -eu

    fail=0

    get_v4_addresses() {
      # Space-separated IPv4 addresses from nslookup's output for $1. The
      # pure dotted-quad match naturally excludes the query server's own
      # "Address: <ip>:53" line and any IPv6 (AAAA) answers — busybox's
      # nslookup prints both address families for a name that has both,
      # and a well-known name can carry more than one address of either.
      nslookup "$1" 2>/dev/null | awk '$1 == "Address:" && $2 ~ /^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$/ { printf "%s ", $2 }'
    }

    check_resolves_to() {
      name="$1"
      expected="$2"
      addrs=$(get_v4_addresses "$name")
      case " $addrs" in
        *" $expected "*)
          echo "OK: $name -> $expected (of: $addrs)"
          ;;
        *)
          echo "FAIL: $name resolved to [$addrs], expected to include '$expected'"
          fail=1
          ;;
      esac
    }

    check_in_cidr() {
      name="$1"
      cidr_prefix="$2"   # e.g. "10.96." — coarse check, laptop-scale only
      addrs=$(get_v4_addresses "$name")
      match=0
      for a in $addrs; do
        case "$a" in
          ${cidr_prefix}*) match=1 ;;
        esac
      done
      if [ "$match" = "1" ]; then
        echo "OK: $name -> $addrs (matches $cidr_prefix*)"
      else
        echo "FAIL: $name resolved to [$addrs], expected an address starting with '$cidr_prefix'"
        fail=1
      fi
    }

    check_nxdomain() {
      name="$1"
      if nslookup "$name" >/dev/null 2>&1; then
        echo "FAIL: $name resolved, expected NXDOMAIN"
        fail=1
      else
        echo "OK: $name did not resolve, as expected"
      fi
    }

    # 1. internal name must resolve inside the cluster's service CIDR
    check_in_cidr kubernetes.default.svc.cluster.local "10.96."

    # 2. known external name must resolve to its known, stable address
    check_resolves_to one.one.one.one 1.1.1.1

    # 3. a name that must not exist, must not resolve
    check_nxdomain this-name-should-never-exist.invalid

    exit $fail
```

`one.one.one.one` resolves to more than one address (Cloudflare publishes it against both
`1.1.1.1` and `1.0.0.1`, plus their IPv6 equivalents), which is exactly why the check
above asks "is the address I expect present" rather than "is the address I expect the
only, last one printed" — a check written the second way fails on a perfectly healthy
answer the moment a name legitimately carries more than one record.

The `CronJob` that runs it:

```yaml
# dns-canary-cronjob.yaml
apiVersion: batch/v1
kind: CronJob
metadata:
  name: dns-canary
  namespace: default
spec:
  schedule: "*/15 * * * *"
  concurrencyPolicy: Forbid
  jobTemplate:
    spec:
      backoffLimit: 0
      activeDeadlineSeconds: 60
      template:
        spec:
          restartPolicy: Never
          containers:
            - name: dns-canary
              image: busybox:1.36
              command: ["/bin/sh", "/scripts/check.sh"]
              volumeMounts:
                - name: script
                  mountPath: /scripts
          volumes:
            - name: script
              configMap:
                name: dns-canary-script
                defaultMode: 0o555
```

Apply both and run one instance ad hoc, without waiting for the schedule:

```bash
kubectl apply -f dns-canary-configmap.yaml -f dns-canary-cronjob.yaml

kubectl create job --from=cronjob/dns-canary dns-canary-manual-1
kubectl wait --for=condition=complete job/dns-canary-manual-1 --timeout=60s
kubectl logs job/dns-canary-manual-1
```

Expected output on a healthy cluster:

```
OK: kubernetes.default.svc.cluster.local -> 10.96.0.1  (matches 10.96.*)
OK: one.one.one.one -> 1.1.1.1 (of: 1.1.1.1 1.0.0.1 )
OK: this-name-should-never-exist.invalid did not resolve, as expected
```

`one.one.one.one` is used deliberately: it is Cloudflare's own hostname for
its resolver service, documented and stable enough to hardcode in a lab
test. In a real deployment, replace it with an endpoint you actually
control and can commit to — a hardcoded public hostname you don't own is a
liability the moment its operator changes it.

### Proving it catches the regression

Reintroduce a split-horizon-style blackhole entirely inside the lab
cluster, by patching CoreDNS's `Corefile` to rewrite the external name to
loopback. Save just the current `Corefile` text first, not a full
`kubectl get -o yaml` dump — restoring a full object dump with `kubectl
apply` later carries its old `resourceVersion` along and the API server
will refuse it as a conflict the moment anything else has touched the
object since:

```bash
kubectl -n kube-system get configmap coredns -o jsonpath='{.data.Corefile}' \
  > /tmp/coredns-original-corefile.txt

kubectl -n kube-system patch configmap coredns --type merge -p '{
  "data": {
    "Corefile": ".:53 {\n    errors\n    health\n    ready\n    kubernetes cluster.local in-addr.arpa ip6.arpa {\n       pods insecure\n       fallthrough in-addr.arpa ip6.arpa\n       ttl 30\n    }\n    rewrite name one.one.one.one localhost\n    forward . /etc/resolv.conf\n    cache 30\n    loop\n    reload\n    loadbalance\n}\n"
  }
}'

kubectl -n kube-system rollout restart deployment coredns
kubectl -n kube-system rollout status deployment coredns
```

Run the canary again:

```bash
kubectl create job --from=cronjob/dns-canary dns-canary-manual-2
kubectl wait --for=condition=complete job/dns-canary-manual-2 --timeout=60s || true
kubectl logs job/dns-canary-manual-2
```

```
OK: kubernetes.default.svc.cluster.local -> 10.96.0.1  (matches 10.96.*)
FAIL: one.one.one.one resolved to [127.0.0.1 ], expected to include '1.1.1.1'
OK: this-name-should-never-exist.invalid did not resolve, as expected
```

`kubectl get job dns-canary-manual-2` shows the Job failed rather than
completed — this is the signal a `kube_job_status_failed` alert would pick
up. Revert by rebuilding a clean `ConfigMap` from the saved `Corefile` text
and replacing the live one outright, and confirm it clears:

```bash
kubectl -n kube-system create configmap coredns \
  --from-file=Corefile=/tmp/coredns-original-corefile.txt \
  --dry-run=client -o yaml > /tmp/coredns-restore.yaml

kubectl -n kube-system replace -f /tmp/coredns-restore.yaml
kubectl -n kube-system rollout restart deployment coredns
kubectl -n kube-system rollout status deployment coredns

kubectl create job --from=cronjob/dns-canary dns-canary-manual-3
kubectl wait --for=condition=complete job/dns-canary-manual-3 --timeout=60s
kubectl logs job/dns-canary-manual-3
```

The last run returns to three `OK` lines. That round trip — pass, break,
fail, revert, pass again — is the actual proof that the canary detects the
class of regression it exists for, not just that the script runs.

## Conclusion

**A canary has to exercise the exact path real workloads use, not a proxy
for it.** Running the check as a pod, subject to the same `resolv.conf`
and the same CoreDNS every other workload gets, is what makes a pass
meaningful. A check from anywhere else is testing a different resolver.

**Codify expectations as a table, not as accumulating script logic.** A
list of names and expected outcomes stays reviewable as it grows; a script
that grows a new `if` branch per incident does not.

**A failed Job is not a test until its failure reaches something you look
at.** Kubernetes already surfaces a failed Job through `status.failed` and
`kube-state-metrics`; the remaining work is routing that into whatever
alerting you already run, not inventing a new channel.

**Keep the watched set small and deliberate.** Three names — one internal,
one external, one that must not exist — cover the failure directions that
matter. Trying to assert on DNS in general produces a check that nobody
can read, and one that nobody trusts when it goes red.
