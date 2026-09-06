---
layout: post
title: "Auto-Unsealing Vault Without Cloud KMS or a TPM, Using Tang and Clevis"
subtitle: "Binding unseal keys to a network presence check instead of typing them in at every boot."
date: 2025-11-28 09:00:00 +0200
tags: [vault, security, linux]
description: >-
  Manual unsealing does not scale once other services depend on Vault being
  available at boot, but Vault's built-in auto-unseal options assume a cloud
  KMS, a TPM, or an HSM, none of which fit every environment. This shows how
  to bind Shamir unseal key shares to a Tang server with Clevis instead, so a
  host can unseal itself automatically while it is on the expected network,
  verified end to end including what happens when the Tang server is
  unreachable.
---

## The problem

Vault starts sealed. Nothing can read a secret from it, including the services that are
supposed to start using it, until someone supplies enough Shamir key shares to reconstruct
the master key. That is exactly the property that makes Vault trustworthy at rest, and
exactly the property that turns every reboot, every node replacement, and every planned
maintenance window into a manual step someone has to be awake for.

Vault's built-in auto-unseal mechanisms solve this by delegating the unwrap step to an
external key-management service — AWS KMS, Azure Key Vault, GCP Cloud KMS, an HSM via
PKCS#11, or a TPM. All of them assume infrastructure that a given environment may not have:
no cloud provider, no HSM budget, and a TPM binds the secret to one specific piece of
physical hardware, which is the wrong shape for a cluster where a Vault node might be
rebuilt on different hardware entirely.

Network-bound disk encryption — Tang and Clevis, the mechanism most commonly used to
auto-unlock LUKS volumes at boot — solves a structurally similar problem for disk
encryption: bind a key to "can this host reach a specific server on the network right
now" rather than to a piece of hardware or a cloud account. Vault has no native Tang
integration, but nothing requires the key being unlocked to be a disk key. The same
primitive — encrypt a secret such that decrypting it requires reaching a Tang server —
works just as well applied directly to the Shamir unseal key shares, with a small script
supplying the decrypted result to `vault operator unseal` at boot instead of to `cryptsetup`.

## Working through it

### What Tang and Clevis actually provide

Tang is deliberately minimal: it holds a keypair and answers a key-derivation request over
HTTP, and it never sees or stores the data being protected. Clevis, the client, encrypts
data such that recovering it requires an exchange with a specific Tang server — but Tang's
answer alone is not the plaintext, so a compromised Tang server cannot decrypt data it
never had, and a network capture of the exchange (McCallum-Relyea, the underlying key
exchange) does not reveal the key either. What Tang provides is not confidentiality of the
ciphertext — Clevis's own encryption does that — it is a *presence check*: decryption
succeeds only when the exchange with Tang succeeds, which in practice means "this host is
on the network the Tang server is reachable from".

This is the honest trade being made: an attacker who has already exfiltrated the encrypted
key shares still needs network access to the Tang server to decrypt them, but anyone who
can reach the Tang server and has the ciphertext can decrypt it too — there is no
additional secret involved beyond reachability. It is a real barrier against a stolen disk
or a stolen backup, not a substitute for access control on the host itself.

### Binding a key share is no different from binding a disk key

```bash
echo -n "the-secret" | clevis encrypt tang '{"url":"http://tang.example.internal:8080"}' -y > secret.jwe
clevis decrypt < secret.jwe
```

`clevis encrypt tang` fetches the Tang server's advertisement (its public keys) on first
use — the `-y` flag skips the interactive trust prompt, appropriate for a scripted setup
where the advertisement's fingerprint has already been verified out of band. What comes out
the other end, `secret.jwe`, is a JSON Web Encryption object: ordinary ciphertext that is
safe to store on the disk it is meant to unseal.

### Removing the single point of failure

One Tang server means one thing that has to be reachable at every boot, forever, which
just relocates the availability problem rather than solving it. Clevis's `sss` (Shamir
Secret Sharing) pin can combine several Tang servers with its own threshold, independent of
Vault's own Shamir threshold on the unseal keys themselves:

