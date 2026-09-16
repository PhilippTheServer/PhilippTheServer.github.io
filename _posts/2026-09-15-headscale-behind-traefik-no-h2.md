---
layout: post
title: "Running a Self-Hosted Tailscale Control Server Behind a Reverse Proxy"
subtitle: "Headscale's documentation says not to put it behind a reverse proxy. The homelab does anyway, and the reason is a single Traefik TLS option that strips HTTP/2 from the ALPN list."
date: 2026-09-15 09:00:00 +0200
tags: [networking, dns, security, docker]
description: >-
  Headscale behind Traefik: tls.options=no-h2@file with alpnProtocols http/1.1,
  because the Tailscale noise handshake breaks when ALPN negotiates h2.
---

## The problem

Tailscale is a WireGuard-based overlay network. The clients are open source; the
part that is not is the control server, the thing that exchanges WireGuard public
keys, hands out the `100.64.0.0/10` addresses, decides which devices may talk to
which, and publishes the routes a node advertises. Headscale reimplements that
control server as a single open-source binary, so a self-hosted network does not
phone home to Tailscale's cloud.

That is the appeal, and it is also the first complication. A control server is,
by definition, a server that clients on the public internet must be able to
reach: the whole point is that a laptop on a café's Wi-Fi and a phone on mobile
data can join the network from anywhere. So it needs a public hostname, a TLS
certificate, and a path through the home firewall. In a homelab that already
routes every public hostname through a single Traefik instance, the obvious move
is to add one more router for the control server, exactly like every other
service.

The obvious move is also the one Headscale's documentation tells you not to do.
The repository's README says they do not support or encourage running it behind a
reverse proxy or in a container. That is fair guidance, and it is also the part
of the setup that took the longest to figure out, because the reason is not in
the sentence: the protocol the clients speak to the control server is not plain
HTTPS.

Tailscale's clients do not authenticate to the control server with a normal
browser-style TLS handshake and a cookie. They use a protocol called noise, a
small, explicit key-exchange pattern that sits on top of the connection and does
its own handshake. The client opens to the control server, performs the noise
handshake, and only then does the authenticated control traffic flow. That
handshake is designed to run over a transport that behaves like a simple,
full-duplex byte stream. HTTP/1.1 over TLS behaves that way. HTTP/2 does not:
it multiplexes many logical streams over one connection, frames them in its own
way, and negotiates itself during the TLS handshake via ALPN. A noise client that
expects a quiet byte stream does not want the proxy deciding to speak HTTP/2
first.

So the constraint is concrete and unglamorous: the reverse proxy must present the
control server's hostname over TLS with HTTP/1.1 only. If the proxy offers
`h2` in the ALPN negotiation, the client may accept it, the noise handshake
meets an HTTP/2 frame layer instead of the stream it expects, and the connection
fails in a way that is easy to misdiagnose as a certificate problem or a
firewall problem, because the TLS layer itself completes fine.

## Working through it

The setup in the homelab is deliberately boring. Headscale runs as a Docker
container on the same Mini PC that runs everything else, with a Postgres
database for its state and a Headplane container for a web UI. The Mini PC is
the box that also runs the Tailscale client itself, advertising the home LAN
subnet to the rest of the mesh, which is what makes the internal services
reachable from a device that is not on the LAN at all.

```yaml
services:
  headscale:
    image: headscale/headscale:0.26.2
    container_name: headscale
    restart: unless-stopped
    command: serve
    volumes:
      - ./services/headscale/config:/etc/headscale:ro
      - headscale_data:/var/lib/headscale
    depends_on:
      headscale-db:
        condition: service_healthy
    networks:
      - proxy
      - internal
    labels:
      - "traefik.enable=true"
      - "traefik.http.routers.headscale.rule=Host(`vpn.example.com`)"
      - "traefik.http.routers.headscale.entrypoints=websecure"
      - "traefik.http.routers.headscale.tls.certresolver=letsencrypt"
      - "traefik.http.routers.headscale.tls.options=no-h2@file"
      - "traefik.http.services.headscale.loadbalancer.server.port=8080"
      - "traefik.docker.network=proxy"

  headscale-db:
    image: postgres:16
    container_name: headscale-db
    restart: unless-stopped
    environment:
      POSTGRES_DB: headscale
      POSTGRES_USER: headscale
    volumes:
      - headscale_db:/var/lib/postgresql
    networks:
      - internal
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U headscale -d headscale"]
      interval: 10s
      timeout: 5s
      retries: 5
      start_period: 30s

networks:
  proxy:
    external: true
  internal:
    name: headscale_internal
    internal: true

volumes:
  headscale_data:
  headscale_db:
```

