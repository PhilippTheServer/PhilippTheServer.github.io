---
layout: post
title: "ndots and the Accidental Search-Domain Leak in Pod DNS"
subtitle: "How a legitimate search-domain addition quietly reroutes ordinary external lookups."
date: 2026-05-05 09:00:00 +0200
tags: [kubernetes, dns, networking]
description: >-
  Kubernetes gives every pod a DNS search list and ndots:5 by default, which
  is usually harmless. Add a custom internal domain to that search list and
  it stops being harmless: an ordinary external hostname can resolve through
  the internal domain's own records before it is ever tried as written. Here
  is why, and three ways to close it off.
---

## The problem

Every pod gets a `/etc/resolv.conf` kubelet builds for it. By default it looks
something like this:

```
search default.svc.cluster.local svc.cluster.local cluster.local
options ndots:5
```

`ndots:5` tells the resolver: if a name being looked up has fewer than five dots in it,
don't assume it's already a complete, absolute name — try it with each entry in the
search list appended first, in order, and only fall back to trying it as written if all
of those fail. A name like `redis` (0 dots) obviously needs this to become
`redis.default.svc.cluster.local`. A name like `db.example.com` has only two dots, so it
gets exactly the same treatment: every search suffix is tried before the plain,
intended name ever is.

Normally this costs nothing but a handful of wasted NXDOMAIN round trips before the
real answer wins, and nobody notices. It stops being harmless the moment a pod's search
list contains a domain that is actually authoritative for something. This is what
`dnsConfig.searches` lets you do — add your own domain to the list, usually to reach
some other internal short name conveniently:

```yaml
# Looks reasonable. Added to reach an internal service by a short name.
dnsPolicy: ClusterFirst
dnsConfig:
  searches:
    - internal.example.net
```

If `internal.example.net` happens to be authoritative for a wildcard record — a
catch-all reverse proxy, or just a permissively configured internal zone — then an
ordinary, unrelated external lookup like `db.example.com` (two dots, so it qualifies
for the search-list treatment) can get answered by
`db.example.com.internal.example.net` matching that wildcard, before the real, absolute
`db.example.com` is ever queried. There is no error and no timeout. The application
gets an answer, accepts it as correct, and connects to whatever the internal wildcard
points at instead of the real external endpoint.

This is hard to catch because the search-domain addition is almost always made for a
legitimate, unrelated reason — reaching one specific internal name conveniently — and
the side effect only appears for names that happen to have fewer than five dots and
were not already being queried as an absolute name. That covers most ordinary
hostnames, which is exactly why the blast radius is larger than whoever added the
search domain was thinking about.

## Working through it

### Lower ndots, but know what it costs

Setting `ndots` lower for a specific workload stops the search list from being
consulted for names that already look sufficiently qualified:

```yaml
dnsConfig:
  options:
    - name: ndots
      value: "1"
```

With `ndots:1`, a name needs only one dot to be tried as-is first. This closes the leak
for anything resembling `db.example.com`. It also breaks the convenience that made
`ndots:5` the default in the first place: a bare service name like `redis` (0 dots)
no longer gets the search suffix applied before being tried absolutely, and an absolute
lookup for a bare `redis` will simply fail. This only makes sense either for workloads
that talk mostly to the outside world and rarely if ever to other in-cluster services by
short name, or combined with switching internal lookups to fully-qualified names too.
It's a workload-level decision, not something to apply cluster-wide without checking
what depends on the default.

### Use a trailing dot for names you control

A trailing dot marks a name as already absolute, and an absolute name skips the search
list entirely, regardless of `ndots`:

```
db.example.com.
```

This is the cheapest and most surgical fix available, because it needs no cluster-wide
configuration change at all — only a one-character edit wherever the hostname is
configured or hardcoded. Its limit is exactly that specificity: it only protects the
names you can actually edit. A name that arrives from a third-party library's default
configuration, or from a value nobody remembers to append the dot to, is not protected
by this fix.

### Treat every added search domain as blast radius

