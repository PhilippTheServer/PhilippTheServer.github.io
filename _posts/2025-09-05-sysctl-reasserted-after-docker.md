---
layout: post
title: "Reasserting Kernel Sysctls That Docker Silently Reverts"
subtitle: "The Docker daemon rewrites several net.ipv4 sysctls with no log line to say so."
date: 2025-09-05 09:00:00 +0200
tags: [linux, docker, security]
description: >-
  Docker sets several networking sysctls itself whenever it starts or creates
  a bridge network, and it does so after boot-time hardening has already run,
  overwriting values you set on purpose. This walks through which values move,
  why systemd-sysctl cannot protect you, and a small systemd unit that
  reasserts the values after Docker has finished starting.
---

## The problem

A reasonably hardened Linux host sets a handful of sysctls at boot through a drop-in under
`/etc/sysctl.d/`:

```ini
# /etc/sysctl.d/99-hardening.conf
net.ipv4.conf.all.rp_filter = 1
net.ipv4.conf.all.accept_redirects = 0
net.ipv4.conf.all.send_redirects = 0
net.ipv4.ip_forward = 0
```

`systemd-sysctl.service` applies this at boot, before most other services start. That
ordering is exactly the problem once Docker enters the picture. The Docker daemon needs
`net.ipv4.ip_forward = 1` to route traffic between containers and the outside world, so it
sets it itself, unconditionally, the moment it starts — not as a suggestion, as a direct
`sysctl -w`. Every time it creates a bridge network it can also touch
`net.ipv4.conf.<bridge-iface>.*` values, and depending on daemon configuration it can
rewrite `net.ipv4.conf.all.forwarding` and iptables-adjacent bridge sysctls too.

None of this produces a log line that says "overriding your sysctl." It is simply what
the value is, next time you check:

```bash
sysctl net.ipv4.ip_forward
# net.ipv4.ip_forward = 1
```

The host's own hardening said `0`. Nothing failed, nothing errored, no service refused to
start. If `ip_forward = 0` was there for a reason — this host was never meant to route
between networks, and the sysctl was your check against a bridge or NAT misconfiguration
turning it into one — that assumption is now silently false, and it stays false for as
long as the Docker daemon runs, which on a Docker host is always. A scan or an audit run
later finds the boot-time config file still says `0` and concludes the host is compliant,
while the running kernel disagrees.

This is easy to miss because both halves look correct in isolation: the sysctl file is
right, and `systemctl status systemd-sysctl` reports success. The drift happens entirely
inside the ordering between two independent things starting, and neither one considers
itself responsible for reconciling with the other.

## Working through it

### Why systemd-sysctl cannot win this race

`systemd-sysctl.service` runs once, early in boot, and applies files under
`/etc/sysctl.d/` and `/usr/lib/sysctl.d/`. It does not run again later, and it has no
mechanism to be told "something else changed a value you manage, please reassert it."
Docker starts well after this, as an ordinary systemd service with its own dependency
graph, and it manages sysctls at two separate moments: once at daemon startup, and again
per bridge network creation. Both are outside anything `systemd-sysctl` observes.

Increasing `systemd-sysctl`'s priority or trying to order it after `docker.service`
does not help — sysctl files are applied once at that unit's run, not continuously, and
Docker keeps re-touching values for the lifetime of the daemon, including on every `docker
network create`.

### Confirming which values actually move

Rather than trust documentation about which sysctls Docker touches, check on the actual
host, because it varies with daemon configuration (`iptables: true/false`,
`ip-forward: true/false` in `daemon.json`) and Docker version:

```bash
sysctl net.ipv4.ip_forward net.ipv4.conf.all.rp_filter net.ipv4.conf.all.accept_redirects
sudo systemctl restart docker
sysctl net.ipv4.ip_forward net.ipv4.conf.all.rp_filter net.ipv4.conf.all.accept_redirects
```

On a stock daemon configuration, `ip_forward` reliably flips to `1` here. `rp_filter` and
`accept_redirects` are less consistently touched but do move in some configurations,
particularly once a bridge network exists, because per-interface sysctls under
`net.ipv4.conf.<iface>` get created fresh for that interface and inherit from
`net.ipv4.conf.default`, not from your `all` setting.

