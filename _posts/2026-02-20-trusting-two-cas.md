---
layout: post
title: "Trusting an Internal and a Public Certificate Authority in One Process"
subtitle: "Adding trust for one CA by replacing the trust store breaks every public connection"
date: 2026-02-20 09:00:00 +0200
tags: [tls, security]
description: >-
  Pointing a process at an internal certificate authority by overriding its trust store
  is the fastest way to make it work, and it silently cuts that same process off from
  every publicly signed endpoint it also needs to reach, such as an external identity
  provider. This article shows the difference between replacing a trust store and
  appending to it, with a Dockerfile and a Python client that get it right.
---

## The problem

A service needs to trust an internally issued certificate — an internal reverse proxy,
an internal API, anything signed by a certificate authority you run yourself rather than
one in the public Mozilla/CA trust programme. The fastest way to make that work is to
point the process straight at the internal CA's certificate:

```bash
# Broken. Do not copy this.
export SSL_CERT_FILE=/etc/internal-ca/ca.pem
curl https://internal-service.internal:8443/
```

The internal call now succeeds. The same process's calls to anything with a publicly
signed certificate now fail:

```bash
curl https://example.com
# curl: (60) SSL certificate problem: unable to get local issuer certificate
```

`SSL_CERT_FILE` does not add a trusted issuer, it *replaces the entire trust store* for
whatever reads that variable. The process that used to trust every public CA now trusts
exactly one, and that one is not any of the public CAs. This is easy to miss because the
person making the change is testing the internal call, which now works — the regression
shows up somewhere else, later, in a call to an external identity provider, a package
registry, or a webhook target, made by a process nobody thought was affected because
nobody touched its code, only its environment.

It gets worse in mixed environments, because different tools read different variables
independently: `SSL_CERT_FILE` and `SSL_CERT_DIR` affect OpenSSL directly; Python's
`requests` uses its own bundled `certifi` package unless `REQUESTS_CA_BUNDLE` or
`CURL_CA_BUNDLE` is set; the JVM has its own `cacerts` keystore entirely. "Just set the
CA env var" can fix curl and quietly leave a Python sidecar in the same container still
trusting nothing but the system default — or the reverse.

## Working through it

### Distinguishing "replace" from "append"

The tools that manage a Linux distribution's trust store, `update-ca-certificates` on
Debian/Ubuntu or `update-ca-trust` on RHEL-family systems, do not replace anything. They
read every certificate under a designated local directory, combine it with the
distribution's shipped public CA bundle, and write out one merged file. That merged file
is what should end up as the trust root for everything in the process — the internal CA
becomes one more trusted issuer alongside the public ones, not a replacement for them.

`SSL_CERT_FILE` pointed at a single file has no concept of "combine". It trusts exactly
the certificates in that file. The fix is not a different environment variable; it is to
make the file it points to be the *merged* bundle, not the internal CA alone.

### Getting the OS layer right first

In a container image, add the internal CA to the standard location and let the
distribution's own tool do the merge:

```dockerfile
COPY internal-ca.pem /usr/local/share/ca-certificates/internal-ca.crt
RUN update-ca-certificates
```

After this, `/etc/ssl/certs/ca-certificates.crt` contains the public CAs *and* the
internal one. Anything that reads the system default — which is most C-linked tooling,
`curl` included, when no override is set — now trusts both chains with no environment
variable needed at all. The correct fix in the common case is to not set `SSL_CERT_FILE`
in the first place and let the OS-level merge do the work.

### Handling runtimes that ship their own bundle

Some runtimes deliberately do not use the OS trust store, `certifi` in the Python
ecosystem being the common one, precisely so that a Python install's TLS behaviour is
not at the mercy of whatever the host OS bundle happens to contain. That default is
sound, and it still needs the same merge treatment for the internal CA: concatenate the
runtime's bundle with the internal CA rather than replacing one with the other.

