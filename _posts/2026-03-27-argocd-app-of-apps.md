---
layout: post
title: "Argo CD App-of-Apps: Bootstrapping a Bare Cluster from One Root Application"
subtitle: "Applying a single Application manifest is the whole bootstrap, and the only manual step."
date: 2026-03-27 09:00:00 +0200
tags: [kubernetes, gitops, automation]
description: >-
  A fresh Kubernetes cluster has nothing on it, and a runbook of kubectl
  apply commands run in the right order does not survive being handed to
  someone else or run against a rebuilt cluster months later. This works
  through the app-of-apps pattern, where one root Argo CD Application
  manages every other Application, and gives a complete, reproducible setup
  on a local kind cluster.
---

## The problem

A cluster fresh off `kind create cluster` or a managed provider's "create" button has
nothing on it: no ingress controller, no cert manager, no workloads. Getting it into a
useful state usually starts as a script:

```bash
# Broken. Do not copy this.
kubectl create namespace ingress-nginx
kubectl apply -f ingress-nginx-install.yaml
kubectl create namespace monitoring
kubectl apply -f prometheus-crds.yaml
kubectl apply -f prometheus-operator.yaml
kubectl apply -f guestbook.yaml
```

This works the day it's written, on the machine it's written on, by the person who
remembers the order matters — CRDs before the resources that use them, the ingress
controller before anything that requests an `Ingress`. It stops working the moment any of
that changes: a new engineer runs it in a different order and half of it fails; a
disaster-recovery drill rebuilds the cluster and the script has quietly drifted from
what's actually installed on the cluster it was copied from; someone patches a Deployment
by hand to fix an incident and the script, next time it runs, doesn't know that patch
exists and doesn't undo it either. None of this is a kubectl problem — it's that a
sequence of imperative commands is not a record of desired state, and nothing is
comparing the cluster to one.

## Working through it

### Desired state has to be something a controller can read back

Argo CD's `Application` custom resource is a declarative description of "this cluster
should have what's in this path of this git repository". Once state is a resource in the
API server rather than a step in a script, a controller can continuously diff the live
cluster against it, and re-applying the same manifest twice is a no-op rather than a
redo of side effects.

The problem this doesn't yet solve is that bootstrapping a whole cluster this way still
means applying one `Application` per component by hand — which is the same runbook
problem, just with kubectl replaced by an equally manual sequence of `kubectl apply -f
application.yaml`.

### One Application that manages other Applications

An `Application` resource's source doesn't have to be a Helm chart or a directory of
plain Deployments — it can be a directory of other `Application` manifests. Argo CD does
not distinguish between the two cases: it reconciles whatever resources are in the
target path, and an `Application` is just another Kubernetes resource. Point one root
Application at a directory that contains child Application manifests, and Argo CD
creates, updates, and prunes those children the same way it would a ConfigMap.

```yaml
spec:
  source:
    repoURL: https://github.com/<your-username>/<your-repo>.git
    targetRevision: main
    path: apps
    directory:
      recurse: true
```

### The bootstrap becomes exactly one manual step

With app-of-apps, standing up a cluster from nothing is: install Argo CD itself, then
apply one file — the root Application. Every other Application, and everything each of
those Applications manages, is created by Argo CD reconciling that one resource. A
cluster rebuilt from scratch six months later runs the same single command against the
same git state and ends up in the same place, because nothing about the process depended
on a person's memory of the right order.

### Ordering between children still needs to be expressed, not assumed

App-of-apps solves *what exists*, not *in what order it gets created*. Argo CD applies
sync waves — an annotation, `argocd.argoproj.io/sync-wave`, that groups resources (and
Applications) into ordered batches — for exactly the cases where one thing genuinely has
to exist before another, such as a CRD before a custom resource that uses it. That
mechanism deserves its own treatment; for a first bootstrap, keep child Applications
independent of each other wherever the underlying components genuinely are, and reach
for sync waves only where there's a real dependency.

### The root manages itself, so adding an app is a pull request

