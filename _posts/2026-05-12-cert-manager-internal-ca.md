---
layout: post
title: "cert-manager for Mesh-Only Names That No ACME Challenge Can Reach"
subtitle: "Why a private CA, not ACME, is the right Issuer for names nothing public can see."
date: 2026-05-12 09:00:00 +0200
tags: [kubernetes, tls]
description: >-
  ACME's HTTP-01 and DNS-01 challenges both assume the certificate authority
  can reach or resolve something public, which is exactly what a mesh-only
  hostname does not have. This article shows why a cert-manager CA Issuer is
  the correct tool for that case, what it costs in trust distribution, and a
  complete kind-based example that issues and verifies a certificate for a
  private name.
---

## The problem

cert-manager's usual advice is an ACME `ClusterIssuer` pointed at Let's Encrypt, and for
anything with a public DNS name it works without much thought. Point it at a name that
only resolves inside a private network and it does not work at all, no matter how many
times the request is retried.

A `Certificate` for a mesh-only name, requested through an ACME Issuer using HTTP-01,
looks reasonable to write:

```yaml
# Broken for a private name. Do not copy this.
apiVersion: cert-manager.io/v1
kind: Certificate
metadata:
  name: myapp-mesh-tls
  namespace: default
spec:
  secretName: myapp-mesh-tls
  dnsNames:
    - myapp.mesh.internal
  issuerRef:
    name: letsencrypt-prod
    kind: ClusterIssuer
```

cert-manager creates a `CertificateRequest`, then an `Order`, then a `Challenge`, and the
Challenge never clears:

```
$ kubectl describe challenge myapp-mesh-tls-<hash>
...
Status:
  Reason:  Waiting for HTTP-01 challenge propagation: failed to perform self check GET
           request 'http://myapp.mesh.internal/.well-known/acme-challenge/<token>':
           dial tcp: lookup myapp.mesh.internal: no such host
  State:   pending
```

Switching to DNS-01 does not help either. It fails one step earlier: cert-manager can
happily create the `_acme-challenge.myapp.mesh.internal` TXT record if the DNS provider
API lets it, but Let's Encrypt's own resolvers query public DNS to check it, and a
mesh-only zone was never delegated there. Either way the Challenge sits in `pending` or
cycles into `errored` forever.

This is not a misconfiguration to fix by retrying, or by picking the other challenge
type. Both ACME challenge types exist to prove one thing: that the requester controls a
publicly reachable resource — a web server the CA can dial into on port 80, or a DNS
zone the CA can query over the public internet. A name that resolves only inside a
WireGuard mesh, a cluster's internal DNS, or any host with no public record at all
satisfies neither precondition. There is nothing public for Let's Encrypt to reach or
look up, so no amount of waiting produces a different result.

It is easy to reach for ACME anyway, because it is the first thing every cert-manager
tutorial demonstrates, and it is genuinely the right answer for every public-facing
service in the same cluster. The gap only shows up the first time someone tries to put
TLS on something that was never meant to be public in the first place.

## Working through it

### ACME proves reachability; a CA Issuer proves key possession

cert-manager supports more Issuer types than ACME. One of them, `CA`, signs certificates
using a private key and certificate that cert-manager holds directly, stored as an
ordinary Kubernetes Secret. There is no challenge, because there is nothing to prove
about public reachability — issuance is just "sign this CSR with the key in this
Secret". That is satisfiable for a name nobody outside the network can even resolve,
because resolvability was never part of the check.

This is the correct tool here, not a workaround for ACME's limits. The `CA` Issuer type
is a first-class, documented part of cert-manager, and it exists specifically for
private PKI: internal service names, mesh addresses, anything where the CA and the
things that trust it are both under your control.

### The Certificate resource does not know or care which Issuer backs it

The `Certificate` object, its reconciliation loop, and its automatic renewal ahead of
expiry are identical regardless of Issuer type. Only `issuerRef` changes. A cluster
routinely has some `Certificate` resources pointed at a public `ClusterIssuer` backed by
Let's Encrypt, and others pointed at an internal `ClusterIssuer` backed by a private CA,
side by side, each renewing on its own schedule. Mixing the two is the normal shape of a
cluster with both public and private surface, not a special case that needs
justification.

### The cost ACME does not have: distributing trust

A private CA is trusted by precisely nothing until someone tells it to be. No operating
system trust store, no browser, no other service's TLS client ships your CA's
certificate. Every consumer of a certificate this CA issues — another service's HTTP
client, a sidecar, a person opening the endpoint in a browser — needs your CA's public
certificate added to its trust bundle out of band before the connection stops failing
with an unknown-authority error.

This is the trade, not a defect: ACME's cost is proving you are publicly reachable;
a private CA's cost is distributing trust yourself. cert-manager removes neither cost,
it only automates the mechanics of issuance and renewal once you have picked which one
you are willing to pay.

### trust-manager exists for exactly this distribution problem

cert-manager's companion project, `trust-manager`, takes a CA's public certificate and
projects it into a `ConfigMap` in every namespace that needs it, kept in sync as the CA
certificate changes. It is worth knowing about the moment more than a couple of
namespaces need to trust the same private CA, so that "copy this cert around by hand"
does not become its own maintenance burden. This article's example stops at the CA
Issuer itself; treat trust-manager as the next step once distribution stops being a
one-off.

### Nobody rotates the CA for you

