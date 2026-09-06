---
layout: post
title: "Ansible Role Idempotence: Why a changed=0 Run Is the Only Proof You Have"
subtitle: "A playbook that reports changes every run has quietly stopped being a drift detector."
date: 2025-08-29 09:00:00 +0200
tags: [ansible, infrastructure-as-code, testing]
description: >-
  A role that reports changed on every run has stopped detecting drift, and
  nothing in Ansible's exit code tells you so. Here is how that happens module
  by module, and how to make the second run's changed=0 an automated check
  rather than something you eyeball.
---

## The problem

Ansible's whole value proposition rests on one promise: run the playbook again, and if
nothing needed to change, nothing will report as changed. That is what lets you run a
role against a thousand hosts and read the summary instead of the diff.

It is easy to write a role that breaks this promise while still working, in the sense
that it converges the host to the right state every time. Here is a task that does
exactly that:

```yaml
# Broken. Reports changed on every single run.
- name: Render the application config
  ansible.builtin.template:
    src: app.conf.j2
    dest: /etc/myapp/app.conf

- name: Restart the application
  ansible.builtin.command: systemctl restart myapp
```

```jinja
{% raw %}{# app.conf.j2 #}
# Rendered {{ ansible_date_time.iso8601 }}
listen_port = {{ app_port }}{% endraw %}
```

The config content is correct on every run. The service ends up in the right state on
every run. And yet `ansible-playbook site.yml` reports two changed tasks every single
time, because the template's rendered content is never byte-identical to what is already
on disk — the timestamp comment guarantees a diff — and `command` has no concept of state
at all; it reports changed unconditionally unless you tell it otherwise.

Nobody notices, because the run still exits 0. `changed=2` looks like normal, healthy
convergence work. The failure is silent in the worst way: the one signal that would tell
you "something actually changed on this host between last run and this one" has been
permanently pegged to "something changed," so it has stopped carrying information. A real
config drift — someone hand-editing `/etc/myapp/app.conf` on the box — produces exactly
the same `changed=1` as a no-op run. You cannot tell them apart from the CLI output, which
is the only thing most people look at.

This matters more as a role gets used for drift detection or `--check` gating in CI, and
it matters most exactly when you need it: during an incident, when you want to know
whether Ansible actually did something on the last run or whether the box was already
fine.

## Working through it

### Command and shell are not state modules

`ansible.builtin.command` and `ansible.builtin.shell` have no idea what "state" means for
the thing they run. They report `changed: true` on every successful invocation, always,
because that is the only safe default when the module cannot inspect the system itself.

The fix is `changed_when`, which lets you tell Ansible how to interpret the result:

```yaml
- name: Restart the application only if the config actually changed
  ansible.builtin.command: systemctl restart myapp
  when: render_config.changed
  changed_when: true
```

That is not idempotence by itself — it is *conditional execution* wired to the real
signal, which is whether the preceding task changed anything. The task now only runs, and
only reports changed, when there was a genuine reason to restart. `changed_when: false` is
also common, for read-only commands you run purely to gather facts:

```yaml
- name: Check whether the service is enabled
  ansible.builtin.command: systemctl is-enabled myapp
  register: enabled_check
  changed_when: false
  failed_when: false
```

### Templates fail idempotence when the content is not deterministic

A `template` task is idempotent by construction, as long as the rendered output is a pure
function of your variables. The moment you put something time-dependent, host-dependent in
an undesired way, or randomly generated into the template, that guarantee is gone.

`{{ ansible_date_time.iso8601 }}` in a comment is the classic version. So is embedding
`{{ inventory_hostname }}` when you meant to embed a group variable that should be the same
everywhere, or a Jinja filter that depends on dictionary ordering that is not guaranteed
stable across Python versions. The fix is boring: keep templates a pure function of
variables that themselves do not change between runs, and if you need a "last rendered"
timestamp for humans, put it in a separate log line, not in the file whose content you
are diffing against.

### Package and service modules are usually fine, until you loosen the version pin

