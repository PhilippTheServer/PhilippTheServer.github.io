---
layout: post
title: "Docker Registry Credentials for Two Local Users on One Host"
subtitle: "Docker reads whoever invoked the CLI's config, so logging in as root alone leaves a deploy user unauthenticated."
date: 2025-09-09 09:00:00 +0200
tags: [docker, ansible, linux]
description: >-
  The Docker CLI reads credentials from the config file of whoever runs it,
  not from a single host-wide location, so a login task that only runs as
  root leaves any other user pulling private images unauthenticated and
  failing with a generic denied error. This shows how to provision registry
  credentials for every user who actually needs them, with a reproducible
  local registry to test against.
---

## The problem

An Ansible role provisions a host, logs into a private registry as part of that
provisioning, and later a deploy pipeline running as an unprivileged `deploy` user tries
to pull an image and gets:

```text
Error response from daemon: pull access denied for registry.example.com/app,
repository does not exist or may require 'docker login'
```

The login happened. It is right there in the play recap, `changed: true`, no errors. The
confusion is that "logged in" was never a host-wide fact — it was true for exactly one
user, and it was probably root, because that is who Ansible tasks run as by default once
`become: true` is set.

{% raw %}
```yaml
# This authenticates root. Nobody else.
- name: Log in to the registry
  community.docker.docker_login:
    registry_url: registry.example.com
    username: "{{ registry_username }}"
    password: "{{ registry_password }}"
  become: true
```
{% endraw %}

The Docker CLI does not consult a single system-wide credential store. It reads
`$HOME/.docker/config.json` of the user invoking `docker`, full stop. `become: true`
without a specific `become_user` runs as root, which writes `/root/.docker/config.json`.
A `deploy` user with membership in the `docker` group can run `docker` commands just
fine — group membership is what lets it talk to the daemon socket at all — but it has its
own, entirely separate `$HOME/.docker/config.json`, which the login task never touched.

The failure mode is quiet because both the login task and the pull command, on their own,
behave exactly as documented. The mismatch only exists in the gap between them: two
different users, two different home directories, one shared assumption that "logged in"
means something host-wide.

## Working through it

### Where the CLI actually looks

`docker login` and every module that wraps it write to
`~/.docker/config.json` for the user context they ran in. `docker pull`, in turn, reads
that same file for the user running the pull. There is no daemon-side credential store for
this — authentication is a client-side concern, mediated entirely through that JSON file
and, optionally, a credential helper it references.

This means "did the login succeed" and "will this user's pull succeed" are two different
questions whenever more than one user account runs `docker` commands on a host. It comes
up constantly with CI runners and deploy tooling, where root provisions the host but an
unprivileged service account performs the actual deployment for defence-in-depth reasons.

### Two honest ways to fix it

The direct fix is to run the login as every user that needs it, rather than once as root:

{% raw %}
```yaml
- name: Log in to the registry as root
  community.docker.docker_login:
    registry_url: registry.example.com
    username: "{{ registry_username }}"
    password: "{{ registry_password }}"
  become: true
  become_user: root

- name: Log in to the registry as the deploy user
  community.docker.docker_login:
    registry_url: registry.example.com
    username: "{{ registry_username }}"
    password: "{{ registry_password }}"
  become: true
  become_user: deploy
```
{% endraw %}

This is explicit and easy to audit, at the cost of the credential landing in two files
that now both need the same lifecycle — rotate the password and both need updating, or you
are back to one working user and one silently stale one.

The other approach is to write the config file directly with `config_path`, and control
its permissions yourself, which lets you loop over every user that needs it from one
credential source instead of one task per user:

{% raw %}
```yaml
- name: Log in as every local user that needs the registry
  community.docker.docker_login:
    registry_url: registry.example.com
    username: "{{ registry_username }}"
    password: "{{ registry_password }}"
    config_path: "/home/{{ item }}/.docker/config.json"
  loop: "{{ registry_consumer_users }}"
  become: true

- name: Fix ownership of the config file the module just wrote
  ansible.builtin.file:
    path: "/home/{{ item }}/.docker/config.json"
    owner: "{{ item }}"
    group: "{{ item }}"
    mode: "0600"
  loop: "{{ registry_consumer_users }}"
  become: true
```
{% endraw %}

