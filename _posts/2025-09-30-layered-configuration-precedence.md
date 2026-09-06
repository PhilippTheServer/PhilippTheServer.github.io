---
layout: post
title: "Configuration Precedence: Docker Secrets, Kubernetes Secrets, Environment, .env, Default"
subtitle: "One resolution order for five ways a value can reach a service, and which one wins."
date: 2025-09-30 09:00:00 +0200
tags: [python, infrastructure-as-code, docker, kubernetes]
description: >-
  A service that reads configuration only from environment variables forces
  every deployment target to shoehorn secrets into that one mechanism, and
  nothing in the code says which value wins when two sources disagree. Here is
  a small, tested resolver with a fixed precedence order, and the tests that
  prove it behaves the same in Compose, in Kubernetes and on a laptop.
---

## The problem

A typical service starts like this:

```python
import os

db_password = os.environ["DB_PASSWORD"]
```

It works, right up until the service has to run in more than one place. Docker Compose
wants to hand it a secret as a file under `/run/secrets/`. Kubernetes wants to do the same
thing, but mounts it somewhere else. A developer running the service locally wants a
`.env` file they never commit. Ops wants an environment variable they can override for one
run without touching any file. None of these is wrong, and none of them is `os.environ`
only.

The usual fix is worse than the problem: read the environment variable, and if it is
`/run/secrets/db_password`, treat it as a path and read the file. Then someone adds
Kubernetes, which mounts secrets somewhere else, and now there is a second special case.
Then someone adds a `.env` loader for local development, and it runs *before* the
environment variable check, so a stale value in `.env` silently overrides a real value set
by the orchestrator. Nobody wrote that behaviour down; it exists because of which `if`
came first.

The failure mode is quiet. The service starts, connects to a database, and works — using
the wrong password, or the right password from the wrong place. You find out during an
incident, when two people are equally certain about what the value should be and both are
reading different sources.

The fix is to decide the order once, write it down as code, and make the resolver tell you
which layer won.

## Working through it

### Naming the layers and picking an order

Five places a value can come from, ordered from most specific to most general:

1. An explicit environment variable — the operator setting this for one run.
2. A Docker secret file (`/run/secrets/<name>`, the Compose and Swarm convention).
3. A Kubernetes secret file (a volume-mounted Secret, path configurable).
4. A `.env` file in the working directory — local development only.
5. A hard-coded default in the code.

The order matters more than which specific order you pick, as long as it is consistent and
documented. This one puts the environment variable first because it is the layer an
operator reaches for when they need to override something *right now*, without editing a
file or redeploying a secret. It puts the two secret mechanisms next because they are the
production path. `.env` sits above the default only because a default exists to let the
service start at all; a `.env` value is still a deliberate developer choice.

### Making the two secret mechanisms look the same

Docker secrets and Kubernetes secrets are both, from the application's point of view, a
directory containing one file per secret name. The only real difference is the mount path,
and that should be configurable rather than hard-coded, because the person writing the
Kubernetes manifest and the person writing the Compose file are rarely the same person and
should not have to agree on a path in advance.

```python
DOCKER_SECRETS_DIR = Path(os.environ.get("DOCKER_SECRETS_DIR", "/run/secrets"))
K8S_SECRETS_DIRS = [
    Path(p)
    for p in os.environ.get("K8S_SECRETS_DIRS", "/var/run/secrets/app").split(":")
    if p
]
```

`K8S_SECRETS_DIRS` takes a colon-separated list because a pod can mount more than one
Secret as more than one volume, and a resolver that only checks one path will work in
development and fail the day a second Secret is added.

### Failing loudly when nothing matches, and saying where a value *did* come from

A `KeyError` with the variable name in it is worth more than `None` propagating three
function calls deep before something breaks on a `NoneType`. The reverse matters just as
much: when a value *is* found, log which layer supplied it — never the value itself, for
anything that might be a secret.

```python
resolution = cfg.resolve("db_password", default="unset")
print(f"db_password resolved from: {resolution.source}")
```

