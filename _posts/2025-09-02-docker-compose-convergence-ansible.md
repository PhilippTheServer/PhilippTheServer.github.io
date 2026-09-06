---
layout: post
title: "Deploying Docker Compose from Ansible Without Shelling Out"
subtitle: "A shell task wrapping docker compose up can never report a real change or run in check mode."
date: 2025-09-02 09:00:00 +0200
tags: [ansible, docker, infrastructure-as-code]
description: >-
  Wrapping docker compose up in an Ansible command task gets a stack deployed
  but throws away everything Ansible is for: real change detection, check
  mode, and a diff you can trust. Here is how to deploy the same stack through
  a module that actually understands compose state, with a full working
  example.
---

## The problem

The fastest way to get a Compose stack deployed from Ansible is also the one that gives
up the most:

```yaml
# Works, but tells you nothing.
- name: Deploy the stack
  ansible.builtin.command: docker compose -f /opt/myapp/docker-compose.yml up -d
  args:
    chdir: /opt/myapp
  changed_when: false
```

`changed_when: false` is there because without it, this task would report `changed: true`
on every run forever — `docker compose up` always exits 0, whether it pulled a new image,
recreated three containers, or did precisely nothing. So the task is silenced instead of
made accurate. The cost of that silence is total: this task can never tell you a container
was recreated, can never fail a `--check` run because check mode does not exist for a raw
`command`, and produces a diff of nothing when you run `ansible-playbook --diff`. You have
correctly deployed the stack and destroyed Ansible's ability to reason about it.

There is a second, quieter problem. `docker compose up -d` is not itself atomic against
concurrent state: if the compose file changed and an image needs pulling, the shell task
blocks for as long as that pull takes, with no distinction in the run's output between "45
seconds because it recreated everything" and "45 seconds because the registry was slow and
nothing changed." Debugging a slow deploy from Ansible's log alone is not possible.

## Working through it

### Compose state belongs to a module, not a shell invocation

The `community.docker` collection ships `community.docker.docker_compose_v2`, which
shells out to the same `docker compose` CLI underneath but parses its output to build a
real Ansible result: which services were created, started, or recreated, and whether
anything happened at all.

```yaml
- name: Deploy the stack
  community.docker.docker_compose_v2:
    project_src: /opt/myapp
    state: present
  register: compose_result
```

`compose_result.changed` is now a real signal — the module compares the previous and
current state of each service and only reports `changed: true` when something in the plan
actually differed. `compose_result.actions` lists what happened, which you can use to
gate a dependent task, the same way `render_config.changed` gates a restart in a role that
does not use containers at all.

### Check mode is not free, but it is available

Because the module models the compose plan rather than only executing it, it supports
`--check`: it can compute what *would* change without applying it, by asking `docker
compose` to plan and diffing that plan against reality. This is the one thing a shell task
structurally cannot do — a raw `command` module either runs the command or does not run it
at all, there is no notion of "run it in dry-run mode" unless the underlying command
itself supports a flag for that and you wire it manually.

```bash
ansible-playbook -i inventory site.yml --check --diff
```

Run against a host with no drift, this reports `changed=0`. Run after editing the compose
file, it reports which services would be recreated, before anything happens.

### Pin the collection, not just the images

`community.docker` moves independently of `ansible-core`, and the compose module's
behaviour around pull policies and orphan removal has changed across versions. A
`requirements.yml` with a pinned version is what makes the role reproducible on a
different machine, or a year later:

```yaml
# requirements.yml
collections:
  - name: community.docker
    version: "3.13.2"
```

```bash
ansible-galaxy collection install -r requirements.yml
```

### Decide what "present" means for images you build yourself

`docker_compose_v2` defaults to pulling images that have a `build:` key only when they are
missing. If your compose file builds a local image and you change the Dockerfile without
bumping a tag, the module will not know to rebuild it — this is a real limitation, not an
oversight to route around silently. State it plainly to whoever maintains the role: either
version your image tags on every change, or set `build: always` and accept that the task
reports changed on every run where a rebuild happens, whether or not the image content
actually differs. There is no way to have both a floating local build and precise change
detection; pick one.

## The solution

A complete, runnable stack: a Compose file for a small web service behind Caddy, and the
playbook and role that deploy it idempotently.

```yaml
# docker-compose.yml
services:
  web:
    image: traefik/whoami:v1.10.3
    restart: unless-stopped
    expose:
      - "80"

  proxy:
    image: caddy:2.8.4-alpine
    restart: unless-stopped
    ports:
      - "8080:80"
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile:ro
    depends_on:
      - web
```

```
# Caddyfile
:80 {
    reverse_proxy web:80
}
```

```yaml
# requirements.yml
collections:
  - name: community.docker
    version: "3.13.2"
```

{% raw %}
```yaml
# roles/compose-stack/tasks/main.yml
- name: Create the stack directory
  ansible.builtin.file:
    path: "{{ compose_project_dir }}"
    state: directory
    owner: root
    group: root
    mode: "0755"

- name: Copy the compose file
  ansible.builtin.copy:
    src: docker-compose.yml
    dest: "{{ compose_project_dir }}/docker-compose.yml"
    owner: root
    group: root
    mode: "0644"

- name: Copy the Caddyfile
  ansible.builtin.copy:
    src: Caddyfile
    dest: "{{ compose_project_dir }}/Caddyfile"
    owner: root
    group: root
    mode: "0644"

- name: Deploy the stack
  community.docker.docker_compose_v2:
    project_src: "{{ compose_project_dir }}"
    state: present
  register: compose_result

- name: Show what changed
  ansible.builtin.debug:
    var: compose_result.actions
  when: compose_result.changed
```
{% endraw %}

```yaml
# site.yml
- name: Deploy the whoami stack
  hosts: docker_hosts
  become: true
  vars:
    compose_project_dir: /opt/whoami
  roles:
    - compose-stack
```

### Verifying it

```bash
ansible-galaxy collection install -r requirements.yml
ansible-playbook -i inventory site.yml
ansible-playbook -i inventory site.yml   # must report changed=0 on the deploy task

curl -s localhost:8080 | head -n1        # Hostname: <container id>

# Prove check mode reflects reality:
sed -i 's/v1.10.3/v1.10.2/' docker-compose.yml
ansible-playbook -i inventory site.yml --check --diff   # reports the image would change
ansible-playbook -i inventory site.yml                  # actually recreates the web service
```

Correct output on the second real run, with no compose file edit in between, is:

```text
PLAY RECAP *********************************************************
host : ok=4   changed=0   unreachable=0   failed=0    skipped=0
```

## Conclusion

**`changed_when: false` on a task that legitimately changes things is not a fix, it is
turning off the instrument.** If the task can genuinely be a no-op, use a module that can
tell the difference; if it cannot, at minimum wire `changed_when` to a real check rather
than silencing it.

**Check mode is a property of the module, not a flag you can bolt onto a shell command.**
Any time a shell task feels indispensable, look for the module first — the collection
ecosystem covers most things people reach for the CLI to do, compose included.

**A local build with a floating tag and precise change detection are mutually exclusive.**
Decide which one the role needs and say so, rather than discovering it during an incident
when a code change silently did not get deployed because the image tag never moved.