The ownership fix matters because the module writes the file as whichever user the task
ran as — root, here, since `become_user` was not overridden — regardless of whose home
directory the path points into. Skip that step and the target user cannot even read their
own credential file.

### Neither option survives a plaintext password in the repository

`registry_password` has to come from Ansible Vault or an external secrets store, not a
group_vars file in cleartext — this is true regardless of which user ends up holding the
credential. That is a separate problem from the one this article is about, but it is the
one that turns a config mistake into an incident, so it is worth stating plainly rather
than leaving as an implied assumption in the examples below.

## The solution

A complete, reproducible test: a local authenticated registry running in a container, and
an Ansible playbook that provisions credentials for two users, verified by having both
actually pull.

```bash
# Set up a throwaway authenticated registry to test against.
mkdir -p auth
docker run --rm --entrypoint htpasswd httpd:2.4 -Bbn testuser testpass123 > auth/htpasswd

docker run -d --name test-registry -p 5000:5000 \
  -v "$(pwd)/auth:/auth" \
  -e REGISTRY_AUTH=htpasswd \
  -e REGISTRY_AUTH_HTPASSWD_REALM="Registry Realm" \
  -e REGISTRY_AUTH_HTPASSWD_PATH=/auth/htpasswd \
  registry:2.8.3

docker pull traefik/whoami:v1.10.3
docker tag traefik/whoami:v1.10.3 localhost:5000/whoami:v1.10.3
docker login localhost:5000 -u testuser -p testpass123
docker push localhost:5000/whoami:v1.10.3
docker logout localhost:5000
```

```yaml
# requirements.yml
collections:
  - name: community.docker
    version: "3.13.2"
```

{% raw %}
```yaml
# site.yml
- name: Provision registry credentials for every consuming user
  hosts: docker_hosts
  vars:
    registry_url: "localhost:5000"
    registry_username: "testuser"
    registry_password: "testpass123"   # In Vault in anything real.
    registry_consumer_users:
      - root
      - deploy
  tasks:
    - name: Ensure the deploy user exists
      ansible.builtin.user:
        name: deploy
        groups: docker
        append: true
        shell: /bin/bash
      become: true

    - name: Ensure each user's .docker directory exists
      ansible.builtin.file:
        path: "{{ '/root/.docker' if item == 'root' else '/home/' + item + '/.docker' }}"
        state: directory
        owner: "{{ item }}"
        group: "{{ item }}"
        mode: "0700"
      loop: "{{ registry_consumer_users }}"
      become: true

    - name: Log in as every consuming user
      community.docker.docker_login:
        registry_url: "{{ registry_url }}"
        username: "{{ registry_username }}"
        password: "{{ registry_password }}"
        config_path: "{{ '/root/.docker/config.json' if item == 'root' else '/home/' + item + '/.docker/config.json' }}"
      loop: "{{ registry_consumer_users }}"
      become: true
      register: login_result

    - name: Fix ownership of the written config files
      ansible.builtin.file:
        path: "{{ '/root/.docker/config.json' if item == 'root' else '/home/' + item + '/.docker/config.json' }}"
        owner: "{{ item }}"
        group: "{{ item }}"
        mode: "0600"
      loop: "{{ registry_consumer_users }}"
      become: true
```
{% endraw %}

### Verifying it

```bash
ansible-galaxy collection install -r requirements.yml
ansible-playbook -i inventory site.yml

# On the host, as each user in turn:
sudo docker pull localhost:5000/whoami:v1.10.3
sudo -u deploy docker pull localhost:5000/whoami:v1.10.3
```

Correct output is an identical, successful pull for both invocations. Before the fix,
`sudo docker pull` (root) succeeds and `sudo -u deploy docker pull` fails with `pull
access denied`, which is the exact split this article is about, reproduced on a laptop
with no private infrastructure involved.

## Conclusion

**Docker authentication is per-user client state, not a host-wide fact.** Anything that
says "the registry login task ran" has to also say "as which user," because that is the
only scope the credential actually has.

**`become: true` without an explicit `become_user` defaults to root, and root's config is
usually not the config that matters.** The user actually running `docker pull` in
production — a deploy account, a CI runner's service account — is very often not root, and
provisioning has to target that account specifically.

**A credential written for two users is two copies to keep in sync, not one.** Rotation,
expiry and revocation all need to happen against every file you wrote, which is a good
argument for a credential helper backed by a real secrets manager once there are more than
one or two consuming accounts on a host.