```bash
clevis encrypt sss '{"t":1,"pins":{"tang":[
  {"url":"http://tang-a.example.internal:8080"},
  {"url":"http://tang-b.example.internal:8080"}
]}}' -y < key-share.txt > key-share.jwe
```

`"t":1` means any one of the two Tang servers being reachable is enough to decrypt —
favouring availability, at the cost that compromising either single server is also enough.
`"t":2` would require both, favouring confidentiality of the presence check at the cost of
availability if either one is down at boot. This is tested and real, not theoretical: with
`t:2`, stopping either Tang server causes decryption to fail outright until it is back;
with `t:1`, either one alone is sufficient. Choose deliberately, and match it to how many
Tang servers are actually run and where.

### Wiring it into Vault's boot sequence

Vault has no hook that calls Clevis directly, so the integration is a small systemd unit
that runs before Vault starts serving, decrypts each key share, and calls
`vault operator unseal` once per share (Vault's own Shamir threshold, separate from any
Clevis threshold, still applies — reaching the unseal endpoint enough times to satisfy
Vault's own `threshold` value is still required).

## The solution

Setting up the key material once, after `vault operator init`:

```bash
#!/usr/bin/env bash
# bind_unseal_keys.sh — run once, after `vault operator init`, on a
# machine that can reach the Tang servers and holds the raw key shares.
set -euo pipefail

TANG_PINS='{"t":1,"pins":{"tang":[
  {"url":"http://tang-a.example.internal:8080"},
  {"url":"http://tang-b.example.internal:8080"}
]}}'

install -d -m 700 /etc/vault-unseal
i=0
for key_share in "$@"; do
  echo -n "$key_share" | clevis encrypt sss "$TANG_PINS" -y \
    > "/etc/vault-unseal/key-${i}.jwe"
  i=$((i + 1))
done
```

```bash
./bind_unseal_keys.sh "$UNSEAL_KEY_1" "$UNSEAL_KEY_2" "$UNSEAL_KEY_3"
```

The raw key shares passed as arguments exist only for the duration of this one-time setup
run; they are not stored anywhere by this script, only their Clevis-encrypted form is.

The boot-time unseal script and its systemd unit:

```bash
#!/usr/bin/env bash
# /usr/local/sbin/vault-auto-unseal
set -euo pipefail

export VAULT_ADDR="https://127.0.0.1:8200"

for jwe in /etc/vault-unseal/key-*.jwe; do
  key=$(clevis decrypt < "$jwe")
  vault operator unseal "$key" >/dev/null
done

vault status
```

```ini
# /etc/systemd/system/vault-auto-unseal.service
[Unit]
Description=Unseal Vault using Clevis-bound key shares
After=vault.service network-online.target
Wants=network-online.target
Requires=vault.service

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/vault-auto-unseal
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
```

```bash
systemctl daemon-reload
systemctl enable --now vault-auto-unseal.service
```

Verifying the failure mode is part of verifying the setup, not optional: stop every Tang
server the `t` threshold needs and confirm the unit fails loudly rather than starting Vault
in a half-configured state —

```bash
systemctl stop tangd.socket   # on the tang hosts
systemctl restart vault-auto-unseal.service
journalctl -u vault-auto-unseal.service --no-pager | tail -5
```

should show `clevis decrypt` failing with a communication error and the unit reporting
`failed`, not a silent skip — and restarting the Tang servers and re-running the unit
should then unseal normally.

## Conclusion

Tang and Clevis do not add a secret to the system, they add a reachability requirement to
an already-encrypted secret — understanding that distinction is the difference between
treating this as a real access control and over-trusting a network boundary as if it were
one.

The same mechanism that autoseals a LUKS volume works unmodified on arbitrary key material,
because Clevis was never specific to disk encryption — it encrypts bytes, and Vault's
unseal keys are just bytes with a particular meaning attached by `vault operator unseal`.

Running more than one Tang server and choosing the `sss` threshold deliberately is the
actual design decision here, not a footnote: `t:1` trades confidentiality margin for boot
reliability, `t:2` trades the reverse, and the right answer depends on how much the
organisation trusts its own network segmentation versus how much it can tolerate a boot
that waits on two servers instead of one.