Three things in that file are worth pausing on.

First, the `internal` network is marked `internal: true`. That is a Docker
network with no route to the outside world. The Postgres database lives on it
and only on it, so even though the container host is on the public internet, the
database has no path to the internet at all. Headscale is on both `proxy` and
`internal`: it talks to the database on the internal side and to Traefik on the
proxy side. The control server is the only thing with a public hostname; its
storage is not.

Second, the router rule is `Host(`vpn.example.com`)`. The control server gets
its own apex-level hostname, not a subdomain of the internal service domain.
That is a deliberate split: the internal services live under one domain that
resolves only inside the mesh, and the control server lives under a hostname
that must resolve from anywhere, because a client on mobile data has to be able
to find it before it is on the network at all.

Third, and the one that matters, the router carries
`traefik.http.routers.headscale.tls.options=no-h2@file`. That label points at a
named TLS option defined in Traefik's dynamic configuration:

```yaml
tls:
  options:
    no-h2:
      alpnProtocols:
        - "http/1.1"
```

The `alpnProtocols` list is the set of application-layer protocols Traefik will
offer during the TLS handshake for this router. By default Traefik offers both
`h2` and `http/1.1`, so a modern client that supports HTTP/2 will negotiate
`h2`. The `no-h2` option replaces that list with `http/1.1` alone. The handshake
still completes, the certificate is still valid, but the transport underneath is
now a plain, full-duplex stream. The noise client gets the byte stream it was
designed for, and the control connection comes up.

The `@file` suffix matters for a reason that is easy to miss. Traefik's dynamic
configuration is split between the entrypoint and router labels (which come from
the Docker labels) and the providers for middlewares, TLS options, and the rest
(which come from files or other sources). A TLS option referenced from a Docker
label has to be resolvable, and `@file` tells Traefik to look for it in the file
provider. Without the suffix, or with the option defined in a provider the
router cannot see, the label is silently ignored and the router falls back to
the default ALPN list, which is exactly the failure mode you are trying to avoid.

The Headscale configuration itself is unremarkable, which is the point. It
points at the public hostname, uses the public DERP map for relays, and leaves
OIDC to the existing Keycloak realm so that joining the mesh is the same
"open this URL and log in" flow as everything else:

```yaml
server_url: "https://vpn.example.com"
listen_addr: "0.0.0.0:8080"

prefixes:
  v4: 100.64.0.0/10
  v6: fd7a:115c:a1e0::/48
  allocation: sequential

derp:
  server:
    enabled: false
  urls:
    - https://controlplane.tailscale.com/derpmap/default
  auto_update_enabled: true
  update_frequency: 24h

dns:
  magic_dns: true
  base_domain: "mesh.example.com"
  nameservers:
    global:
      - 1.1.1.1
      - 8.8.8.8
    split:
      "home.example.com":
        - 192.168.178.1

oidc:
  only_start_if_oidc_is_available: true
  issuer: "https://auth.example.com/realms/homelab"
  client_id: headscale
  scope:
    - openid
    - profile
    - email
```

Two lines in that file do more than they look like. `derp.server.enabled: false`
with the public `derpmap` means the homelab does not run its own DERP relay.
DERP is Tailscale's relay layer: when two devices cannot find a direct path
through their NATs, they bounce through a relay. Using the public map means the
homelab gets relay coverage for free, at the cost of a little traffic transiting
Tailscale's servers when a direct connection is not possible. For a personal
network that is a fair trade, and it is the difference between "works when I am
home" and "works when I am on a train."

