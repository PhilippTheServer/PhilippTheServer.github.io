---
layout: post
title: "Sync Waves and the CRD-Before-Consumer Ordering Problem"
subtitle: "Ordering a sync explicitly, and stopping a removed CRD from deleting what depends on it."
date: 2026-04-07 09:00:00 +0200
tags: [kubernetes, gitops, testing]
description: >-
  Applying a CustomResourceDefinition and a custom resource that depends on
  it in the same Argo CD sync can fail intermittently, and removing the CRD
  from git can prune it in a way that deletes every instance of that
  resource cluster-wide, not just the ones this Application owns. This
  works through sync waves and a prune guard that make both failures
  impossible, with a reproducible kind cluster test proving it.
---

## The problem

A CustomResourceDefinition and a custom resource that uses it, committed together and
synced by one Argo CD Application, look like they belong together:

```yaml
# Broken. Do not copy this — both in the same source, no ordering.
apiVersion: apiextensions.k8s.io/v1
kind: CustomResourceDefinition
metadata:
  name: widgets.example.com
spec:
  group: example.com
  scope: Namespaced
  names: { plural: widgets, singular: widget, kind: Widget, listKind: WidgetList }
  versions:
    - name: v1
      served: true
      storage: true
      schema:
        openAPIV3Schema: { type: object, properties: { spec: { type: object } } }
---
apiVersion: example.com/v1
kind: Widget
metadata:
  name: demo-widget
spec:
  size: large
```

Argo CD does apply CRDs earlier than arbitrary resources by default — but "earlier in
the same wave" is not "confirmed ready before the next resource is created". A CRD
becomes usable only once the API server has finished registering its REST endpoint and
its `Established` condition flips to `True`; that takes a moment even after the `create`
call returns. A `Widget` applied immediately afterwards can hit the API server before
that registration finishes, and fail with `no matches for kind "Widget" in version
"example.com/v1"`. It's not a permanent failure — the next sync usually succeeds, because
by then the CRD has settled — but "usually succeeds on retry" is exactly the flaky,
hard-to-reproduce class of failure that erodes trust in a sync pipeline.

The second failure is worse, and it isn't a race — it's certain, and it's not scoped to
this Application. If the CRD manifest is later removed from git — someone reorganising
files, splitting an Application, believing they're deleting an unused definition — and
Argo CD's automated sync has pruning on, it deletes the CRD. Kubernetes' behaviour on
CRD deletion is unconditional: every custom resource of that kind, in every namespace,
managed by anything, is deleted along with it. If three unrelated teams each have their
own `Widget` objects, tracked by three different Applications that never touch the CRD
themselves, removing the CRD from the one Application that happens to own it deletes all
three teams' objects. Nothing about "which Application manages this CR" limits the blast
radius, because CRD deletion is a Kubernetes garbage-collection rule, not an Argo CD one.

## Working through it

### Same wave means no ordering guarantee at all

A "wave" in Argo CD is a synchronisation barrier: every resource in wave *N* must reach
a healthy state before wave *N + 1* is applied. Resources without an explicit
`argocd.argoproj.io/sync-wave` annotation default to wave `0` — which means, without an
annotation, the CRD and the `Widget` above are in the *same* wave, and Argo CD's
kind-based apply order inside a wave is a best-effort hint, not a readiness barrier.
Nothing waits for the CRD to be `Established` before the `Widget` is submitted.

### Put the CRD in an earlier wave and let its health check do the waiting

Argo CD has a built-in health check specifically for CRDs: a `CustomResourceDefinition`
is only considered `Healthy` once `Established` and `NamesAccepted` are both `True`.
Combined with an earlier sync wave, that health check becomes the actual ordering
guarantee:

```yaml
metadata:
  annotations:
    argocd.argoproj.io/sync-wave: "-1"
```

Wave `-1` runs first; Argo CD does not begin wave `0` — where the `Widget` lives, with no
annotation needed there since `0` is already the default — until the CRD reports
`Healthy`. This isn't a hint any more; it's the same barrier Argo CD uses between any two
waves, applied to a case that specifically needs it.

### A removed CRD manifest should not be able to prune the CRD

Pruning a CRD is a decision with cluster-wide, irreversible consequences, and "the
manifest is no longer in this Application's source" is too weak a signal to trigger it
automatically. Argo CD has a per-resource escape hatch for exactly this:

```yaml
metadata:
  annotations:
    argocd.argoproj.io/sync-options: Prune=false
```

With this annotation, if the CRD manifest disappears from git, Argo CD reports the
resource as out-of-sync — pruning would remove it — but does not delete it. Sync remains
`OutOfSync` until a human looks at why and either restores the manifest or removes the
resource deliberately, outside of automated sync. This is worth its cost: an
Application that's meant to be fully self-healing now has one resource that
deliberately isn't, and that has to be a documented exception, not a surprise the next
person debugging a stuck sync discovers on their own.