This one line is what turns "the password is wrong" into "the password came from the
`.env` file, and it shouldn't have, because Kubernetes mounted a secret" — a five-minute
diagnosis instead of a half-day one.

### Testing precedence without any of the four backends running

None of Docker, Kubernetes, or a real secrets store needs to be present to test that layer
five loses to layer four, and layer four loses to layer one. `tmp_path` and `monkeypatch`
are enough to construct every combination and assert the winner.

## The solution

```python
# config.py
"""Layered configuration resolution.

Precedence, highest first:
1. Explicit environment variable
2. Docker secret file (DOCKER_SECRETS_DIR/<key>)
3. Kubernetes secret file (any dir in K8S_SECRETS_DIRS/<key>)
4. .env file in the working directory
5. Hard-coded default
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

DOCKER_SECRETS_DIR = Path(os.environ.get("DOCKER_SECRETS_DIR", "/run/secrets"))
K8S_SECRETS_DIRS = [
    Path(p)
    for p in os.environ.get("K8S_SECRETS_DIRS", "/var/run/secrets/app").split(":")
    if p
]


def _read_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


@dataclass
class Resolution:
    value: str
    source: str


@dataclass
class Config:
    dotenv_path: Path = field(default_factory=lambda: Path(".env"))
    docker_secrets_dir: Path = field(default_factory=lambda: DOCKER_SECRETS_DIR)
    k8s_secrets_dirs: list[Path] = field(default_factory=lambda: list(K8S_SECRETS_DIRS))
    _dotenv_cache: dict[str, str] | None = field(default=None, init=False, repr=False)

    def _dotenv(self) -> dict[str, str]:
        if self._dotenv_cache is None:
            self._dotenv_cache = _read_dotenv(self.dotenv_path)
        return self._dotenv_cache

    def resolve(self, key: str, default: str | None = None) -> Resolution:
        env_key = key.upper()

        if env_key in os.environ:
            return Resolution(os.environ[env_key], "environment variable")

        docker_secret = self.docker_secrets_dir / key
        if docker_secret.is_file():
            return Resolution(
                docker_secret.read_text().strip(),
                f"docker secret file ({docker_secret})",
            )

        for secrets_dir in self.k8s_secrets_dirs:
            k8s_secret = secrets_dir / key
            if k8s_secret.is_file():
                return Resolution(
                    k8s_secret.read_text().strip(),
                    f"kubernetes secret file ({k8s_secret})",
                )

        dotenv_values = self._dotenv()
        if env_key in dotenv_values:
            return Resolution(dotenv_values[env_key], f".env file ({self.dotenv_path})")

        if default is not None:
            return Resolution(default, "default")

        raise KeyError(f"no value for {key!r} in any configuration layer")

    def get(self, key: str, default: str | None = None) -> str:
        return self.resolve(key, default).value
```

```python
# main.py
from config import Config


def main() -> None:
    cfg = Config()
    resolution = cfg.resolve("db_password", default="unset")
    print(f"db_password resolved from: {resolution.source}")
    print(f"db_password length: {len(resolution.value)}")  # never print the value


if __name__ == "__main__":
    main()
```

```dockerfile
# Dockerfile
FROM python:3.12-slim

WORKDIR /app
COPY config.py main.py ./

CMD ["python", "main.py"]
```

```yaml
# docker-compose.yml
services:
  app:
    build: .
    secrets:
      - db_password
    environment:
      DOCKER_SECRETS_DIR: /run/secrets

secrets:
  db_password:
    file: ./secrets/db_password.txt
```

```yaml
# k8s-secret-example.yaml
apiVersion: v1
kind: Secret
metadata:
  name: app-secrets
type: Opaque
stringData:
  db_password: k8s-mounted-value
---
apiVersion: v1
kind: Pod
metadata:
  name: app-demo
spec:
  containers:
    - name: app
      image: example.internal/app:1.0.0
      env:
        - name: K8S_SECRETS_DIRS
          value: /var/run/secrets/app
      volumeMounts:
        - name: app-secrets
          mountPath: /var/run/secrets/app
          readOnly: true
  volumes:
    - name: app-secrets
      secret:
        secretName: app-secrets
  restartPolicy: Never
```

