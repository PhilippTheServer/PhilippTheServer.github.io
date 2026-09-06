---
layout: post
title: "Pinning Image Tags to Git SHAs Because GitOps Diffs Manifests, Not Registries"
subtitle: "A floating tag never changes as text, so a GitOps controller never sees a reason to sync."
date: 2026-04-10 09:00:00 +0200
tags: [gitops, ci-cd, docker]
description: >-
  A Deployment manifest that references an image by a floating tag such as
  latest never changes as text, so a GitOps controller comparing git to the
  cluster sees no diff and triggers no rollout even after CI pushes a new
  image. This shows why, and how baking the git SHA into the tag at build
  time, with a reproducible local-registry demo proving both the failure
  and the fix.
---

## The problem

A Deployment referencing an image by a floating tag is a common starting point:

```yaml
# Broken. Do not copy this.
spec:
  containers:
    - name: demo-app
      image: localhost:5001/demo-app:latest
      imagePullPolicy: Always
```

CI builds on every commit, tags the result `latest`, and pushes it. The manifest in git
never mentions a specific build — it says `latest` today and it will say `latest` a year
from now, regardless of how many images have been pushed under that name in between.

A GitOps controller's core operation is a diff: it renders the manifests in git and
compares them, field by field, against what's live in the cluster. `image:
localhost:5001/demo-app:latest` in git and `image: localhost:5001/demo-app:latest` on
the running Pod are textually identical, every single time, no matter which actual image
digest that tag currently points at in the registry. The controller has nothing to act
on — as far as it can see, nothing changed, so nothing gets rolled out, even though CI
just pushed genuinely new code five seconds ago.

This doesn't mean the new image is never used. `imagePullPolicy: Always` re-pulls on
every container (re)start — but only on a restart, which a GitOps controller was never
asked to trigger. If a pod later restarts for an unrelated reason — a crash, a node
drain, a `kubectl delete pod` — *that* pod pulls whatever `latest` happens to point at in
that moment, which may by then be several commits ahead of what a sibling replica,
still running since before the last push, is serving. Two replicas of the same
Deployment can end up running different code, indefinitely, with nothing in `kubectl get
pods` distinguishing them and no event marking when or why.

## Working through it

### GitOps compares two documents, not two points in time

The mental model that leads to `:latest` is "the cluster should always run the newest
image" — a statement about time. What Argo CD, Flux, or even a scripted `kubectl diff`
actually compute is a comparison between two static documents: the manifest and the live
object. A tag is just a string in that document. For the diff to produce a change, the
string itself has to change.

### The tag needs to carry the thing that actually changed

The git SHA of the commit that produced a given image is unique, stable, and already
computed by CI for free. Using it as the tag makes the tag a function of the code:
different code, different SHA, different tag, different string in the manifest — which
is precisely the condition a diff-based controller needs to notice something happened.

```bash
SHA=$(git rev-parse --short HEAD)
docker build --build-arg VERSION="${SHA}" -t "localhost:5001/demo-app:${SHA}" .
docker push "localhost:5001/demo-app:${SHA}"
```

### CI has to write the new tag back into the manifest

Building and pushing a SHA-tagged image doesn't, by itself, change anything the cluster
or a GitOps controller looks at — the manifest still says whatever tag it said before.
The missing step is CI committing the updated tag into the file that's actually tracked:

```bash
sed -i "s#image: localhost:5001/demo-app:.*#image: localhost:5001/demo-app:${SHA}#" deployment.yaml
git add deployment.yaml
git commit -m "release: demo-app@${SHA}"
git push
```

This is the point at which a real diff exists. A tool such as Argo CD Image Updater can
automate this write-back; a plain CI step doing exactly the `sed` above is equally
valid and has no additional moving parts to operate.

### Every replica rolls together, deliberately, because the manifest changed

Once the tag in git changes, the Deployment's Pod template changes, which is what
actually triggers a `RollingUpdate` — new ReplicaSet, old Pods drained on the schedule
the `strategy` specifies. Every replica moves to the new SHA as part of one rollout
event that shows up in `kubectl rollout history`, rather than drifting to it one
unrelated restart at a time.

### Rollback becomes "go back to the previous manifest", which git already tracks

With a SHA-pinned tag, the previous version is not "whatever the registry happened to
serve under `latest` before" — it's a specific commit, still in git history, still
pullable from the registry under its own immutable tag. `git revert` on the manifest
commit, or an Argo CD rollback to the prior sync, restores an exact, known-good state.
`:latest` has no equivalent: once a new image is pushed under that name, the previous
one is only recoverable if someone happened to keep its digest written down somewhere.

## The solution

A minimal app, built and released against a local registry, to see the failure and the
fix on a laptop with no external dependencies:

```python
# demo-app/app.py
import os
from http.server import BaseHTTPRequestHandler, HTTPServer


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(os.environ.get("VERSION", "unknown").encode())