`ansible.builtin.package` with `state: present` and no version is stable — it does nothing
if any version is installed. The moment you pin a version, upstream repositories can
change under you, and a role that used to be a no-op starts reporting changed because the
available version moved and your `state: latest` or unpinned spec resolved differently.
This is not a bug in the module; it is a role that promised idempotence it cannot actually
deliver, because "the newest version available today" is not a stable target.

### Idempotence is not something you inspect, it is something you assert

Looking at `changed=0` in a terminal after a manual run proves nothing about the next
run, on the next host, after the next change. It has to be a check that fails the build,
not a habit. Molecule's `idempotence` sequence does exactly this: it runs `converge`
twice against the same instance and fails the test if the second run reports any changed
or failed tasks.

## The solution

A minimal role plus a Molecule scenario that makes idempotence a CI gate rather than
something you remember to check.

```yaml
# roles/myapp/defaults/main.yml
app_port: 8080
```

```jinja
{% raw %}{# roles/myapp/templates/app.conf.j2 #}
# Managed by Ansible. Local edits are overwritten.
listen_port = {{ app_port }}{% endraw %}
```

```yaml
# roles/myapp/tasks/main.yml
- name: Render the application config
  ansible.builtin.template:
    src: app.conf.j2
    dest: /etc/myapp/app.conf
    owner: root
    group: root
    mode: "0644"
  register: render_config

- name: Restart the application only if the config changed
  ansible.builtin.systemd:
    name: myapp
    state: restarted
  when: render_config.changed
```

`ansible.builtin.systemd` with `state: restarted` still reports changed every time it
restarts — that part is correct, a restart is a real change. What makes the role
idempotent overall is that it only restarts *when the config actually differs*, guarded by
`when: render_config.changed` rather than running unconditionally.

```yaml
# molecule/default/molecule.yml
dependency:
  name: galaxy
driver:
  name: docker
platforms:
  - name: myapp-instance
    image: geerlingguy/docker-debian12-ansible:latest
    pre_build_image: true
provisioner:
  name: ansible
verifier:
  name: ansible
```

```yaml
# molecule/default/converge.yml
- name: Converge
  hosts: all
  become: true
  vars:
    app_port: 8080
  roles:
    - role: myapp
```

```yaml
# molecule/default/verify.yml
- name: Verify
  hosts: all
  become: true
  tasks:
    - name: Check the rendered config content
      ansible.builtin.command: cat /etc/myapp/app.conf
      register: config_contents
      changed_when: false

    - name: Assert the port is present
      ansible.builtin.assert:
        that:
          - "'listen_port = 8080' in config_contents.stdout"
```

Running it:

```bash
pip install "molecule[docker]" molecule-plugins[docker] ansible
molecule test
```

`molecule test` runs, in order: `create`, `converge`, `idempotence`, `verify`, `destroy`.
The `idempotence` step is `converge` run a second time against the same container, and it
fails the whole test if that second run reports anything other than `ok`. Correct output
for the second converge looks like this:

```text
PLAY RECAP *********************************************************
myapp-instance : ok=2   changed=0   unreachable=0   failed=0    skipped=0
```

If instead you see `changed=1` on that second pass, Molecule reports `Idempotence test
failed because of the following tasks:` and names them — which is exactly the diagnostic
you do not get from a manual `ansible-playbook` run in production.

## Conclusion

**`changed_when` is not an optimisation, it is the module telling the truth.** Every
`command` or `shell` task without one is a task that cannot participate in drift
detection, because it has no way to say "nothing needed doing."

**Idempotence has to be tested against the same target twice, in the same run of CI.**
A role that looks idempotent because you happened to run it against an already-converged
host once is not tested; it is observed. Molecule's `idempotence` step, or a hand-rolled
equivalent that runs the playbook twice and greps for `changed=0`, is the actual check.

**Non-idempotent building blocks are fine as long as they are guarded, not banned.** A
service restart is inherently a change every time it happens; the requirement is that it
only happens when something upstream genuinely changed, wired through `register` and
`when`, not that every module in the role reports `changed: false` in isolation.
