---
layout: post
title: "Replacing Distributed SSH Keys with a Vault Certificate Authority"
subtitle: "Signing short-lived SSH certificates on demand instead of distributing public keys."
date: 2025-11-18 09:00:00 +0200
tags: [vault, security, tls]
description: >-
  Copying a public key into every host's authorized_keys file does not scale
  and leaves no record of who was granted access or when. This walks through
  standing up Vault's SSH secrets engine as a certificate authority, signing
  short-lived user certificates on demand, and shows a complete, runnable
  demonstration against a disposable sshd container.
---

## The problem

The usual way to grant someone SSH access to a fleet of hosts is to append their public
key to `authorized_keys` on every host they need, or to push it there via configuration
management. This works for a handful of hosts and falls apart as the fleet and the team
grow: revoking access means finding and editing every host that key was ever pushed to,
rotating a compromised key means the same exercise in reverse, and there is no audit trail
beyond whatever the config management tool happened to log — `authorized_keys` itself does
not record who connected, only who is allowed to try.

The alternative is to stop distributing keys at all. An SSH certificate authority signs a
short-lived certificate over a user's public key; hosts are configured once to trust the
CA's public key, and after that, access is a signing operation rather than a file
distribution. Revoking access is "stop signing for this person" rather than "find every
host". A short TTL means a certificate that leaks is only useful for minutes, not until
someone remembers to remove it.

Vault's `ssh` secrets engine can act as that CA. It is easy to under-use, though, because
setting it up looks deceptively similar to generating a normal keypair, and it is easy to
miss the two things that actually make it a fleet-wide access-control mechanism rather than
just a different way to make a key: the certificate's *principals* (who it is valid for)
and its *TTL* (how long it is valid), both enforced by Vault at signing time, not by the
host.

## Working through it

### Why one CA key beats N distributed keys

A host only ever needs to trust one thing: the CA's public key, installed once via
`TrustedUserCAKeys`. Every certificate that CA signs is automatically trusted by every host
configured that way, with no further file distribution. Access control moves entirely into
Vault: who can request a signature, for which `allowed_users`, and for how long, all
governed by a Vault policy and role rather than by what happens to be sitting in a file on
a hundred hosts.

### Standing up the CA and telling a host to trust it

The `ssh` secrets engine, mounted and told to generate its own signing key, is the whole
CA:

```bash
vault secrets enable -path=ssh-client-signer ssh
vault write ssh-client-signer/config/ca generate_signing_key=true
vault read -field=public_key ssh-client-signer/config/ca > trusted-user-ca-keys.pem
```

A host trusts it by pointing `sshd` at that public key:

```
# /etc/ssh/sshd_config.d/10-ca.conf
TrustedUserCAKeys /etc/ssh/trusted-user-ca-keys.pem
```

Nothing user-specific has happened yet — the host now trusts *a* CA, not any particular
person. Who that CA is willing to vouch for is decided entirely on the Vault side, by the
role used to sign.

### Defining who can be whom, and for how long

A role is where the actual access policy lives:

```bash
vault write ssh-client-signer/roles/demo-role -<<EOF
{
  "allow_user_certificates": true,
  "allowed_users": "demo",
  "default_extensions": {
    "permit-pty": ""
  },
  "key_type": "ca",
  "default_user": "demo",
  "ttl": "30m",
  "max_ttl": "1h"
}
EOF
```

`allowed_users` constrains which OS usernames a certificate from this role may claim as a
principal — a certificate cannot be signed for a user this role does not permit, regardless
of who is asking, unless a Vault policy also restricts who may use the role at all (which,
in a real deployment, is exactly how different teams get access to different roles). `ttl`
and `max_ttl` are enforced by Vault when it signs, not by the host, so shortening them takes
effect immediately for every future signature with no host-side change.

### Signing on demand from the client side

A user generates an ordinary keypair — Vault never sees or stores the private key — and
asks Vault to sign the public half:

```bash
ssh-keygen -t ed25519 -f client_key -N "" -C "demo-client"
vault write -field=signed_key ssh-client-signer/sign/demo-role \
  public_key=@client_key.pub valid_principals=demo > client_key-cert.pub
```

`ssh-keygen -Lf client_key-cert.pub` shows exactly what was granted — principals, validity
window, and extensions — which is worth inspecting the first time, since it is the thing
that actually governs access, not the keypair itself.

