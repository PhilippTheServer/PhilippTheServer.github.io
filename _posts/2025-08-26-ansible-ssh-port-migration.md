---
layout: post
title: "Migrating the SSH Port Mid-Playbook Without Locking Ansible Out"
subtitle: "Sequencing a config change, a service restart and a connection change so the tool survives its own work."
date: 2025-08-26 09:00:00 +0200
tags: [ansible, security, linux, infrastructure-as-code]
description: >-
  Changing sshd's port during provisioning severs Ansible's own control
  connection unless the validation, restart, connection update and firewall
  change happen in the right order. Here is that order, and the two guards that
  make it safe to re-run.
---

## The problem

Ansible reaches a host over SSH. If a playbook changes the port sshd listens on, it is
changing the thing it is standing on.

Do it naively and the run dies halfway:

```yaml
# Broken. Do not copy this.
- name: Set the SSH port
  ansible.builtin.lineinfile:
    path: /etc/ssh/sshd_config
    regexp: '^#?Port '
    line: 'Port 2222'

- name: Restart sshd
  ansible.builtin.service:
    name: sshd
    state: restarted

- name: Anything at all after this
  ansible.builtin.command: true
```

The restart happens, sshd stops listening on 22, and the next task tries to reuse a
connection to a port nothing is bound to. The run fails with a timeout, and now the host
is in a state neither the playbook nor you expected: the config says 2222, the firewall
probably still says 22, and your inventory says 22.

The failure is bad. What is worse is that it is *not reliably reproducible*. Whether the
run dies at the restart or three tasks later depends on SSH connection multiplexing,
`ControlPersist` timeouts and how fast sshd drops existing sessions. So it looks flaky,
and flaky problems get retried rather than fixed.

There are four separate hazards here, and each needs its own answer:

1. A syntax error in the new config makes sshd fail to start, and now nothing can log in.
2. The restart happens at an unpredictable point relative to the rest of the play.
3. Ansible keeps using a connection to the old port.
4. The firewall is opened or closed at the wrong moment relative to the restart.

## Working through it

### Validate before you write

`sshd -t` checks a config file without touching the running daemon. Ansible's file modules
take a `validate` argument that runs a command against the *candidate* file and refuses to
install it if the command fails.

```yaml
- name: Install the SSH hardening drop-in
  ansible.builtin.template:
    src: 10-hardening.conf.j2
    dest: /etc/ssh/sshd_config.d/10-hardening.conf
    owner: root
    group: root
    mode: "0644"
    validate: /usr/sbin/sshd -t -f %s
```

`%s` is replaced with the temporary file's path. If `sshd -t` exits non-zero the file is
never moved into place, so a typo costs you a failed task instead of a locked-out host.

A drop-in under `sshd_config.d/` rather than an edit to `sshd_config` matters too: it is a
whole file you own, so it is idempotent by construction. `lineinfile` against the main
config is a regex that has to keep matching across distribution upgrades, and one day it
will not.

### Make the restart happen where you decide

A handler runs at the end of the play, which is exactly wrong here — everything between
the config change and the end of the play would still be running against the old port,
and the port change would land after the tasks that depend on it.

Notify a handler, then force it to run immediately:

```yaml
- name: Install the SSH hardening drop-in
  ansible.builtin.template:
    src: 10-hardening.conf.j2
    dest: /etc/ssh/sshd_config.d/10-hardening.conf
    mode: "0644"
    validate: /usr/sbin/sshd -t -f %s
  notify: Restart sshd

- name: Apply the pending sshd restart now, not at the end of the play
  ansible.builtin.meta: flush_handlers
```

`flush_handlers` runs every notified handler at that point. The restart is now at a place
you chose.

### Tell Ansible where the host went

`ansible_port` is an ordinary variable. Setting it changes where subsequent connections
go — but only for connections that have not already been established, and Ansible holds
a multiplexed connection open.

Both halves are needed:

{% raw %}
```yaml
- name: Point subsequent connections at the new port
  ansible.builtin.set_fact:
    ansible_port: "{{ ssh_port }}"

- name: Drop the multiplexed connection so the new port is used
  ansible.builtin.meta: reset_connection
```
{% endraw %}

`reset_connection` closes the persistent control socket. Without it, `set_fact` changes a
variable that the existing connection does not consult, and the next task quietly keeps
using the old socket until it expires.

### Open the firewall before you need it, close the old port after

The ordering that cannot fail is: allow the new port, restart, verify, then remove the old
one. Any other order has a window in which nothing can connect.