The `split` nameservers entry is the other quiet load-bearer. It tells the mesh
that names under `home.example.com` resolve to the Mini PC's LAN address. That
is split DNS: inside the mesh, `something.home.example.com` points at the real
internal service, and from the public internet the same name either does not
resolve or resolves to nothing useful. Combined with the Mini PC advertising the
`192.168.178.0/24` subnet, it is what makes an internal hostname reachable from
a device that is, from the internet's point of view, not in that subnet at all.

## The solution

The fix for the original problem is one label and one small file, and the rest
is the mesh being a mesh.

The label is `traefik.http.routers.headscale.tls.options=no-h2@file` on the
Headscale router. The file is the `no-h2` TLS option that restricts ALPN to
`http/1.1`. Together they make the proxy offer a transport the noise protocol
can actually use. Nothing else about the reverse proxy changes: the same
Let's Encrypt resolver, the same `websecure` entrypoint, the same host-based
routing as every other service. The control server is, from Traefik's point of
view, just another HTTPS hostname with one extra constraint.

The reason it is worth writing down is that the failure is quiet. A client that
negotiates HTTP/2 against a noise endpoint does not fail with a TLS error you can
read in a log; it fails with the connection simply not establishing, or
establishing and then stalling, and the obvious suspects (certificate, port
forward, firewall) all check out. The one thing that differs between "works" and
"does not" is a protocol version the client picked during handshake, which is the
last place you look because the handshake reported success.

The subnet router is the second half of making it a network you use rather than
a network you have. The Mini PC runs the Tailscale client as a native systemd
service, joins the mesh, and advertises the home LAN:

```bash
tailscale up --login-server=https://vpn.example.com \
             --advertise-routes=192.168.178.0/24 \
             --hostname=minipc
```

Headscale requires an operator to approve advertised routes, so there is a one-time
step to enable the `192.168.178.0/24` route, and then a one-time
`tailscale up --accept-routes` on the devices that want to use it. After that,
a laptop on mobile data that is on the mesh can reach `192.168.178.20` and
everything behind the home router, because the Mini PC is carrying that subnet
into the overlay. The route approval is manual and survives re-deploys, which is
a small price for not having a container silently advertise a route it should
not.

The pieces fit together in a way that is easy to state and hard to assemble:

- The control server is public, on its own hostname, behind the same Traefik as
  everything else, with ALPN pinned to HTTP/1.1 so the noise handshake works.
- Its database is on an internal Docker network with no external route.
- The Mini PC is both a mesh node and a subnet router, pulling the LAN into the
  overlay.
- Split DNS points the internal service domain at the Mini PC, so internal
  names resolve only from inside the mesh.
- The public DERP map provides relay fallback, so a device behind a hostile NAT
  still connects, just slower, instead of not at all.

## Conclusion

Headscale's documentation is not wrong to discourage a reverse proxy: the
control server's control connection is not a normal HTTPS endpoint, and a proxy
that negotiates HTTP/2 will break it in a way that is quiet and easy to blame on
something else. The homelab runs it behind Traefik anyway, because the control
server needs a public hostname and the rest of the stack already has exactly one
box that hands out public hostnames. The reconciliation is a single TLS option
that removes `h2` from the ALPN list on that one router, and the `@file` suffix
that makes sure the option is actually found.

The broader lesson is that "self-hosted" does not mean "standalone." The
control server is one container in a stack that already has a reverse proxy, a
certificate authority, an identity provider, and a database. The interesting
part is not that Headscale runs in Docker; it is that a protocol with a specific
transport requirement can be satisfied by the proxy you already have, with one
named option, without giving the service its own special port or its own special
path through the firewall. The mesh then does the rest: a subnet router pulls
the LAN in, split DNS keeps internal names internal, and a public relay keeps a
device on a bad network connected at all.