## The solution

A complete, disposable demonstration: an sshd container configured to trust the CA, and a
client certificate signed by Vault used to log in without ever placing a public key on the
container.

```bash
# 1. Start a dev Vault server (development only — never for real secrets).
vault server -dev -dev-root-token-id=root -dev-listen-address=127.0.0.1:8200 &
export VAULT_ADDR=http://127.0.0.1:8200
export VAULT_TOKEN=root

# 2. Stand up the CA.
vault secrets enable -path=ssh-client-signer ssh
vault write ssh-client-signer/config/ca generate_signing_key=true
vault read -field=public_key ssh-client-signer/config/ca > ca.pub

# 3. Define who it will vouch for.
vault write ssh-client-signer/roles/demo-role -<<EOF
{
  "allow_user_certificates": true,
  "allowed_users": "demo",
  "default_extensions": { "permit-pty": "" },
  "key_type": "ca",
  "default_user": "demo",
  "ttl": "30m",
  "max_ttl": "1h"
}
EOF
```

```dockerfile
# Dockerfile
FROM ubuntu:24.04
RUN apt-get update && apt-get install -y --no-install-recommends openssh-server \
    && rm -rf /var/lib/apt/lists/*
RUN useradd -m -s /bin/bash demo
RUN mkdir -p /run/sshd
COPY sshd_config.d/10-ca.conf /etc/ssh/sshd_config.d/10-ca.conf
COPY ca.pub /etc/ssh/trusted-user-ca-keys.pem
EXPOSE 22
CMD ["/usr/sbin/sshd", "-D"]
```

```
# sshd_config.d/10-ca.conf
TrustedUserCAKeys /etc/ssh/trusted-user-ca-keys.pem
PasswordAuthentication no
PubkeyAuthentication yes
```

```bash
# 4. Build and run the target host. No user key is ever copied into it.
docker build -t ssh-ca-demo .
docker run -d --name ssh-ca-demo -p 2222:22 ssh-ca-demo

# 5. Generate a client keypair and have Vault sign it.
ssh-keygen -t ed25519 -f client_key -N "" -C "demo-client"
vault write -field=signed_key ssh-client-signer/sign/demo-role \
  public_key=@client_key.pub valid_principals=demo > client_key-cert.pub

# 6. Log in using the certificate, not a distributed key.
ssh -p 2222 -i client_key -i client_key-cert.pub \
  -o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no \
  -o IdentitiesOnly=yes -o PreferredAuthentications=publickey \
  demo@127.0.0.1 'whoami; echo CERT_LOGIN_OK'
```

Expected output:

```
demo
CERT_LOGIN_OK
```

`-o IdentitiesOnly=yes` is not cosmetic here: without it, an SSH client with other default
identity files present (or an `ssh-agent` holding unrelated keys) will offer those first,
and `sshd`'s `MaxAuthTries` can be exhausted before it ever tries the certificate,
producing a confusing "Too many authentication failures" rather than a clear rejection.

`UserKnownHostsFile=/dev/null` and `StrictHostKeyChecking=no` are demonstration shortcuts
for a throwaway container with an ephemeral host key — a real deployment should also stand
up an SSH host CA (`ssh-host-signer`, the same pattern applied to host keys instead of user
keys) so clients can verify the host without disabling the check.

Waiting past the certificate's TTL and repeating the same `ssh` command demonstrates the
other half of the mechanism: the certificate is rejected on expiry with no host-side change
required to revoke it.

## Conclusion

Moving from distributed keys to a CA does not remove the need for an access-control
decision, it relocates it: instead of "which hosts have this key in authorized_keys",
the question becomes "which Vault policy can use which role", which is one place to look
rather than every host in the fleet.

Short TTLs are the actual revocation mechanism in this model — there is no host-side
"remove this certificate" operation, only "wait for it to expire" or "rotate the CA key",
so the TTL is a security control, not a convenience setting, and should be chosen
accordingly rather than left at whatever default felt reasonable during setup.

This does not remove the CA itself as a target: whoever can request signatures from a
permissive role effectively has the access that role grants, so the Vault policy guarding
`ssh-client-signer/sign/*` deserves the same scrutiny as the `authorized_keys` files it is
replacing.
