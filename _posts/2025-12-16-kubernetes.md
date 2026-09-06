---
layout: post
title: "Docker Compose or Kubernetes: What the Control Loop Actually Buys You"
subtitle: "It is not a better Docker Compose. It is a different bargain, and the price is real."
date: 2025-12-16 09:00:00 +0200
tags: [kubernetes, docker, architecture, gitops]
description: >-
  Kubernetes is usually sold as the next step up from Docker Compose, which
  makes it sound like a bigger version of what you already have. It is a
  trade: you hand over control of where things run in exchange for a system
  that keeps working when a machine dies. This works through what that trade
  actually costs, with a runnable example showing the control loop doing its
  job and a Compose stack that cannot do the same thing.
---

## The problem

I run both a Docker host and a Kubernetes cluster in parallel, and people find this
inconsistent. It is the most deliberate decision in the whole estate, because the two are
not the same tool at different scales — they answer a different question.

The framing that gets everyone into trouble is "Kubernetes is what you graduate to once
Compose stops being enough." That makes the choice sound like a size problem. It is not. A
Compose file describes containers on a host you can name. A Kubernetes manifest describes
a *desired state*, and a controller spends forever trying to make the cluster match it. If
a host dies under Compose, whatever it was running is gone until a human notices and acts.
If a node dies under Kubernetes, the controller notices first, and the human reads about it
later.

That is the entire value proposition, and it is easy to buy without understanding what it
costs: debugging changes shape completely. With Compose, something is wrong, you SSH in,
you read the logs, you see the process — one hop deep. With Kubernetes there is a
scheduler, a CNI plugin, a service abstraction, an ingress, and a set of health checks
between you and the process, any one of which can be the reason nothing responds. The
first few times, working out which layer is lying takes hours for a problem the Compose
equivalent would have taken minutes to see.

## Working through it

### The control loop is the whole feature

Everything Kubernetes gives you is a consequence of one mechanism: a controller
continuously compares the cluster's actual state to the state declared in a manifest, and
issues corrections. A Deployment does not start three pods once — it maintains three pods,
forever, against a world that keeps trying to knock that number down. Machine failure
stops being an incident and becomes a rescheduling event you read about after the fact.

Docker Compose has no such loop. `restart: always` restarts a container on the same host.
It has nothing to say about a host that no longer exists.

### GitOps makes the loop apply to deployment itself

If a controller also watches a git repository and reconciles the cluster toward it, the
deployment story becomes identical to the infrastructure story: the repository is the
truth, the cluster converges on it, and nobody runs a deploy script — they merge, and the
cluster notices. There is no "did the staging change reach production" question, because
the answer is whatever is in git. Rolling back is a revert, not a re-run of a script with
different arguments.

### What the loop costs you

The scheduler deciding where things run is only a good trade if you actually want that
decision made for you. Workloads that are not replicable — a database with local state, a
license tied to a MAC address, anything that cannot tolerate being moved — get nothing
from a control loop that keeps trying to move them. You can pin them to a node, and people
do, but at that point the cluster is an elaborate way to run a process on a specific host,
with all of the scheduling overhead and none of the benefit.

The debugging cost is real too, and it does not go away with experience — it just gets
faster. Budget for it rather than being surprised by it.

### The test that decides it

Ask, for a given workload: *if the host it is on died right now, do I want the system to
handle that, or do I want to be told?* Both are legitimate answers. A database wants to be
told. A stateless API replica wants to be handled. Forcing everything into one bucket is
how you end up with a database in a pod that gets rescheduled away from its disk, or a
disposable web frontend kept alive by someone's pager.

## The solution

Below is a complete, minimal way to see the control loop do its job, using `kind`
(Kubernetes-in-Docker) so it runs on a laptop with no cloud account. Alongside it is the
Compose equivalent, so the difference is something you watch happen rather than something
you take on faith.

```yaml
# kind-config.yaml
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
nodes:
  - role: control-plane
  - role: worker
```

```yaml
# deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: demo
spec:
  replicas: 3
  selector:
    matchLabels:
      app: demo
  template:
    metadata:
      labels:
        app: demo
    spec:
      containers:
        - name: demo
          image: nginx:1.27-alpine
          resources:
            requests:
              cpu: "50m"
              memory: "32Mi"
            limits:
              cpu: "200m"
              memory: "64Mi"
          readinessProbe:
            httpGet:
              path: /
              port: 80
            initialDelaySeconds: 2
```

```bash
kind create cluster --name demo --config kind-config.yaml
kubectl apply -f deployment.yaml
kubectl get pods -l app=demo
# demo-7f6d...   1/1   Running   0   10s   (x3)

# Simulate a failure by deleting a pod directly, as if its node had died
kubectl delete pod -l app=demo --field-selector status.phase=Running --wait=false | head -n1
kubectl get pods -l app=demo -w
# the deleted pod disappears and a replacement appears within seconds,
# with no one having been paged
```

The `readinessProbe` and `resources` block are not decoration. Without a resource request,
the scheduler is packing nodes by guesswork, and it will guess wrong under exactly the load
that made you care in the first place. Without a readiness probe, the control loop can call
a pod healthy before it can actually serve traffic.

Now the Compose side, doing the equivalent job on a single host:

```yaml
# docker-compose.yml
services:
  demo:
    image: nginx:1.27-alpine
    restart: always
    deploy:
      replicas: 3
```

```bash
docker compose up -d
docker compose ps
docker kill $(docker compose ps -q demo | head -n1)
docker compose ps
# the killed container restarts on the same host — restart: always did its job

# now remove the host entirely from the equation
docker compose down
docker compose ps
# nothing is running anywhere, and nothing will start it again until a human runs "up"
```

That last step is the actual comparison. `restart: always` handles a crashed process. It
has no answer for a host that is gone, because there is no second host in the picture for
it to move to. That gap is precisely what the Kubernetes control loop exists to close, and
precisely what you are paying the debugging-complexity cost for.

## Conclusion

**Ask what you want to happen when a machine dies before choosing the tool.** If the
honest answer is "someone will notice and fix it, and that is fine," that is a legitimate
answer, and a great deal of YAML has just been avoided.

**Resource requests are inputs to a decision, not paperwork.** They are how the scheduler
allocates the cluster. Leaving them unset does not opt you out of scheduling — it opts you
into scheduling by guesswork.

**A control loop only helps workloads that can tolerate being moved.** Anything that
cannot — pinned state, a license tied to hardware — gets the operational overhead of the
cluster and none of the resilience, which is the worst combination available.

**The learning curve is the actual price, not the YAML.** A cluster nobody on the team is
curious about becomes a cluster nobody upgrades, and an unpatched cluster is a worse
liability than the single host it replaced.