```bash
cat "$(python -c 'import certifi; print(certifi.where())')" internal-ca.pem > /etc/ssl/certs/combined-ca.pem
export REQUESTS_CA_BUNDLE=/etc/ssl/certs/combined-ca.pem
```

Now `requests` trusts both the public CAs it shipped with and the internal one, without
touching the OS trust store that `curl` and everything else in the container reads.

### Verifying both directions, not just the one you changed

The mistake that causes this whole class of bug is testing only the connection you were
trying to fix. Any change to trust configuration needs a test against a publicly signed
endpoint as well, every time, specifically because that is the connection nobody thinks
to check.

## The solution

A complete, runnable demonstration: a Dockerfile that generates an internal CA and an
internal HTTPS service inside the image, installs the CA the correct way, and proves
that both the internal service and a real public endpoint are reachable from the same
container.

```dockerfile
# Dockerfile
FROM debian:bookworm-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    openssl ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

# --- Simulate an internally issued CA and a service certificate signed by it ---
RUN mkdir -p /internal-ca && cd /internal-ca && \
    openssl req -x509 -newkey rsa:2048 -days 3650 -nodes \
      -keyout ca-key.pem -out ca.pem -subj "/CN=Example Internal CA" && \
    openssl req -newkey rsa:2048 -nodes \
      -keyout server-key.pem -out server.csr \
      -subj "/CN=internal-service.internal" && \
    openssl x509 -req -in server.csr -CA ca.pem -CAkey ca-key.pem \
      -CAcreateserial -days 825 -out server.pem \
      -extfile <(printf "subjectAltName=DNS:internal-service.internal")

# --- Install the internal CA correctly: append, do not replace ---
RUN cp /internal-ca/ca.pem /usr/local/share/ca-certificates/internal-ca.crt && \
    update-ca-certificates

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
```

```bash
#!/usr/bin/env bash
# entrypoint.sh — start a local HTTPS server on the internal CA's certificate,
# then prove both trust chains work from the same process.
set -euo pipefail

openssl s_server -quiet -www \
  -cert /internal-ca/server.pem -key /internal-ca/server-key.pem \
  -accept 8443 &
sleep 1

echo "--- internal service, signed by the internal CA ---"
curl -sS --resolve internal-service.internal:8443:127.0.0.1 \
  https://internal-service.internal:8443/ -o /dev/null -w "HTTP %{http_code}\n"

echo "--- public endpoint, signed by a public CA ---"
curl -sS https://example.com -o /dev/null -w "HTTP %{http_code}\n"

wait
```

```bash
docker build -t two-ca-demo .
docker run --rm two-ca-demo
```

```
--- internal service, signed by the internal CA ---
HTTP 200
--- public endpoint, signed by a public CA ---
HTTP 200
```

Delete the `RUN update-ca-certificates` line, or replace it with
`ENV SSL_CERT_FILE=/internal-ca/ca.pem`, rebuild, and rerun: the internal call still
returns `HTTP 200`, and the public one fails with a certificate verification error. That
is the regression this article is about, reproduced deliberately so you can see exactly
what breaks and why the fix above avoids it.

## Conclusion

**A trust store override is process-wide, not endpoint-specific.** `SSL_CERT_FILE` and
its equivalents do not mean "also trust this"; they mean "trust only this". Every
connection the process makes is affected, including ones you were not thinking about
when you set the variable.

**Prefer the OS-level merge over an application-level override whenever you can.**
`update-ca-certificates` (or the RHEL-family equivalent) exists specifically to combine
a local CA with the distribution's public set, and letting it do that means every tool
in the container that reads the system default benefits without individual
configuration.

**A runtime with its own bundle needs the same treatment, deliberately.** `certifi` and
similar bundled trust stores are a reasonable design choice, not a bug to work around —
but they still need the internal CA concatenated in, not swapped in, or you have solved
the problem for `curl` and silently broken it for the runtime sitting next to it.