Because the root Application is watching a directory, adding a new component later means
adding a file to that directory and letting Argo CD's next sync pick it up — no new
manual `kubectl apply`, no privileged cluster access needed by whoever's adding the app.
That's the operational payoff: onboarding a new piece of infrastructure becomes a
reviewable diff instead of a runbook update someone has to remember to also run.

## The solution

A local, reproducible bootstrap on `kind`. It needs one small git repository you control
— GitHub, GitLab, or any HTTPS remote reachable from the cluster — holding two files;
everything those files point to is a public chart or the Argo CD project's own public
example repository, so nothing else needs to be created by hand.

Create the cluster and install Argo CD, pinned:

```bash
kind create cluster --name gitops-demo

kubectl create namespace argocd
kubectl apply -n argocd -f \
  https://raw.githubusercontent.com/argoproj/argo-cd/v2.12.4/manifests/install.yaml

kubectl -n argocd wait --for=condition=available --timeout=300s \
  deployment/argocd-server
```

In your own git repository, at `apps/ingress-nginx.yaml`:

```yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: ingress-nginx
  namespace: argocd
spec:
  project: default
  source:
    repoURL: https://kubernetes.github.io/ingress-nginx
    chart: ingress-nginx
    targetRevision: 4.11.2
    helm:
      values: |
        controller:
          service:
            type: ClusterIP
  destination:
    server: https://kubernetes.default.svc
    namespace: ingress-nginx
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
    syncOptions:
      - CreateNamespace=true
```

And at `apps/guestbook.yaml`:

```yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: guestbook
  namespace: argocd
spec:
  project: default
  source:
    repoURL: https://github.com/argoproj/argocd-example-apps.git
    targetRevision: master
    path: guestbook
  destination:
    server: https://kubernetes.default.svc
    namespace: guestbook
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
    syncOptions:
      - CreateNamespace=true
```

Commit and push those two files. Then, locally, the root Application — the one and only
manifest you apply by hand:

```yaml
# bootstrap-root.yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: root
  namespace: argocd
spec:
  project: default
  source:
    repoURL: https://github.com/<your-username>/<your-repo>.git
    targetRevision: main
    path: apps
    directory:
      recurse: true
  destination:
    server: https://kubernetes.default.svc
    namespace: argocd
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
    syncOptions:
      - CreateNamespace=true
```

```bash
kubectl apply -f bootstrap-root.yaml

kubectl get applications -n argocd
```

Correct output, once Argo CD has synced (typically under a minute):

```
NAME            SYNC STATUS   HEALTH STATUS
root            Synced        Healthy
ingress-nginx   Synced        Healthy
guestbook       Synced        Healthy
```

Three Applications exist and the two workloads are running, and the only command that
was ever run against this cluster's API server by hand was the `kubectl apply` for
`root`. Delete `apps/guestbook.yaml` from the git repository and push; within one sync
interval `kubectl get applications -n argocd` shows only `root` and `ingress-nginx`,
and `kubectl get all -n guestbook` shows nothing — pruning removed what the repository no
longer describes.

## Conclusion

The runbook script and the app-of-apps pattern do the same job on day one. They diverge
on day two hundred, when the cluster needs to be rebuilt, handed to someone else, or
audited for what's actually supposed to be running.

**A single entry point makes bootstrap a fact you can state, not a procedure you have to
remember.** "Apply this one Application" is reproducible in a way that "run these twelve
commands in this order" is not, regardless of how well the twelve commands are
documented.

**Treating an Application as an ordinary Kubernetes resource is what makes the pattern
recursive.** Nothing distinguishes a root Application from a leaf one; the same
reconciliation, pruning and self-healing rules apply at every level, so the pattern
doesn't need special-casing as the tree of apps grows.

**This is not a substitute for expressing genuine ordering dependencies.** App-of-apps
gets everything onto the cluster; it says nothing about *when*, relative to each other.
Where one component truly cannot exist before another — a CRD before its consumer, most
commonly — that has to be stated explicitly, which is its own mechanism and its own set
of failure modes.
</content>
