---
layout: post
title: "Runtime Secret Injection with a Vault Agent Sidecar and a Wrapped AppRole"
subtitle: "Rendering secrets into a shared volume at runtime instead of baking them into an image."
date: 2025-11-25 09:00:00 +0200
tags: [vault, secrets-management, security, docker]
description: >-
  Rendering secrets into config files during a deploy leaves plaintext
  sitting on disk indefinitely and turns every rotation into a redeploy.
  This sets up a Vault Agent sidecar that authenticates with a single-use,
  response-wrapped AppRole secret and renders a live secret into a file an
  application container reads, verified end to end with a complete
  docker-compose stack.
---

## The problem

A common shortcut for getting secrets into a container is to fetch them once during the
deploy pipeline and bake or mount them as a static file: a CI step calls out to the secret
store, writes the result into a config file, and that file ships with the container or
gets copied onto the host. It works, and it is wrong in two ways that only show up later.

First, the plaintext secret now lives on disk for as long as the container or the host
does, in a file that config management, backups, and anyone with read access to the host
can all see, with no relationship to Vault's own access logging — Vault only knows the
secret was read once, at deploy time, by whatever identity the pipeline used.

Second, rotating that secret means redeploying. If a database password changes and nothing
tells the running containers, they carry on with the stale copy in the file until the next
deploy happens to pick up the new one, at exactly the point where the answer to "please
rotate this credential" should be "already done" rather than "scheduled for the next
release".

A Vault Agent sidecar avoids both: it holds no long-lived credential of its own, keeps a
lease on the secret it fetches, re-renders the file when the secret changes or the lease
needs renewing, and the application never talks to Vault directly. The part that is easy
to get wrong is how the agent itself first authenticates — handing it a plain AppRole
`secret_id` defeats much of the point, because that file is then the same kind of
long-lived, disk-resident credential this design is meant to avoid.

## Working through it

### Why the agent's own bootstrap credential needs its own answer

An AppRole has two halves: a `role_id` (not secret, identifies the role) and a `secret_id`
(secret, proves the caller is allowed to assume that role). If the `secret_id` is generated
once and written to a file the agent reads at startup, that file is a static, long-lived
secret sitting on disk — the same problem this design set out to solve, just moved one
level down.

### Response wrapping turns the secret_id into a single-use token

Vault can generate a `secret_id` and immediately wrap the response in a short-lived,
single-use wrapping token, rather than handing back the `secret_id` itself:

```bash
vault write -f -wrap-ttl=60s -field=wrapping_token \
  auth/approle/role/demo-app/secret-id > wrapped-secret-id
```

The file that actually gets delivered to wherever the container starts — by a
provisioning step, cloud-init, or whatever already has a trusted channel to a new
instance — is that wrapping token, not the `secret_id`. The wrapping token is useless on
its own: it can only be unwrapped once, within its 60-second TTL, and unwrapping it is the
only way to learn the real `secret_id`. If it is intercepted in transit and unwrapped by
someone else, the legitimate agent's own unwrap attempt fails immediately and loudly,
which is a detectable tamper signal that a plain `secret_id` file never gives.

### Telling Vault Agent to expect a wrapped value

Vault Agent's `approle` auto-auth method has a field for exactly this —
`secret_id_response_wrapping_path` — which tells the agent that the file at
`secret_id_file_path` holds a wrapping token, not a raw `secret_id`, and that it should
call the given path to unwrap it:

```hcl
auto_auth {
  method "approle" {
    mount_path = "auth/approle"
    config = {
      role_id_file_path   = "/vault/config/role-id"
      secret_id_file_path = "/vault/config/wrapped-secret-id"
      secret_id_response_wrapping_path = "auth/approle/role/demo-app/secret-id"
      remove_secret_id_file_after_reading = true
    }
  }
}
```

`remove_secret_id_file_after_reading` deletes the wrapped-token file once the agent has
consumed it, so nothing meaningful is left on disk after startup — by design, this
bootstrap credential is meant to be used exactly once.

### Rendering the secret where the application can read it, and nowhere else

The agent's `template` stanza fetches a secret and writes only the fields the application
needs, in whatever format it expects, to a location shared with the application container
but not with anything else:

```hcl
template {
  source      = "/vault/config/app.tpl"
  destination = "/shared/app.env"
}
```

The application never authenticates to Vault, never sees a token, and would keep working
unmodified even if the secret backend changed entirely — its contract is "read this file",
which the agent keeps current for as long as it runs.