With Let's Encrypt, the CA enforces a 90-day certificate lifetime, so short-lived certs
and their renewal are the default whether you think about it or not. A private CA has no
equivalent external enforcement. The root key you generate is valid for as long as you
say it is, and if it expires, or is compromised, or simply needs rotating, that is
entirely the operator's plan to write and run. cert-manager will renew leaf certificates
signed by it on schedule, but the CA's own lifecycle is yours alone.

## The solution

A complete example on a local `kind` cluster: install cert-manager, create a lab root
CA, wire it into a `CA` Issuer, and issue a certificate for a placeholder mesh-only name.

```bash
# Install cert-manager, pinned.
kubectl apply -f https://github.com/cert-manager/cert-manager/releases/download/v1.14.5/cert-manager.yaml

# Wait for it to be ready before creating any Issuer or Certificate.
kubectl wait --for=condition=Available --timeout=120s \
  deployment -n cert-manager -l app.kubernetes.io/instance=cert-manager
```

Generate a root CA for the lab. This is a throwaway key for a local cluster, not
guidance for handling a production root key:

```bash
openssl req -x509 -newkey rsa:4096 -sha256 -nodes \
  -days 3650 \
  -keyout ca.key -out ca.crt \
  -subj "/CN=philipptheserver-lab-root-ca"
```

Store the key pair as a Secret cert-manager can use:

```bash
kubectl create secret tls mesh-root-ca-secret \
  --cert=ca.crt --key=ca.key \
  --namespace=cert-manager
```

Point a `ClusterIssuer` of type `CA` at it:

```yaml
# ca-cluster-issuer.yaml
apiVersion: cert-manager.io/v1
kind: ClusterIssuer
metadata:
  name: mesh-ca-issuer
spec:
  ca:
    secretName: mesh-root-ca-secret
```

```bash
kubectl apply -f ca-cluster-issuer.yaml
```

Request a certificate for a mesh-only name through it:

```yaml
# myapp-mesh-tls.yaml
apiVersion: cert-manager.io/v1
kind: Certificate
metadata:
  name: myapp-mesh-tls
  namespace: default
spec:
  secretName: myapp-mesh-tls
  dnsNames:
    - myapp.mesh.internal
  duration: 2160h    # 90 days
  renewBefore: 360h  # 15 days
  issuerRef:
    name: mesh-ca-issuer
    kind: ClusterIssuer
```

```bash
kubectl apply -f myapp-mesh-tls.yaml
kubectl wait --for=condition=Ready --timeout=60s certificate/myapp-mesh-tls
```

Verify what got issued:

```bash
kubectl get secret myapp-mesh-tls -o jsonpath='{.data.tls\.crt}' \
  | base64 -d | openssl x509 -noout -text | grep -E 'Issuer:|Subject:|DNS:'
```

Correct output shows the issuer matching the lab CA's subject and the SAN carrying the
requested name, with no CA contact and no challenge involved at any point:

```
        Issuer: CN=philipptheserver-lab-root-ca
        Subject:
                DNS:myapp.mesh.internal
```

The `Subject:` line is empty, not a typo in this example: cert-manager only fills in a
certificate's Subject Common Name when the `Certificate` resource explicitly sets
`spec.commonName`. Leaving it out, as above, produces a certificate that carries the
requested name solely as a SAN entry — which is what every modern TLS client actually
checks against; the CN has been ignored by browsers for hostname matching for years. Add
`commonName: myapp.mesh.internal` to the spec if something you don't control still reads
the Subject field.

That certificate is real and correctly signed, but nothing trusts it yet. Making the
trust-distribution cost concrete: extract the CA's public certificate and add it to a
client's trust bundle before the client will accept a connection secured with it. Pull
it from the issued certificate's own Secret, not from `mesh-root-ca-secret` directly —
cert-manager automatically writes a `ca.crt` entry into every Secret it populates from a
`Certificate`, pointing at the issuing CA, which is the normal way to hand a workload's
peers what they need to trust it without also handing out the CA Issuer's own Secret
from the `cert-manager` namespace:

```bash
kubectl get secret myapp-mesh-tls \
  -o jsonpath='{.data.ca\.crt}' | base64 -d > mesh-root-ca.crt

# On a Debian/Ubuntu client that needs to trust it:
sudo cp mesh-root-ca.crt /usr/local/share/ca-certificates/mesh-root-ca.crt
sudo update-ca-certificates
```

Until a given client has done exactly this, a TLS handshake against `myapp.mesh.internal`
fails with an unknown-authority error regardless of how correctly the certificate was
issued.

## Conclusion

**ACME's two challenge types both assume the internet can see you.** HTTP-01 needs the
CA to reach your web server; DNS-01 needs the CA to query a public zone. A mesh-only name
satisfies neither, and no retry count changes that — it is the wrong Issuer, not a
misconfigured one.

**A private CA inverts the assumption and pays for it in trust distribution instead.**
Issuance stops requiring public reachability, but every consumer now needs your CA's
certificate added to its trust store by hand, or via something like trust-manager, before
connections succeed.

**cert-manager's renewal machinery does not distinguish between the two.** A `Certificate`
resource reconciles and renews the same way whichever Issuer backs it, which is why
public ACME-issued certificates and private CA-issued certificates coexisting in one
cluster is the ordinary case, not an exception worth special-casing.

**Nobody rotates a private CA for you.** Let's Encrypt's short lifetimes are enforced on
your behalf; a self-issued root's expiry and rotation are a plan you have to own,
starting from the day you generate it.
