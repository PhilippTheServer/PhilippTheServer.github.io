---
layout: post
title: "Running Alembic Migrations Once, Before the Workers Fork"
subtitle: "A locked entrypoint step replaces migration code that N gunicorn workers all run."
date: 2026-03-20 09:00:00 +0200
tags: [databases, docker]
description: >-
  Calling alembic upgrade head from inside application code means every
  gunicorn worker, and every replica of the container, races to run the same
  migration concurrently. This shows why that races, and an entrypoint that
  migrates exactly once, guarded by a Postgres advisory lock, before the
  server process ever starts.
---

## The problem

A common way to make sure a container's database schema is current is to call Alembic
from inside the application itself:

```python
# app.py — broken. Do not copy this.
from alembic.config import Config
from alembic import command

alembic_cfg = Config("alembic.ini")
command.upgrade(alembic_cfg, "head")

app = FastAPI()

# ... routes ...
```

Run this with `uvicorn app:app` on a laptop and it looks correct: the app imports,
migrates, and starts. Run it in production with a real process manager and it breaks in
a way that's easy to miss in testing, because testing rarely uses more than one worker.

```ini
# gunicorn.conf.py
bind = "0.0.0.0:8000"
workers = 4
worker_class = "uvicorn.workers.UvicornWorker"
```

By default gunicorn does not preload the application in its master process — each worker
imports the WSGI/ASGI module independently, after the master has already forked. That's
exactly backwards from what the module-level migration call needs: instead of running
once, before anything forks, it runs four times, once per worker, and all four workers
start it within milliseconds of each other because they all import the module at roughly
the same point in their own boot sequence.

Alembic has no built-in locking. Four processes calling `alembic upgrade head` against
the same database at the same time will all read the same starting revision from the
`alembic_version` table, and all attempt to apply the same next migration. Depending on
what that migration does, the outcome ranges from a harmless duplicate-key error on the
version table (annoying, but caught) to two workers racing on the same `ALTER TABLE`
and one of them hitting a lock timeout that looks like a random, unreproducible startup
failure. On top of that, whichever worker finishes first starts serving traffic while the
others are still migrating — so for a window of a few hundred milliseconds, the schema a
request sees depends on which worker happened to handle it.

Scale the same container to multiple replicas behind a load balancer, which is the whole
reason to run more than one process, and the same race now spans hosts, not just
processes on one host.

## Working through it

### A migration is not a side effect of importing the app module

The fix isn't "guard the call with a lock inside the app" — it's to stop running
migrations as a consequence of application startup at all. A migration is a one-time
operation against shared state; the number of times it should run is a property of the
deployment, not of how many worker processes the deployment happens to configure.

### "Before the workers fork" means outside the server process entirely

The cleanest place to run a migration is a step that completes and exits *before* the
command that starts the server is even invoked — a container entrypoint. Docker
supports exactly this split: an `ENTRYPOINT` that does preparatory work and then
`exec`s into the real `CMD`, replacing itself in the process table rather than spawning
a child. `os.execvp` in Python does the same thing: the entrypoint process becomes
gunicorn; there is no migration code left running, and no migration code inside `app.py`
to run again per worker.

### One process still isn't one replica

An entrypoint step removes the intra-container race, but a Kubernetes Deployment with
three replicas — or three `docker compose` instances of the same image — still starts
three entrypoints at once, each about to run its own `alembic upgrade head` against the
same database. The number of racing processes changed from "one per worker" to "one per
replica"; the race itself hasn't gone away.

### A Postgres advisory lock serialises across replicas for free

Postgres ships session-level advisory locks precisely for "let exactly one of several
racing sessions do a thing" — no extra service, no extra schema, and the lock is
released automatically if the holding connection dies, so a crashed migration doesn't
leave the database locked forever.

```python
lock_conn = psycopg.connect(database_url, autocommit=True)
lock_conn.execute("SELECT pg_advisory_lock(%s)", (MIGRATION_LOCK_KEY,))
```

The key is an arbitrary fixed integer, stable for the life of this application's
migrations — any other value would let two unrelated locks collide, and a value that
changes between deploys would let two versions of the app migrate concurrently, which is
the exact problem being solved. The two replicas that don't win the lock block on that
call, not on a retry loop, until the holder releases it — and when they wake up, `alembic
upgrade head` against an already-current database is a correct no-op, not an error.

### Traffic starts only after migration succeeds

Because the entrypoint `exec`s into the server only after the migration step returns,
the server literally cannot begin accepting connections until this replica's schema view
is current. There's no window, inside this container, where a request is served against
a schema mid-migration.

## The solution

```dockerfile
# Dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENTRYPOINT ["python", "entrypoint.py"]
CMD ["gunicorn", "-c", "gunicorn.conf.py", "app:app"]
```

```
# requirements.txt
fastapi==0.115.0
gunicorn==23.0.0
uvicorn==0.30.6
alembic==1.13.2
sqlalchemy==2.0.35
psycopg[binary]==3.2.1
```