### Reasserting after, not instead of

The fix is not to stop Docker from setting what it needs — `ip_forward = 1` genuinely is a
requirement for container networking to work, and fighting that is fighting the feature.
The fix is to run your own hardening pass again, after Docker has finished starting, and
make that reassertion a permanent, ordered dependency rather than a one-off manual step.

A systemd oneshot unit ordered `After=docker.service` and triggered by
`Wants=docker.service` on the boot path, plus a path unit or timer to catch
runtime `docker network create` events if those matter to you, does this reliably. For
most hosts, catching the daemon-startup case covers the actual risk, since per-network
drift on `net.ipv4.conf.<iface>.*` self-heals in practice once traffic flows through the
`all`/`default` values you have already fixed — the exception is if you specifically care
about a per-bridge-interface value, which needs the same reassertion run after each
`docker network create` rather than only at boot.

### Ordering, made explicit

`After=docker.service` on its own only says "if both are starting, do this after that
one" — it does not pull `docker.service` in as a dependency. Pairing it with a target that
already depends on Docker being up, and using `Wants=` rather than `Requires=`, means the
reassertion still fires if Docker's own start fails for an unrelated reason, without
letting a Docker failure block the reassertion of unrelated sysctls that have nothing to
do with containers.

## The solution

```ini
# /etc/sysctl.d/99-hardening.conf
net.ipv4.conf.all.rp_filter = 1
net.ipv4.conf.all.accept_redirects = 0
net.ipv4.conf.all.send_redirects = 0
net.ipv4.ip_forward = 0
```

```bash
#!/usr/bin/env bash
# /usr/local/sbin/reassert-sysctls.sh
set -euo pipefail
/usr/sbin/sysctl --system
logger -t reassert-sysctls "sysctls reapplied after docker.service start"
```

```ini
# /etc/systemd/system/reassert-sysctls.service
[Unit]
Description=Reapply hardening sysctls after Docker has started
After=docker.service
Wants=docker.service

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/reassert-sysctls.sh

[Install]
WantedBy=multi-user.target
```

```bash
sudo chmod 0755 /usr/local/sbin/reassert-sysctls.sh
sudo systemctl daemon-reload
sudo systemctl enable --now reassert-sysctls.service
```

Note this unit deliberately sets `net.ipv4.ip_forward` back to whatever
`/etc/sysctl.d/99-hardening.conf` says, which is `0` in this example. On an actual Docker
host you want container networking to work, `ip_forward` needs to be `1` in your own
hardening file too — the point is not to fight Docker's requirement, it is to make the
final value the one *you* declared, rather than whatever the daemon happened to set and
you happened to never check again.

### Verifying it

```bash
sudo systemctl restart docker
sudo systemctl restart reassert-sysctls.service
sysctl net.ipv4.ip_forward net.ipv4.conf.all.rp_filter
# net.ipv4.ip_forward = 0
# net.ipv4.conf.all.rp_filter = 1

sudo reboot
# after boot:
systemctl is-active docker reassert-sysctls.service
sysctl net.ipv4.ip_forward
```

Correct output after a full reboot is the daemon active, the reassertion unit having run
and exited successfully (`systemctl status reassert-sysctls.service` shows
`Active: inactive (dead)` with a zero exit code, since it is a oneshot), and `ip_forward`
matching your hardening file's value rather than Docker's default.

## Conclusion

**A service that manages kernel state outside its own configuration file is a source of
drift by design, not by bug.** Docker is explicit that it needs `ip_forward`; the mistake
is assuming that need is scoped to boot rather than reasserted for the life of the daemon.

**`systemd-sysctl` only guarantees "correct at this one moment," not "correct forever."**
Anything that runs later and touches the same values needs its own reconciliation step,
ordered explicitly against the thing causing the drift.

**Reassert rather than prevent, when the other side's behaviour is a real requirement.**
Trying to stop Docker from setting `ip_forward` breaks container networking; the workable
fix accepts the value Docker needs is also the value you want, and makes sure the *rest*
of your hardening survives the daemon's own writes.