```python
# test_config.py
import os

import pytest

from config import Config


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.delenv("DB_PASSWORD", raising=False)


def test_default_wins_when_nothing_else_is_set(tmp_path):
    cfg = Config(
        dotenv_path=tmp_path / "missing.env",
        docker_secrets_dir=tmp_path / "no-docker",
        k8s_secrets_dirs=[tmp_path / "no-k8s"],
    )
    resolution = cfg.resolve("db_password", default="fallback")
    assert resolution.value == "fallback"
    assert resolution.source == "default"


def test_dotenv_beats_default(tmp_path):
    (tmp_path / ".env").write_text("DB_PASSWORD=from-dotenv\n")
    cfg = Config(
        dotenv_path=tmp_path / ".env",
        docker_secrets_dir=tmp_path / "no-docker",
        k8s_secrets_dirs=[tmp_path / "no-k8s"],
    )
    resolution = cfg.resolve("db_password", default="fallback")
    assert resolution.value == "from-dotenv"
    assert resolution.source.startswith(".env file")


def test_k8s_secret_beats_dotenv(tmp_path):
    (tmp_path / ".env").write_text("DB_PASSWORD=from-dotenv\n")
    k8s_dir = tmp_path / "k8s"
    k8s_dir.mkdir()
    (k8s_dir / "db_password").write_text("from-k8s\n")
    cfg = Config(
        dotenv_path=tmp_path / ".env",
        docker_secrets_dir=tmp_path / "no-docker",
        k8s_secrets_dirs=[k8s_dir],
    )
    resolution = cfg.resolve("db_password")
    assert resolution.value == "from-k8s"


def test_docker_secret_beats_k8s_secret(tmp_path):
    docker_dir = tmp_path / "docker"
    docker_dir.mkdir()
    (docker_dir / "db_password").write_text("from-docker\n")
    k8s_dir = tmp_path / "k8s"
    k8s_dir.mkdir()
    (k8s_dir / "db_password").write_text("from-k8s\n")
    cfg = Config(
        dotenv_path=tmp_path / "missing.env",
        docker_secrets_dir=docker_dir,
        k8s_secrets_dirs=[k8s_dir],
    )
    resolution = cfg.resolve("db_password")
    assert resolution.value == "from-docker"


def test_env_var_beats_everything(tmp_path, monkeypatch):
    docker_dir = tmp_path / "docker"
    docker_dir.mkdir()
    (docker_dir / "db_password").write_text("from-docker\n")
    monkeypatch.setenv("DB_PASSWORD", "from-env")
    cfg = Config(docker_secrets_dir=docker_dir)
    resolution = cfg.resolve("db_password")
    assert resolution.value == "from-env"
    assert resolution.source == "environment variable"
```

Run the tests and the Compose demo:

```bash
pip install pytest
pytest -v test_config.py
# 5 passed

mkdir -p secrets
echo "dev-only-value" > secrets/db_password.txt
docker compose run --rm app
# db_password resolved from: docker secret file (/run/secrets/db_password)
# db_password length: 14

docker compose run --rm -e DB_PASSWORD=overridden app
# db_password resolved from: environment variable
# db_password length: 10
```

## Conclusion

The design decision here is not "support all five sources" — that is almost free once you
have file-based secret reading and a `.env` parser. The decision that actually matters is
the *order*, written as one function instead of scattered across several `if os.getenv(...)`
checks added at different times by different people.

Two things generalise beyond this one resolver:

**A precedence order is a contract, not an implementation detail.** Once other code, or
other people, depend on "environment variable wins", changing that order silently is a
breaking change even though no function signature changed.

**Never let a resolver be silent about where a value came from.** The log line costs one
`print` or one structured-log call and turns a values-that-disagree incident into a lookup.
The same idea applies well outside configuration: any time a value can arrive by more than
one path, name the paths and expose which one fired.