```python
# entrypoint.py
import os
import sys

import psycopg
from alembic import command
from alembic.config import Config

MIGRATION_LOCK_KEY = 727271


def main():
    database_url = os.environ["DATABASE_URL"]
    lock_conn = psycopg.connect(database_url, autocommit=True)
    print("waiting for the migration lock...", flush=True)
    lock_conn.execute("SELECT pg_advisory_lock(%s)", (MIGRATION_LOCK_KEY,))
    print("lock acquired, running alembic upgrade head", flush=True)
    try:
        alembic_cfg = Config("alembic.ini")
        command.upgrade(alembic_cfg, "head")
    finally:
        lock_conn.execute("SELECT pg_advisory_unlock(%s)", (MIGRATION_LOCK_KEY,))
        lock_conn.close()
    print("migrations complete, starting server", flush=True)
    os.execvp(sys.argv[1], sys.argv[1:])


if __name__ == "__main__":
    main()
```

```ini
# gunicorn.conf.py
bind = "0.0.0.0:8000"
workers = 4
worker_class = "uvicorn.workers.UvicornWorker"
```

```ini
# alembic.ini
[alembic]
script_location = migrations

[loggers]
keys = root,alembic

[logger_root]
level = WARNING
handlers = console

[logger_alembic]
level = INFO
handlers =
qualname = alembic

[handlers]
keys = console

[handler_console]
class = StreamHandler
args = (sys.stderr,)
level = NOTSET
formatter = generic

[formatters]
keys = generic

[formatter_generic]
format = %(levelname)-5.5s [%(name)s] %(message)s
```

```python
# migrations/env.py
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

config = context.config
fileConfig(config.config_file_name)
config.set_main_option("sqlalchemy.url", os.environ["DATABASE_URL"])


def run_migrations_online():
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=None)
        with context.begin_transaction():
            context.run_migrations()


run_migrations_online()
```

```python
# migrations/versions/0001_create_widgets.py
from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None


def upgrade():
    op.create_table(
        "widgets",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(length=200), nullable=False),
    )


def downgrade():
    op.drop_table("widgets")
```

```python
# app.py
from fastapi import FastAPI

app = FastAPI()


@app.get("/healthz")
def healthz():
    return {"status": "ok"}
```

A `docker-compose.yml` that starts three replicas at once, against one database, to show
the lock actually serialising them:

```yaml
# docker-compose.yml
services:
  postgres:
    image: postgres:16
    environment:
      POSTGRES_USER: app
      POSTGRES_PASSWORD: app
      POSTGRES_DB: app
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U app"]
      interval: 2s
      retries: 15

  app-1:
    build: .
    environment:
      DATABASE_URL: postgresql://app:app@postgres:5432/app
    depends_on:
      postgres:
        condition: service_healthy
    ports: ["8001:8000"]

  app-2:
    build: .
    environment:
      DATABASE_URL: postgresql://app:app@postgres:5432/app
    depends_on:
      postgres:
        condition: service_healthy
    ports: ["8002:8000"]

  app-3:
    build: .
    environment:
      DATABASE_URL: postgresql://app:app@postgres:5432/app
    depends_on:
      postgres:
        condition: service_healthy
    ports: ["8003:8000"]
```

```bash
docker compose up --build
```

Correct output interleaves like this — one replica migrates, two wait, all three end up
running:

```
app-2-1  | waiting for the migration lock...
app-1-1  | waiting for the migration lock...
app-3-1  | waiting for the migration lock...
app-1-1  | lock acquired, running alembic upgrade head
app-1-1  | migrations complete, starting server
app-2-1  | lock acquired, running alembic upgrade head
app-2-1  | migrations complete, starting server
app-3-1  | lock acquired, running alembic upgrade head
app-3-1  | migrations complete, starting server
```

Which replica wins the lock first is arbitrary and does not matter — only one of the
three ever executes the migration's DDL; the other two find the database already at
`head` and return immediately.

## Conclusion

The bug in the original code was not "no lock" — it was locating a one-time operation
inside code that a process manager is explicitly designed to run more than once.

**Separate schema migration from application boot entirely.** Once migration code lives
outside `app.py`, the question "how many times does this run per worker" cannot arise,
because there is no migration code left for a worker to execute.

**Any operation that must run exactly once across N racing processes touching shared
state needs a distributed lock, not a lucky ordering.** A Postgres advisory lock is
often the right tool because it is already there, costs nothing to set up, and releases
itself if the process holding it dies — which matters, because a migration that leaves a
lock held forever after a crash is a worse outage than the race it replaced.

**Entrypoint-then-exec is the general pattern for "prepare, then become the real
process".** It costs one extra file and one clear boundary between preparation and
serving. The trade-off worth naming honestly: every replica now waits for the migration
step even when it's a guaranteed no-op, which adds a small, fixed delay to every
rollout — cheap for a table alteration, worth watching if a migration ever does
something genuinely slow, since that cost is now paid serially by the whole fleet at
once rather than once, out of band, before the fleet rolls.
</content>