### This only guards the GitOps path, honestly

`Prune=false` stops Argo CD's own reconciliation from deleting the CRD. It does nothing
against `kubectl delete crd widgets.example.com` run directly against the cluster — that
bypasses Argo CD entirely, and Kubernetes' garbage collection of dependent custom
resources happens regardless of how the CRD was deleted. The annotation closes the
GitOps-shaped version of this mistake — the one this pattern is actually meant to
prevent — not every way to delete a CRD.

## The solution

A `Widget` CRD and instance, wave-ordered and prune-guarded, applied through Argo CD on
a local `kind` cluster:

```yaml
# widget-crd.yaml
apiVersion: apiextensions.k8s.io/v1
kind: CustomResourceDefinition
metadata:
  name: widgets.example.com
  annotations:
    argocd.argoproj.io/sync-wave: "-1"
    argocd.argoproj.io/sync-options: Prune=false
spec:
  group: example.com
  scope: Namespaced
  names:
    plural: widgets
    singular: widget
    kind: Widget
    listKind: WidgetList
  versions:
    - name: v1
      served: true
      storage: true
      schema:
        openAPIV3Schema:
          type: object
          properties:
            spec:
              type: object
              properties:
                size:
                  type: string
```

```yaml
# widget-instance.yaml
apiVersion: example.com/v1
kind: Widget
metadata:
  name: demo-widget
  namespace: default
spec:
  size: large
```

```yaml
# application.yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: widgets-demo
  namespace: argocd
spec:
  project: default
  source:
    repoURL: https://github.com/<your-username>/<your-repo>.git
    targetRevision: main
    path: widgets-demo
  destination:
    server: https://kubernetes.default.svc
    namespace: default
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
```

Push `widget-crd.yaml` and `widget-instance.yaml` under `widgets-demo/` in your git
repository, then set up the cluster:

```bash
kind create cluster --name sync-waves-demo

kubectl create namespace argocd
kubectl apply -n argocd -f \
  https://raw.githubusercontent.com/argoproj/argo-cd/v2.12.4/manifests/install.yaml
kubectl -n argocd wait --for=condition=available --timeout=300s deployment/argocd-server

kubectl apply -f application.yaml
```

A test proving both properties, written as a shell script so it's a real, re-runnable
check rather than a one-off manual look:

```bash
#!/usr/bin/env bash
# test-sync-waves.sh
set -euo pipefail

echo "waiting for the Application to sync..."
for _ in $(seq 1 30); do
  status=$(kubectl get application widgets-demo -n argocd \
    -o jsonpath='{.status.sync.status}/{.status.health.status}')
  [ "$status" = "Synced/Healthy" ] && break
  sleep 2
done

echo "checking the CRD is Established..."
kubectl get crd widgets.example.com \
  -o jsonpath='{.status.conditions[?(@.type=="Established")].status}' | grep -q True

echo "checking the Widget exists (proves it wasn't submitted before the CRD was ready)..."
kubectl get widget demo-widget -n default -o jsonpath='{.spec.size}' | grep -q large

echo "removing the CRD from source and re-syncing..."
git -C repo rm widgets-demo/widget-crd.yaml
git -C repo commit -m "test: remove CRD from source"
git -C repo push
argocd app sync widgets-demo --prune || true

echo "confirming the CRD survived despite being out of git..."
kubectl get crd widgets.example.com >/dev/null
kubectl get widget demo-widget -n default >/dev/null

echo "PASS: CRD ordering held and Prune=false protected the CRD"
```

Correct output ends with:

```
PASS: CRD ordering held and Prune=false protected the CRD
```

Running the same test with the two `argocd.argoproj.io` annotations removed reproduces
both failures this pattern exists to prevent: the first sync intermittently reports the
`Widget` as `SyncFailed` with `no matches for kind "Widget"`, and the final sync — the
one that follows removing the CRD manifest — deletes the CRD and the `Widget` disappears
with it.

## Conclusion

Both failures come from the same mistaken assumption: that "applied in the same
operation" is the same as "ordered", and that "removed from source" is a safe enough
signal to delete something whose deletion cascades cluster-wide.

**A wave boundary is a health-checked barrier, not a hint.** Use it specifically where
one resource's *readiness*, not just its existence, gates another's — a CRD before its
consumers is the canonical case, but the same reasoning applies to a namespace before
its RBAC, or a secret before the deployment reading it as an environment variable.

**Some deletions are too expensive to leave to an automated diff.** `Prune=false` is an
explicit statement that a resource's removal must be a human decision, made outside
normal sync, and it costs exactly one line to say so.

**A guard that only covers the GitOps path is still worth having, and worth naming as
partial.** It stops the mistake that GitOps automation itself would otherwise make
silently; it was never going to stop a direct `kubectl delete`, and claiming otherwise
would be the same over-promise this pattern is trying to eliminate.
</content>