## The solution

A complete, runnable stack. `docker compose up` brings up Vault in dev mode, a setup step
configures AppRole and a policy, and the agent and application containers demonstrate the
end-to-end flow.

```yaml
# docker-compose.yml
services:
  vault:
    image: hashicorp/vault:1.20.4
    cap_add: ["IPC_LOCK"]
    ports: ["8200:8200"]
    command: server -dev -dev-root-token-id=root -dev-listen-address=0.0.0.0:8200

  vault-agent:
    image: hashicorp/vault:1.20.4
    depends_on: ["vault"]
    volumes:
      - ./agent:/vault/config
      - shared-secrets:/shared
    entrypoint: ["vault", "agent", "-config=/vault/config/agent.hcl"]

  app:
    image: busybox:1.36
    depends_on: ["vault-agent"]
    volumes:
      - shared-secrets:/shared
    command: sh -c "while true; do cat /shared/app.env 2>/dev/null; sleep 30; done"

volumes:
  shared-secrets:
```

```hcl
# agent/agent.hcl
vault {
  address = "http://vault:8200"
}

auto_auth {
  method "approle" {
    mount_path = "auth/approle"
    config = {
      role_id_file_path   = "/vault/config/role-id"
      secret_id_file_path = "/vault/config/wrapped-secret-id"
      secret_id_response_wrapping_path = "auth/approle/role/demo-app/secret-id"
      remove_secret_id_file_after_reading = true
    }
  }

  sink "file" {
    config = {
      path = "/shared/vault-token"
    }
  }
}

template {
  source      = "/vault/config/app.tpl"
  destination = "/shared/app.env"
}
```

```jinja
{% raw %}{{- with secret "secret/data/app" }}
DB_USER={{ .Data.data.user }}
DB_PASSWORD={{ .Data.data.password }}
{{- end }}{% endraw %}
```

```bash
#!/usr/bin/env bash
# setup.sh — run once, against the vault service, before starting the agent.
set -euo pipefail
export VAULT_ADDR=http://127.0.0.1:8200
export VAULT_TOKEN=root

vault secrets enable -path=secret kv-v2 2>/dev/null || true
vault kv put -mount=secret app user=alice password=s3cret port=5432

vault auth enable approle 2>/dev/null || true
vault policy write demo-app-policy - <<'EOF'
path "secret/data/app" {
  capabilities = ["read"]
}
EOF

vault write auth/approle/role/demo-app \
  token_policies="demo-app-policy" \
  token_ttl=15m \
  token_max_ttl=30m \
  secret_id_ttl=5m \
  secret_id_num_uses=1

vault read -field=role_id auth/approle/role/demo-app/role-id > agent/role-id
vault write -f -wrap-ttl=60s -field=wrapping_token \
  auth/approle/role/demo-app/secret-id > agent/wrapped-secret-id
```

Bringing it up:

```bash
docker compose up -d vault
sleep 3
./setup.sh
docker compose up -d vault-agent app
sleep 5
docker compose logs app --tail=5
```

Expected output from the last command:

```
DB_USER=alice
DB_PASSWORD=s3cret
```

`secret_id_num_uses=1` and the 60-second wrap TTL mean the files `setup.sh` produces are
good for exactly one agent bootstrap. Restarting `vault-agent` after that window requires
running `setup.sh` again to mint a fresh wrapped `secret_id` — this is the actual cost of
the design: the bootstrap credential problem has not been eliminated, it has been shrunk
to a single, time-boxed use and moved to whatever trusted process provisions a new
instance, which in a real deployment is typically the same place that already injects
cloud-init data or a orchestrator-issued identity, not a person typing a command.

## Conclusion

A sidecar does not remove the need for the application to trust something — it moves the
trust boundary from "a secret baked into this image" to "whatever handed this container its
bootstrap credential", which is a smaller and more auditable thing to reason about, not a
disappearance of the problem.

Response wrapping is what makes a single bootstrap credential survive being handled by
infrastructure that was not built to keep secrets confidential — a wrapping token is safe
to log accidentally or pass through a system that was not designed for secret material,
because on its own it grants nothing.

The application container's simplicity is the actual payoff: it reads a file, and rotation,
authentication and renewal all happen in a process that can be swapped out or upgraded
without touching application code — the cost of that simplicity is real operational
machinery around bootstrapping the agent itself, which is worth budgeting for rather than
treating as an afterthought.
