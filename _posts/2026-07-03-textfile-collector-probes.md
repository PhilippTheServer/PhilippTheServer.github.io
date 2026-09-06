---
layout: post
title: "Host Probes via the Textfile Collector Pattern"
subtitle: "Getting a one-off measurement into Prometheus without writing an exporter."
date: 2026-07-03 09:00:00 +0200
tags: [observability, linux]
description: >-
  A single custom host measurement does not justify a dedicated exporter
  process, but it still needs to reach the same scrape pipeline as everything
  else. The textfile collector is node_exporter's answer, and it has two
  failure modes that only show up under load. This covers both, with a
  complete, runnable example.
---

## The problem

`node_exporter` covers CPU, memory, disk and network out of the box. Sooner or later you
need something it does not cover: the age of the most recent backup file, whether a
certificate on disk is close to expiry, the exit status of a cron job that ran overnight.
None of these are worth a dedicated exporter — a whole HTTP server, a `/metrics` endpoint,
a systemd unit, a firewall rule, just to expose one gauge that changes once a day.

The obvious shortcut is to write the value somewhere and have something else read it. The
naive version of that is a cron job appending to a log file and a second script tailing
it, which is not a metrics pipeline, it is two scripts hoping to agree on a format.

`node_exporter`'s textfile collector is the actual answer: point it at a directory, and it
reads every `*.prom` file in that directory on every scrape, parses it as Prometheus
exposition format, and merges the result into `/metrics` as if it had collected the values
itself. Writing a metric becomes writing a text file.

That sounds trivial, and the failure modes are exactly why it is not:

```bash
# Broken. Do not copy this.
echo "backup_age_seconds $(stat -c %Y /var/backups/latest.tar.gz)" \
  > /var/lib/node_exporter/textfile_collector/backup.prom
```

Run this and, most of the time, it works. Occasionally `node_exporter` scrapes the
directory at the exact moment this command is mid-write — truncated the old file, has not
finished writing the new content — and reads a zero-length or half-written `.prom` file.
Depending on the version, that either produces a parse error that `node_exporter` logs and
skips (so the metric silently disappears from that scrape) or, worse, a stale value that
never gets refreshed again because the script that was supposed to update it failed
partway and left the file in a state nothing will overwrite. Both failures are invisible
until someone asks why a graph has a gap or a flat line exactly where a real change should
be.

## Working through it

### Write atomically, or don't call it a write

The fix is one of the oldest tricks in Unix: write to a temporary file in the same
filesystem, then `rename()` it over the target. A `rename` within the same filesystem is
atomic — a reader either sees the whole old file or the whole new one, never a partial
file, because the directory entry flips in one operation rather than the file's contents
changing underneath a reader.

```bash
tmp=$(mktemp /var/lib/node_exporter/textfile_collector/.backup.prom.XXXXXX)
echo "backup_age_seconds $(stat -c %Y /var/backups/latest.tar.gz)" > "$tmp"
mv "$tmp" /var/lib/node_exporter/textfile_collector/backup.prom
```

`mktemp` in the same directory matters, not incidentally — `mv` across filesystems falls
back to copy-then-delete, which reintroduces the exact race this is meant to remove. Same
directory, same filesystem, atomic rename.

### Decide what a stale file means, and enforce it

A script that stops running — a cron entry someone deleted, a systemd timer that started
failing silently — leaves its last `.prom` file in place forever. `node_exporter` will keep
serving that value on every scrape, correctly reporting a number that stopped being true
weeks ago. There is no complaint anywhere, because from `node_exporter`'s point of view
nothing is wrong: it read a valid file and exposed valid content.

Two independent guards close this: alert on the *absence* of a timestamp metric that the
script itself emits (`backup_check_last_run_timestamp_seconds`, evaluated in Prometheus as
`time() - backup_check_last_run_timestamp_seconds > 3600`), and set a `mtime`-based cleanup
that removes `.prom` files nothing has updated in longer than they should ever go
unrefreshed. The first tells you the check is stale; the second stops a genuinely dead
check from reporting a plausible-looking old value forever.

### Get permissions right in one direction

`node_exporter` typically runs as its own unprivileged user and needs read access to the
textfile directory; whatever writes the `.prom` files needs write access to that same
directory. Running the writer as root because it is a cron job "somewhere" is how a
world-writable `/var/lib/node_exporter/textfile_collector` directory happens — and a
world-writable directory that feeds an unauthenticated `/metrics` endpoint is a path for
anything on the host to inject arbitrary metric names and values into your monitoring.
Create a dedicated group, put the collector directory in it, and add exactly the users or
services that need to write to it — nothing broader.

### Emit the right metric type, correctly, every time

The exposition format wants a `# HELP` and `# TYPE` line per metric, and reusing a filename
for a metric whose type changes between runs (a `gauge` one day, a `counter` the next,
because two different scripts happened to write to the same file) produces output
`node_exporter` will refuse to parse. Pick one file per logical metric, declare its type
once, and never let two different producers write to the same `.prom` file.

## The solution