{% raw %}
```yaml
- name: Allow the new SSH port before sshd moves to it
  community.general.ufw:
    rule: allow
    port: "{{ ssh_port }}"
    proto: tcp
```
{% endraw %}

Removing the old rule belongs at the very end, after a task has actually succeeded over
the new port — which is the proof that the move worked.

## The solution

The complete role, in order. Everything before `flush_handlers` prepares; everything after
it operates on a host that has already moved.

```yaml
# roles/ssh-port/defaults/main.yml
ssh_port: 2222
ssh_port_old: 22
```

```yaml
# roles/ssh-port/handlers/main.yml
- name: Restart sshd
  ansible.builtin.service:
    name: sshd
    state: restarted
```

{% raw %}
```yaml
# roles/ssh-port/tasks/main.yml
- name: Allow the new SSH port before sshd moves to it
  community.general.ufw:
    rule: allow
    port: "{{ ssh_port }}"
    proto: tcp

- name: Install the SSH port drop-in
  ansible.builtin.template:
    src: 10-port.conf.j2
    dest: /etc/ssh/sshd_config.d/10-port.conf
    owner: root
    group: root
    mode: "0644"
    validate: /usr/sbin/sshd -t -f %s
  notify: Restart sshd

- name: Apply the pending restart here, not at the end of the play
  ansible.builtin.meta: flush_handlers

- name: Point subsequent connections at the new port
  ansible.builtin.set_fact:
    ansible_port: "{{ ssh_port }}"

- name: Drop the multiplexed connection so the new port is used
  ansible.builtin.meta: reset_connection

- name: Prove the new port works before closing the old one
  ansible.builtin.ping:

- name: Close the old SSH port
  community.general.ufw:
    rule: deny
    port: "{{ ssh_port_old }}"
    proto: tcp
  when: ssh_port_old | int != ssh_port | int
```
{% endraw %}

```jinja
{% raw %}{# roles/ssh-port/templates/10-port.conf.j2 #}
# Managed by Ansible. Local edits are overwritten.
Port {{ ssh_port }}{% endraw %}
```

The `ansible.builtin.ping` task is the load-bearing one. It is not a network ping — it is
a module execution over SSH, so it succeeds only if Ansible genuinely reached the host on
the new port. Closing the old port before that check is how you find out the move failed
by losing access to the host.

### Making it re-runnable

The role above works on a host still using the old port. It also has to work on a host
that has *already* moved, or it is a one-shot script rather than a role.

That case works by accident of ordering, and it is worth understanding why rather than
trusting it. On a second run the template is unchanged, so the handler is never notified,
so `flush_handlers` does nothing and sshd is not restarted. `set_fact` and
`reset_connection` still run and are harmless. The `ping` still proves reachability.

The remaining problem is the *first* connection of the second run: the inventory says port
22, the host listens on 2222. Solve it in the inventory, not in the role — after a
successful migration the inventory is what should carry the new port:

```yaml
# inventory/host_vars/example.yml
ansible_port: 2222
```

Until that is committed, a run against a migrated host cannot connect at all, and the role
never gets the chance to be idempotent. This is the part people forget, because the first
run works.

### Verifying it

```bash
# From the control machine, against a throwaway VM:
ansible-playbook -i inventory site.yml --tags ssh-port
ansible-playbook -i inventory site.yml --tags ssh-port   # must be changed=0

ss -tlnp | grep sshd        # on the host: listening on 2222, not 22
sshd -T | grep -i '^port'   # the effective config, not the file
```

The second run reporting `changed=0` is the whole test. If it reports a change, something
in the role is rewriting a file on every pass, and you have lost the ability to tell a
real drift from noise.

## Conclusion

The mistake is treating an SSH port change as a config change. It is a config change plus
a restart plus a connection change plus a firewall change, and the four have exactly one
safe order.

Three things generalise beyond SSH:

**Validate candidate config before installing it.** `validate:` costs one line and turns
a class of "the service will not start and I cannot get in" outages into a failed task.
Anything with a `-t`-style check — sshd, nginx, sudoers via `visudo -c`, Prometheus via
`promtool` — deserves it.

**When automation changes what it is standing on, `flush_handlers` is how you choose the
moment.** Deferring to the end of the play is the default because it is usually right;
here it is precisely wrong.

**A configuration change is not applied until something has proven it.** The `ping` before
closing the old port is the difference between "the file says 2222" and "I can reach this
host on 2222". Only the second one lets you safely remove your way back in.
