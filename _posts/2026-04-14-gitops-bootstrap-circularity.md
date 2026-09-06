---
layout: post
title: "Breaking the Circular Dependency in GitOps Secret Delivery"
subtitle: "The controller that is supposed to deliver secrets cannot also deliver its own."
date: 2026-04-14 09:00:00 +0200
tags: [kubernetes, gitops, secrets-management]
description: >-
  A secrets operator deployed through GitOps is the natural way to get
  credentials onto a cluster without committing them to git, but that
  operator needs its own credential to reach the secret store, and nothing
  has delivered that one yet. This works through where the circularity
  actually breaks, and gives a complete, reproducible bootstrap using a
  Vault dev server and the External Secrets Operator on a local kind
  cluster.
---

## The problem

Once a cluster has a secrets operator — something that reads from an external secret
store and materialises a Kubernetes `Secret` from it — every application's credentials
can be kept out of git entirely: the manifest declares *which* secret it needs, and the
operator fetches the value at reconcile time. That's the appeal, and it's real. The
awkward part is how the operator itself authenticates to the secret store.

```yaml
# Broken. Do not copy this — where does this Secret come from?
apiVersion: external-secrets.io/v1beta1
kind: SecretStore
metadata:
  name: vault-backend
spec:
  provider:
    vault:
      server: "https://vault.example.internal:8200"
      auth:
        tokenSecretRef:
          name: vault-token          # <- this Secret has to already exist
          key: token
```

The `SecretStore` resource needs a Kubernetes `Secret` — a Vault token, in this case —
before it can fetch anything at all. If that credential is meant to arrive the same way
every other secret does, through the operator this resource configures, the resource is
waiting on the very system it's part of setting up. If instead it's meant to arrive
through GitOps like everything else, it's a plaintext credential in the git repository —
the exact thing the entire operator exists to avoid. Either path is circular, and the
usual instinct — "just commit a placeholder and swap it in manually after bootstrap" —
works exactly once, on the day someone remembers to do it by hand, and is invisible to
whoever rebuilds the cluster next and doesn't know that step exists.

## Working through it

### Not everything can arrive through the system it establishes

A circular dependency means some fact has to be true before the mechanism that would
normally make it true exists. That's not a bug to route around cleverly — it's the
literal boundary of what GitOps can automate. Something, somewhere, has to be
provisioned by a process outside the GitOps loop, exactly once, and treated afterwards
as an input to that loop rather than an output of it.

### Draw the boundary at "credential to reach the secret store", nowhere further in

The temptation is to solve this generally — "make bootstrapping fully hands-off" — which
usually ends up smuggling a real secret into a place it doesn't belong. The narrower,
honest boundary is: everything *except* the one credential the secrets operator uses to
authenticate to the store is GitOps-managed as normal. The operator's own deployment, its
`SecretStore` configuration, every `ExternalSecret` any application declares — all of
that is ordinary git-tracked YAML. Only the credential that lets the `SecretStore`
actually authenticate sits outside it.

### That one credential is provisioned out-of-band, deliberately, once

"Out-of-band" here means a step a human or a pipeline with cluster-admin access runs
directly against the cluster, not through the GitOps controller: `kubectl create secret`
against a namespace the operator will read from, run once per cluster, and never again
unless the credential rotates. This is not a workaround — it's the actual, correct shape
of the problem. A secret store credential is infrastructure bootstrap, in the same
category as the cloud API token that let you create the cluster in the first place; it
was never going to be one more thing GitOps delivers to itself.

### Namespace and secret shape have to be predictable before the operator exists

Because this step runs before the GitOps-managed resources that depend on it, the
namespace it targets and the name it gives the secret can't be discovered from the
GitOps repository — they have to be fixed, documented values that the `SecretStore`
manifest, written afterwards, references by the same fixed names. Getting this pairing
wrong — the bootstrap script creates the secret in one namespace, the `SecretStore`
looks in another — is the single most common way this setup silently fails, and it fails
quietly: the `SecretStore` simply reports it can't find its credential, which looks
identical to a dozen other configuration mistakes.

### Rotation goes through the same door it came in

