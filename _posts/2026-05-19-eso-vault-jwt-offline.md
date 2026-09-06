---
layout: post
title: "External Secrets with Offline JWT Validation When Vault Cannot Reach the Cluster"
subtitle: "Verifying a service-account token's signature instead of asking the API server about it."
date: 2026-05-19 09:00:00 +0200
tags: [kubernetes, vault, secrets-management, security]
description: >-
  Vault's default Kubernetes auth method needs to call back into the cluster's
  API server on every login, which cannot work once Vault and the cluster sit
  in deliberately isolated networks. Here is how to configure Vault's JWT auth
  method to validate service-account tokens offline, against a static public
  key, and wire External Secrets Operator to use it.
---

## The problem

Vault's `kubernetes` auth method is the obvious choice for letting workloads authenticate
with a service-account token instead of a static credential. Configure it once:

```bash
vault auth enable kubernetes

vault write auth/kubernetes/config \
  kubernetes_host="https://10.0.0.0:6443" \
  kubernetes_ca_cert=@ca.crt
```

and it works, in most environments, without further thought. That is precisely what makes
the failure mode hard to see coming: this method does not just check a token's signature
against something it already holds. On every login it calls back into the Kubernetes API
server's TokenReview endpoint, over the network, to ask "is this token valid, and who does
it belong to". Vault is not verifying the token by itself; it is delegating the question to
the cluster.

That works fine as long as Vault has a route to `kubernetes_host`. It stops working the
moment someone puts Vault and the cluster into networks that cannot reach each other by
design — a segmented topology where the secrets manager and the workload cluster are
deliberately kept apart, so that compromising one does not hand an attacker a path to the
other. That is a reasonable, often recommended, security posture. It is also exactly the
posture that breaks `kubernetes` auth, because the method has a hard dependency that has
nothing to do with configuration and everything to do with network topology.

The failure does not announce itself as an incompatibility. It shows up as a login that
times out or refuses to connect, which looks like a Vault problem, or a certificate
problem, or a firewall rule someone forgot:

```text
Error writing data to auth/kubernetes/login: Error making API request.
Url: PUT https://vault.example.internal/v1/auth/kubernetes/login
Code: 500. Errors:
* failed to validate token: Post "https://10.0.0.0:6443/apis/authentication.k8s.io/v1/tokenreviews": dial tcp 10.0.0.0:6443: i/o timeout
```

Nothing in that message says "this auth method cannot work here". It says "connection
timed out", and a connection timeout sends most people towards routing tables and security
groups, not towards the realisation that the auth method itself is the wrong shape for the
topology. The environments where this is discovered are usually the ones where isolating
Vault from the cluster was a deliberate, correct decision — so the person who did the
secure thing is the one who gets the confusing failure.

## Working through it

### Why one method calls home and the other does not

A Kubernetes service-account token is a JWT. Kubernetes signs it with a private key the
API server holds, and the corresponding public key is all that is needed to check that a
given token was genuinely issued by that cluster — no live conversation with the API
server required, only arithmetic against a key you already have.

Vault's `kubernetes` auth method does not use that fact. It treats the token as an opaque
credential and asks the cluster to vouch for it via TokenReview, on every single login.
That gives you one advantage — Kubernetes can revoke a token centrally and Vault finds out
immediately — at the cost of a hard network dependency that never goes away.

Vault's `jwt` auth method takes the other path: it verifies the signature itself. Point it
at the same cluster's signing key once, and every subsequent login is pure local
cryptography. No callback, no dependency on Vault being able to reach anything at
login time.

### Why `jwt_validation_pubkeys` and not `jwks_url`

The `jwt` auth method supports two ways of supplying the signing key material. `jwks_url`
tells Vault to fetch a JWKS document over HTTP and refresh it periodically. That still
requires network reachability to the cluster — just less frequently than per-login. For an
isolated topology this does not solve the problem, it only shrinks how often it bites, and
a cluster's own OIDC discovery endpoint is usually served by the API server itself, so
pointing `jwks_url` at it reintroduces the exact dependency you are trying to remove.

`jwt_validation_pubkeys` is different in kind, not degree: you hand Vault one or more
static PEM public keys, once, at configuration time. After that, Vault never needs a route
to the cluster again, for configuration or for login. This is what makes validation
genuinely offline rather than just infrequent.

### Where the public key comes from

On a cluster built with `kubeadm` — which is what `kind` does under the hood — the
service-account signing key pair lives as plain files on the control-plane node:
`/etc/kubernetes/pki/sa.key` (private, stays on the node) and `/etc/kubernetes/pki/sa.pub`
(public, already PEM-encoded, safe to copy out). That file is exactly the input
`jwt_validation_pubkeys` wants, with no format conversion.

This generalises to any kubeadm-based cluster, including most self-managed on-premises
installs. It does not generalise to every cluster: managed offerings on the large public
clouds typically expose the equivalent key material through a public OIDC discovery
endpoint returning JSON Web Key Sets rather than a bare PEM file on a node's disk, and
using it with `jwt_validation_pubkeys` means fetching that document once, out of band, and
converting the JWK to PEM before handing it to Vault. The mechanism is the same; the file
you start from is not.

### Why the audience binding is not optional

A token that only proves "this identity belongs to the cluster" is dangerous to accept
anywhere else it might be presented. Kubernetes lets a token be minted bound to a specific
audience — a claim saying which consumer it is valid for — and Vault's `jwt` role can
require a matching `bound_audiences`. Skip this and a token minted for some unrelated
purpose, if it also carries the right subject, would authenticate to Vault too. Binding the
audience is what keeps offline verification safe: the signature alone proves the token is
genuine, the audience proves it was actually meant for this login.