HTTPServer(("0.0.0.0", 8000), Handler).serve_forever()
```

```dockerfile
# demo-app/Dockerfile
FROM python:3.12-slim
ARG VERSION=dev
ENV VERSION=$VERSION
WORKDIR /app
COPY app.py .
CMD ["python", "app.py"]
```

```bash
# ci-release.sh
#!/usr/bin/env bash
set -euo pipefail

SHA=$(git rev-parse --short HEAD)
IMAGE="localhost:5001/demo-app:${SHA}"

docker build --build-arg VERSION="${SHA}" -t "${IMAGE}" ./demo-app
docker push "${IMAGE}"

sed -i.bak "s#image: localhost:5001/demo-app:.*#image: ${IMAGE}#" deployment.yaml
rm -f deployment.yaml.bak

git add deployment.yaml
git commit -m "release: demo-app@${SHA}"
echo "manifest now points at ${IMAGE}"
```

```yaml
# deployment.yaml — the pinned, correct version
apiVersion: apps/v1
kind: Deployment
metadata:
  name: demo-app
spec:
  replicas: 2
  selector:
    matchLabels:
      app: demo-app
  template:
    metadata:
      labels:
        app: demo-app
    spec:
      containers:
        - name: demo-app
          image: localhost:5001/demo-app:placeholder
          imagePullPolicy: IfNotPresent
          ports:
            - containerPort: 8000
```

Local registry connected to a `kind` cluster, following kind's own documented pattern:

```yaml
# kind-config.yaml
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
containerdConfigPatches:
  - |-
    [plugins."io.containerd.grpc.v1.cri".registry.mirrors."localhost:5001"]
      endpoint = ["http://kind-registry:5001"]
nodes:
  - role: control-plane
```

```bash
docker run -d --restart=always -p 5001:5000 --network bridge --name kind-registry registry:2
kind create cluster --name tag-pinning-demo --config kind-config.yaml
docker network connect kind kind-registry 2>/dev/null || true

git init -q && git add -A && git commit -qm "initial commit"
bash ci-release.sh   # builds and pushes demo-app:<sha1>, updates deployment.yaml

kubectl apply -f deployment.yaml
kubectl rollout status deployment/demo-app
```

Now reproduce the failure with a floating tag, to see exactly what a controller sees.
Build and push a second image under `:latest` without touching `deployment.yaml`'s
`image:` field at all:

```bash
docker build --build-arg VERSION=second-push \
  -t localhost:5001/demo-app:latest ./demo-app
docker push localhost:5001/demo-app:latest

kubectl diff -f deployment.yaml || true
```

Correct output — this is the failure, made visible:

```
(no output — kubectl diff reports no differences)
```

A brand-new image exists in the registry, and the exact command a GitOps controller
uses internally to decide whether to act reports nothing to do, because
`deployment.yaml` never referenced `:latest` in this scenario and its SHA-pinned value
is unchanged. Compare that with a real release:

```bash
sed -i 's/second-push/third-release/' demo-app/app.py   # any code change
git commit -am "third release"
bash ci-release.sh
kubectl diff -f deployment.yaml
```

```
~ spec.template.spec.containers[0].image:
  - localhost:5001/demo-app:<sha1>
  + localhost:5001/demo-app:<sha2>
```

That diff is the thing a GitOps controller reacts to. `kubectl apply -f deployment.yaml`
now rolls both replicas to the new SHA together, and `kubectl rollout history
deployment/demo-app` records it as one event with a specific previous state to return to.

## Conclusion

The manifest is the interface a GitOps controller acts through. A tag that never
changes as text is invisible to that interface, no matter how much the registry behind
it has moved on.

**A GitOps controller only knows what its diff shows it.** Any fact that matters to a
rollout — which build is running — has to be encoded as text that changes when that fact
changes; a mutable label is not that, by definition.

**A git SHA is a tag you get for free and that already means something.** It requires no
new infrastructure, ties every running image back to an exact commit, and turns rollback
into "go back to a manifest git already has", rather than a scramble to remember what
used to be at `:latest`.

**The honest cost is one extra CI step that writes to the GitOps repository.** That step
needs its own credentials, its own failure handling, and — if multiple pipelines write
to the same manifest — a way to avoid two releases racing to commit at once. It is a
small amount of plumbing to own, in exchange for a rollout that happens deliberately,
once, for every replica together, instead of by accident, one replica at a time.
</content>