Because the credential didn't arrive through GitOps, it doesn't rotate through GitOps
either. A runbook — or better, a scheduled job with the same narrow, direct access the
bootstrap step used — has to update that one `Secret` in place when the credential
changes. This is worth stating plainly as a limitation: the one piece of this system
that isn't self-healing through git is also the piece a compromised or expired
credential would affect first.

## The solution

A Vault dev server standing in for the secret store, and the External Secrets Operator
reading from it, on a local `kind` cluster. This reproduces the actual shape of the
circularity and its break: everything after the bootstrap step is ordinary, git-style
YAML; only the bootstrap step itself touches the cluster directly.

```bash
kind create cluster --name secrets-bootstrap-demo

docker run -d --name vault-dev --network kind \
  -p 8200:8200 \
  -e VAULT_DEV_ROOT_TOKEN_ID=dev-only-root-token \
  hashicorp/vault:1.17.6
```

Seed one secret into Vault, to prove the operator can fetch something real later:

```bash
docker exec -e VAULT_ADDR=http://127.0.0.1:8200 \
  -e VAULT_TOKEN=dev-only-root-token vault-dev \
  vault kv put secret/demo-app password=hunter2-placeholder
```

Install the External Secrets Operator, pinned:

```bash
helm repo add external-secrets https://charts.external-secrets.io
helm repo update
helm install external-secrets external-secrets/external-secrets \
  --version 0.10.4 \
  --namespace external-secrets --create-namespace \
  --set installCRDs=true

kubectl -n external-secrets wait --for=condition=available --timeout=300s \
  deployment/external-secrets
```

This is the out-of-band bootstrap step — the one command in this whole setup that is
not, and cannot be, delivered through GitOps, because the resource it creates is what
every GitOps-managed `SecretStore` afterwards depends on:

```bash
kubectl create namespace external-secrets --dry-run=client -o yaml | kubectl apply -f -
kubectl -n external-secrets create secret generic vault-token \
  --from-literal=token=dev-only-root-token
```

Everything from here on is a manifest a GitOps controller applies like any other:

```yaml
# secret-store.yaml
apiVersion: external-secrets.io/v1beta1
kind: SecretStore
metadata:
  name: vault-backend
  namespace: external-secrets
spec:
  provider:
    vault:
      server: "http://vault-dev.default.svc.cluster.local:8200"
      path: secret
      version: v2
      auth:
        tokenSecretRef:
          name: vault-token
          key: token
```

```yaml
# external-secret.yaml
apiVersion: external-secrets.io/v1beta1
kind: ExternalSecret
metadata:
  name: demo-app-credentials
  namespace: external-secrets
spec:
  refreshInterval: 1h
  secretStoreRef:
    name: vault-backend
    kind: SecretStore
  target:
    name: demo-app-credentials
  data:
    - secretKey: password
      remoteRef:
        key: demo-app
        property: password
```

```bash
kubectl apply -f secret-store.yaml
kubectl apply -f external-secret.yaml

kubectl -n external-secrets get secret demo-app-credentials \
  -o jsonpath='{.data.password}' | base64 -d
```

Correct output:

```
hunter2-placeholder
```

The value travelled from Vault into a Kubernetes `Secret` without ever being written to
the git repository holding `secret-store.yaml` and `external-secret.yaml` — those two
files contain no credential at all, only references. The one value that never went
through git is `vault-token`, created by the direct `kubectl create secret` command
above, run once, outside the loop it makes possible.

## Conclusion

The circularity was never a puzzle with a clever fix hiding in it — it's a real boundary,
and the only mistake available is refusing to admit where it is.

**Every automated system has at least one credential that has to be provisioned before
the automation exists.** Naming that credential explicitly, and being honest that it sits
outside GitOps, is safer than pretending the whole chain is self-bootstrapping when one
link in it quietly isn't.

**Narrow the manual step to the smallest thing that has to be manual.** Here, that's one
Vault token in one namespace — not the operator's deployment, not any application's
credentials, not the `SecretStore` configuration. Everything that *can* go through
GitOps should; the exception should be small enough to document in one paragraph.

**An out-of-band step needs an out-of-band rotation plan, or it becomes the thing nobody
remembers how to change.** A credential that bypassed GitOps to get in bypasses it again
every time it needs to be replaced — write that runbook at the same time as the bootstrap
step, not after the token has already expired in production.
</content>