The most durable fix is upstream of both of the above: stop treating
`dnsConfig.searches` as a free convenience. Every domain added there is tried for
*every* non-absolute lookup a pod makes, not just the specific internal name you had in
mind when you added it. If a workload genuinely needs to resolve names inside an
internal domain, a narrower mechanism — an explicit resolver used only by the
workloads that need it, or a dedicated CoreDNS zone matched by name rather than
injected into every pod's search path — keeps an ordinary external lookup from ever
being able to match it by accident. The convenience of a short name is not worth making
every other lookup that workload makes ambiguous.

## The solution

Everything below runs on a laptop with `kind`, using its default CoreDNS. The zone
`internal.example.net` and the wildcard inside it exist only in this lab cluster —
nothing external is touched.

Add a self-contained internal zone with a wildcard record to CoreDNS:

{% raw %}
```bash
kubectl get configmap coredns -n kube-system -o jsonpath='{.data.Corefile}' > Corefile.orig

kubectl patch configmap coredns -n kube-system --type merge -p '
data:
  Corefile: |
    .:53 {
        errors
        health
        ready
        kubernetes cluster.local in-addr.arpa ip6.arpa {
            pods insecure
            fallthrough in-addr.arpa ip6.arpa
        }
        prometheus :9153
        forward . /etc/resolv.conf
        cache 30
        loop
        reload
        loadbalance
    }
    internal.example.net:53 {
        template IN A {
            answer "{{ .Name }} 60 IN A 203.0.113.50"
        }
    }
'

kubectl rollout restart deployment coredns -n kube-system
kubectl rollout status deployment coredns -n kube-system
```
{% endraw %}

The broken pod, demonstrating the leak:

```yaml
# pod-leak.yaml
apiVersion: v1
kind: Pod
metadata:
  name: dns-leak
spec:
  dnsPolicy: ClusterFirst
  dnsConfig:
    searches:
      - internal.example.net
  containers:
    - name: shell
      image: busybox:1.36
      command: ["sleep", "3600"]
```

```bash
kubectl apply -f pod-leak.yaml
kubectl wait --for=condition=Ready pod/dns-leak --timeout=60s
kubectl exec dns-leak -- getent hosts db.example.com
# 203.0.113.50   db.example.com.internal.example.net
```

`db.example.com` was never a real name anywhere. It resolved anyway, via
`internal.example.net`'s wildcard, because the search-list suffix was tried before the
absolute name — which, in this lab, would have correctly returned nothing.

Fix one: lower `ndots` for this workload.

```yaml
# pod-ndots1.yaml
apiVersion: v1
kind: Pod
metadata:
  name: dns-ndots1
spec:
  dnsPolicy: ClusterFirst
  dnsConfig:
    searches:
      - internal.example.net
    options:
      - name: ndots
        value: "1"
  containers:
    - name: shell
      image: busybox:1.36
      command: ["sleep", "3600"]
```

```bash
kubectl apply -f pod-ndots1.yaml
kubectl wait --for=condition=Ready pod/dns-ndots1 --timeout=60s
kubectl exec dns-ndots1 -- getent hosts db.example.com
# (no output, exit status 2 - NXDOMAIN, correctly not resolved)
```

Fix two: a trailing dot, without touching pod DNS config at all.

```bash
kubectl exec dns-leak -- getent hosts db.example.com.
# (no output, exit status 2 - NXDOMAIN, correctly not resolved)
```

Both fixes stop the wildcard from being consulted; which one is appropriate depends on
whether you control the pod's DNS configuration or only the application's own hostname
strings.

## Conclusion

**`ndots:5` is a reasonable default for short cluster-internal names, and a liability
the moment any domain with a wildcard sits anywhere in the search path.** The default
was not designed with that domain in mind; it just happens to apply to it too.

**A trailing dot is a one-character fix for any fully-qualified name you control.** It
needs no cluster or pod configuration change, which makes it the right first thing to
reach for whenever the name in question is something your own code or config sets.

**Every domain added via `dnsConfig.searches` is blast radius, not just convenience.**
It is tried for every lookup that isn't already absolute, not only the one internal name
you added it to reach — and that gap between intent and effect is exactly what makes
this leak easy to introduce and hard to notice.