## The solution

Everything below runs on a laptop with Docker, `kind`, `kubectl`, and the `vault` CLI
installed. No cloud account and no access to any private cluster is needed.

### Create the cluster and extract the signing key

```bash
kind create cluster --name jwt-lab --image kindest/node:v1.29.2

docker exec jwt-lab-control-plane cat /etc/kubernetes/pki/sa.pub > sa.pub
```

### Run Vault in dev mode

```bash
docker run -d --name vault-dev \
  --cap-add=IPC_LOCK \
  -p 8200:8200 \
  -e VAULT_DEV_ROOT_TOKEN_ID=root-token-for-lab-only \
  -e VAULT_DEV_LISTEN_ADDRESS=0.0.0.0:8200 \
  hashicorp/vault:1.15.6

export VAULT_ADDR=http://127.0.0.1:8200
export VAULT_TOKEN=root-token-for-lab-only
```

Dev mode is unsealed and stores nothing to disk — it is here to make the auth mechanism
reproducible, not to model how you would run Vault in production.

### Configure the JWT auth method

```bash
vault auth enable jwt

vault write auth/jwt/config \
  jwt_validation_pubkeys=@sa.pub
```

```hcl
# myapp-policy.hcl
path "secret/data/myapp/*" {
  capabilities = ["read"]
}
```

```bash
vault policy write myapp-policy myapp-policy.hcl

vault write auth/jwt/role/myapp-role \
  role_type="jwt" \
  bound_audiences="vault-internal" \
  user_claim="sub" \
  bound_subject="system:serviceaccount:default:myapp" \
  policies="myapp-policy" \
  ttl="15m"

vault secrets enable -path=secret kv-v2

vault kv put secret/myapp/config api_key="example-not-a-real-secret"
```

### Prove it works offline, before ESO enters the picture

```bash
kubectl create serviceaccount myapp

TOKEN=$(kubectl create token myapp --audience=vault-internal --duration=10m)

vault write auth/jwt/login role=myapp-role jwt="$TOKEN"
```

This succeeds and returns a Vault token scoped to `myapp-policy` — and it does so despite
Vault, running here as a plain Docker container with no route into the `kind` network at
all, never once contacting the Kubernetes API server. The verification is entirely local
signature checking against `sa.pub`, plus the audience and subject checks on the role. That
is the same thing that happens when Vault genuinely cannot reach the cluster, which is the
whole point.

### Wire up External Secrets Operator

```bash
helm repo add external-secrets https://charts.external-secrets.io
helm install external-secrets external-secrets/external-secrets \
  --namespace external-secrets --create-namespace \
  --version 0.10.4
```

```yaml
# service-account.yaml
apiVersion: v1
kind: ServiceAccount
metadata:
  name: myapp
  namespace: default
```

```yaml
# secret-store.yaml
apiVersion: external-secrets.io/v1beta1
kind: SecretStore
metadata:
  name: vault-jwt
  namespace: default
spec:
  provider:
    vault:
      server: "http://host.docker.internal:8200"
      path: secret
      version: v2
      auth:
        jwt:
          kubernetesServiceAccountToken:
            serviceAccountRef:
              name: myapp
            audiences:
              - vault-internal
          role: myapp-role
          path: jwt
```

```yaml
# external-secret.yaml
apiVersion: external-secrets.io/v1beta1
kind: ExternalSecret
metadata:
  name: myapp-config
  namespace: default
spec:
  refreshInterval: 1h
  secretStoreRef:
    name: vault-jwt
    kind: SecretStore
  target:
    name: myapp-config
  data:
    - secretKey: api_key
      remoteRef:
        key: myapp/config
        property: api_key
```

`server` above uses `host.docker.internal` so a pod inside `kind` can reach the Vault
container on the Docker host; in a real isolated topology this would instead be whatever
address Vault is reachable at from the cluster's egress path, if any exists at all — the
technique works even if that address only accepts the JWT login and nothing else.

```bash
kubectl apply -f service-account.yaml -f secret-store.yaml -f external-secret.yaml

kubectl get externalsecret myapp-config
# NAME           STORE       REFRESH INTERVAL   STATUS         READY
# myapp-config   vault-jwt   1h                 SecretSynced   True

kubectl get secret myapp-config -o jsonpath='{.data.api_key}' | base64 -d
# example-not-a-real-secret
```

`SecretSynced` and the decoded value confirm ESO minted an audience-bound token for the
`myapp` service account, Vault verified it purely against `sa.pub`, and the secret landed
in the cluster — with no network path from Vault back to the API server used or required
anywhere in that chain.

## Conclusion

**Token-based authentication does not have to mean callback-based authentication.** A JWT
carries everything needed to prove its own authenticity in its signature; asking the
issuer to re-confirm it on every use is a design choice, not a requirement, and Vault
offers both options for exactly this reason.

**A mechanism that defers a network dependency is not the same as one that removes it.**
`jwks_url` looks like an offline option until you notice it still needs a route to fetch
the key material, on a schedule. The distinction only matters once you have actually
isolated the two systems, which is precisely when it matters most.

**Audience binding is what makes offline validation safe rather than merely convenient.**
Signature verification proves a token is genuine; it says nothing about what the token was
for. Binding the login to a specific audience is the check that stops a token minted for
one purpose from being valid everywhere.

**The pattern outlives Vault.** Any system that needs to trust a Kubernetes service-account
token but cannot itself reach the API server — a CI runner in another network, a partner
service consuming workload identity, a policy engine evaluating tokens at the edge — can
use the same static-public-key verification instead of assuming a TokenReview call is
always available.
