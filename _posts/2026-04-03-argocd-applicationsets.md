---
layout: post
title: "ApplicationSets: Onboarding a Workload With a Directory and a Pull Request"
subtitle: "Generating one Application per service directory instead of hand-writing each one."
date: 2026-04-03 09:00:00 +0200
tags: [kubernetes, gitops, automation]
description: >-
  Hand-writing a new Argo CD Application object for every microservice is
  repetitive, and the step is easy to forget entirely when a new service is
  added. This shows how an ApplicationSet's git directory generator turns
  onboarding a workload into adding a directory and merging a pull request,
  with a complete, reproducible setup on a local kind cluster.
---

## The problem

App-of-apps solves how a cluster gets bootstrapped from one root Application, but it
doesn't solve how each individual Application gets written. In an organisation with a
dozen small services, each one typically needs its own near-identical Application
manifest:

```yaml
# apps/hello.yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: hello
  namespace: argocd
spec:
  project: default
  source:
    repoURL: https://github.com/<your-username>/<your-repo>.git
    targetRevision: main
    path: services/hello
  destination:
    server: https://kubernetes.default.svc
    namespace: hello
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
```

Adding a thirteenth service means copying this file, changing three strings, and
remembering to also commit it alongside the service's own manifests — a step that lives
in a different mental category from "write the Deployment", so it's the one that gets
forgotten. When it is forgotten, the failure is silent: the service's manifests sit in
git, correctly written, and nothing ever applies them, because nothing in the cluster
is watching that path yet. Nobody gets an error. The service is just missing, and the
person who added it finds out only when someone asks why it isn't running.

Copy-paste also drifts. Twelve files that started identical except for three fields
slowly diverge — one has an extra `syncOptions` entry another doesn't, one still points
at an old `targetRevision` — because each was hand-edited independently and nothing
keeps them consistent.

## Working through it

### Generate the repetitive part instead of writing it by hand

The twelve Application manifests differ from each other in exactly the same three
places every time: a name, a source path, a destination namespace. That's a strong
signal the manifest shouldn't be hand-written at all — it should be generated from
whatever already varies per service, which in a git-based layout is the directory
structure itself.

### The ApplicationSet controller turns a directory listing into Applications

`ApplicationSet` is a separate CRD, reconciled by a controller bundled into Argo CD's
standard install since 2.3. It takes a *generator* — a source of parameters — and a
*template* — an Application manifest with placeholders — and produces one Application
per set of parameters the generator returns. The git directory generator's parameters
are the paths matching a glob inside a repository:

```yaml
generators:
  - git:
      repoURL: https://github.com/<your-username>/<your-repo>.git
      revision: main
      directories:
        - path: services/*
```

Each directory under `services/` that exists in the repository becomes one parameter
set, exposing {% raw %}`{{path}}`{% endraw %} (the full path) and
{% raw %}`{{path.basename}}`{% endraw %} (the last segment) to the template.

### Adding a service becomes adding a directory

With the generator watching `services/*`, a service's own Deployment and Service
manifests, committed under `services/<name>/`, are sufficient on their own. Nobody writes
an Application for it — the ApplicationSet controller notices the new directory on its
next poll and creates one from the template. Onboarding a new service and registering it
with Argo CD become the same pull request instead of two.

### The polling interval is a real trade-off, not just a default

The git generator re-lists the repository on an interval — `requeueAfterSeconds`,
three minutes by default. That means a new directory doesn't produce an Application
instantly; there's a window, bounded by that setting, between the merge and the
Application appearing. Lowering it trades latency for load on the git host and the API
server; a webhook-driven refresh removes the wait entirely but adds a component (an
ingress and a shared secret between the git host and Argo CD) that a poll-based setup
doesn't need. For most service counts, a short poll interval is the simpler choice and
the wait is not one anyone notices.

### Templating failures are quieter than a hand-written manifest's

A malformed `kustomization.yaml` inside a generated Application's source path doesn't
fail the same way a bad hand-written Application does — the ApplicationSet still creates
the Application object, but that Application then fails to sync, and the failure is
visible on the generated Application, not on the ApplicationSet itself. Anyone debugging
"why isn't my service running" needs to know to check `kubectl get applications`, not
just `kubectl get applicationsets` — the generation step and the sync step fail
independently and are diagnosed in different places.