A complete, runnable setup: a Python check script, a systemd service and timer that run it
on a schedule, and a `docker-compose.yml` that runs `node_exporter` against the same
directory so you can see the metric appear on a laptop.

```python
#!/usr/bin/env python3
# /usr/local/bin/backup_age_check.py
import os
import tempfile
import time

TEXTFILE_DIR = "/var/lib/node_exporter/textfile_collector"
TARGET = os.path.join(TEXTFILE_DIR, "backup_age.prom")
BACKUP_FILE = "/var/backups/latest.tar.gz"


def write_atomically(path: str, content: str) -> None:
    directory = os.path.dirname(path)
    fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=".tmp-")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(content)
        os.rename(tmp_path, path)
    except Exception:
        os.unlink(tmp_path)
        raise


def main() -> None:
    now = time.time()
    if os.path.exists(BACKUP_FILE):
        age_seconds = now - os.path.getmtime(BACKUP_FILE)
    else:
        age_seconds = -1  # sentinel: no backup file exists at all

    content = (
        "# HELP backup_age_seconds Age of the most recent backup file in seconds.\n"
        "# TYPE backup_age_seconds gauge\n"
        f"backup_age_seconds {age_seconds}\n"
        "# HELP backup_check_last_run_timestamp_seconds "
        "Unix time this check last completed.\n"
        "# TYPE backup_check_last_run_timestamp_seconds gauge\n"
        f"backup_check_last_run_timestamp_seconds {now}\n"
    )
    write_atomically(TARGET, content)


if __name__ == "__main__":
    main()
```

```ini
# /etc/systemd/system/backup-age-check.service
[Unit]
Description=Write backup age metric for node_exporter textfile collector

[Service]
Type=oneshot
ExecStart=/usr/local/bin/backup_age_check.py
User=metrics-writer
Group=node-exporter-textfile
```

```ini
# /etc/systemd/system/backup-age-check.timer
[Unit]
Description=Run backup-age-check every 5 minutes

[Timer]
OnBootSec=1min
OnUnitActiveSec=5min
AccuracySec=30s

[Install]
WantedBy=timers.target
```

Enable it on a real host with:

```bash
sudo useradd --system --no-create-home metrics-writer
sudo groupadd node-exporter-textfile
sudo usermod -a -G node-exporter-textfile metrics-writer
sudo mkdir -p /var/lib/node_exporter/textfile_collector
sudo chgrp node-exporter-textfile /var/lib/node_exporter/textfile_collector
sudo chmod 2775 /var/lib/node_exporter/textfile_collector

sudo systemctl daemon-reload
sudo systemctl enable --now backup-age-check.timer
```

`chmod 2775` sets the setgid bit, so files created in the directory inherit the group,
which is what lets `node_exporter`'s own user read them without needing to be in every
writer's primary group.

To see the whole thing work end to end on a laptop, without touching a real host, this
`docker-compose.yml` runs the check once and starts `node_exporter` pointed at the same
directory:

```yaml
# docker-compose.yml
services:
  backup-check:
    image: python:3.12-slim
    volumes:
      - textfile-data:/var/lib/node_exporter/textfile_collector
      - ./backup_age_check.py:/usr/local/bin/backup_age_check.py:ro
    command: >
      sh -c "mkdir -p /var/backups &&
             touch /var/backups/latest.tar.gz &&
             python3 /usr/local/bin/backup_age_check.py"

  node-exporter:
    image: prom/node-exporter:v1.8.2
    depends_on:
      - backup-check
    volumes:
      - textfile-data:/var/lib/node_exporter/textfile_collector
    command:
      - "--collector.textfile.directory=/var/lib/node_exporter/textfile_collector"
    ports:
      - "9100:9100"

volumes:
  textfile-data:
```

Run it and check the metric appears:

```bash
docker compose up --abort-on-container-exit backup-check
docker compose up -d node-exporter
curl -s http://localhost:9100/metrics | grep backup_age_seconds
```

Expected output:

```
# HELP backup_age_seconds Age of the most recent backup file in seconds.
# TYPE backup_age_seconds gauge
backup_age_seconds 0.842193
```

## Conclusion

The textfile collector is a small piece of glue and it is tempting to treat it as too
simple to get wrong. The two ways it does go wrong — a torn write and a script that died
without anyone noticing — are both invisible at the point they happen and only visible
much later as a graph that looks wrong for reasons nobody can immediately explain.

Two things generalise past this specific pattern:

**Atomic replace-by-rename is the correct way to publish a file another process reads
concurrently**, whether that file is a Prometheus textfile, a config another daemon
watches, or a state file a script checks on startup. Anywhere a writer and a reader do not
coordinate directly, `write-temp-then-rename` is the coordination.

**A value that stopped updating is a different failure from a value that is wrong, and it
needs its own alert.** `node_exporter` has no way to know your backup check died; it only
knows the file it read parsed correctly. Emitting a "last successfully ran at" timestamp
alongside the actual measurement is what turns silent staleness into something Prometheus
can catch.