## The solution

A reproducible setup on `kind`, using the same one small git repository from bootstrapping
the cluster with app-of-apps — the pinned Argo CD install already includes the
ApplicationSet controller, so no separate installation step is needed.

```bash
kind create cluster --name applicationset-demo

kubectl create namespace argocd
kubectl apply -n argocd -f \
  https://raw.githubusercontent.com/argoproj/argo-cd/v2.12.4/manifests/install.yaml

kubectl -n argocd wait --for=condition=available --timeout=300s \
  deployment/argocd-applicationset-controller
```

In your git repository, two service directories to start with:

```yaml
# services/hello/kustomization.yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - deployment.yaml
  - service.yaml
```

```yaml
# services/hello/deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: hello
spec:
  replicas: 1
  selector:
    matchLabels:
      app: hello
  template:
    metadata:
      labels:
        app: hello
    spec:
      containers:
        - name: hello
          image: nginxdemos/hello:plain-text
          ports:
            - containerPort: 80
```

```yaml
# services/hello/service.yaml
apiVersion: v1
kind: Service
metadata:
  name: hello
spec:
  selector:
    app: hello
  ports:
    - port: 80
```

`services/world/` is an identical set of three files with every `hello` replaced by
`world`. And the ApplicationSet itself:

{% raw %}
```yaml
# applicationset.yaml
apiVersion: argoproj.io/v1alpha1
kind: ApplicationSet
metadata:
  name: services
  namespace: argocd
spec:
  goTemplate: true
  goTemplateOptions: ["missingkey=error"]
  generators:
    - git:
        repoURL: https://github.com/<your-username>/<your-repo>.git
        revision: main
        requeueAfterSeconds: 30
        directories:
          - path: services/*
  template:
    metadata:
      name: "{{.path.basename}}"
    spec:
      project: default
      source:
        repoURL: https://github.com/<your-username>/<your-repo>.git
        targetRevision: main
        path: "{{.path.path}}"
      destination:
        server: https://kubernetes.default.svc
        namespace: "{{.path.basename}}"
      syncPolicy:
        automated:
          prune: true
          selfHeal: true
        syncOptions:
          - CreateNamespace=true
```
{% endraw %}

Commit and push `services/hello/`, `services/world/`, and `applicationset.yaml`, then
apply the ApplicationSet:

```bash
kubectl apply -f applicationset.yaml

kubectl get applications -n argocd
```

Correct output, once the generator has run:

```
NAME    SYNC STATUS   HEALTH STATUS
hello   Synced        Healthy
world   Synced        Healthy
```

Now add a third service without touching the ApplicationSet at all — push
`services/again/` with the same three files, `again` substituted for `hello`:

```bash
git add services/again && git commit -m "add again service" && git push

sleep 35   # requeueAfterSeconds is 30 in this example
kubectl get applications -n argocd
```

```
NAME    SYNC STATUS   HEALTH STATUS
again   Synced        Healthy
hello   Synced        Healthy
world   Synced        Healthy
```

No Application manifest was written for `again`. The only thing committed was the
service's own files, in the same directory shape every other service already uses.

## Conclusion

The manual Application-per-service approach and ApplicationSets produce the same
running cluster. What differs is where the repetitive, error-prone step lives: in a
human's memory, or in a generator that runs the same way every time.

**Generate what varies predictably; hand-write what doesn't.** Twelve Applications
differing only in name, path and namespace are a template and a parameter list, not
twelve documents. Reach for ApplicationSets specifically where the variation is that
mechanical — a workload with genuinely bespoke sync behaviour still deserves its own
hand-written Application.

**The convention the generator relies on has to be enforced somewhere.** A directory
generator matching `services/*` only works because every service's manifests live at
that consistent path with that consistent shape; nothing stops someone from adding a
service directory that doesn't match the assumed layout, and when that happens the
Application it produces is broken in a way that's specific to whatever assumption broke.

**Two failure surfaces now exist where hand-writing had one.** Debugging a missing or
broken service means checking both the ApplicationSet (did a directory get picked up at
all) and the generated Application (did what got picked up actually sync) — worth
knowing before the first incident, not during it.
</content>
