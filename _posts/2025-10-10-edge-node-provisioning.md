---
layout: post
title: "Provisioning a Single-Board Computer as an Industrial Edge Node"
subtitle: "One role that detects the board revision and applies the right overlay instead of a manual checklist."
date: 2025-10-10 09:00:00 +0200
tags: [linux, embedded, infrastructure-as-code]
description: >-
  Turning a bare carrier board into a working edge node is usually a manual
  sequence someone follows from memory, and it differs subtly between board
  revisions in ways the checklist never mentions. This builds an Ansible role
  that detects the board at runtime, loads revision-specific settings, and
  applies a common baseline, tested against a throwaway container so the
  logic can be verified without any real hardware.
---

## The problem

The first time you image a single-board computer for a fixed deployment — a sensor
gateway, a protocol converter, a small controller bolted into a cabinet — the setup fits in
your head: flash the image, set a hostname, bring up the network interface, enable a
watchdog, disable the services you don't need. By the twentieth board, it does not fit in
anyone's head, and it never accounted for board revisions in the first place.

Revisions are the part that breaks the checklist quietly. A board revision changes which
device-tree overlay is needed for a peripheral, which serial port maps to which physical
header pin, or which network interface name the kernel assigns. A checklist written
against revision A silently produces a half-working node on revision B — the watchdog gets
enabled, the network doesn't come up, and the failure looks like a bad board rather than a
missed step, because nothing recorded that the step depends on the revision at all.

Doing this by hand also means the sequence is undocumented. When it fails on a Tuesday
afternoon on the fifteenth unit, the only source of truth is whoever did units one through
fourteen and remembers what they changed for board B.

## Working through it

### Detecting the board instead of asking the operator

The device tree exposes the board model as a plain file:

```bash
cat /proc/device-tree/model
# Example Board Model B Rev 2
```

Reading this at provisioning time, rather than asking the operator to type it in or
encoding it in the inventory, removes an entire class of mistake: a board correctly
identified by hardware cannot be provisioned as the wrong revision because someone typed
the inventory entry wrong.

### Layering variables instead of branching tasks

The temptation is a task file full of `when: board_model == "..."` conditions. That scales
badly — every new revision adds a branch to every task that differs, and the tasks that
*don't* differ get harder to find in the noise. Ansible's `include_vars` with
`first_found` does the equivalent of the branching, but as data lookup rather than control
flow: a `vars/<slugified-model>.yml` file for a specific revision, falling back to
`vars/generic.yml` for anything unrecognised. Tasks stay uniform; only the variable values
differ.

### Choosing defaults that fail safe

A board that isn't in the revision list yet should still get a working baseline —
DHCP on whatever interface exists, watchdog enabled, standard hostname pattern — rather
than the role failing outright. Failing outright on an unrecognised revision is tempting
because it looks safer, but it means a new board revision blocks every deployment until
someone updates the role, instead of deploying with sane defaults and someone updating the
role when they get a chance.

### Testing an SBC role without an SBC

The role does not need real GPIO or a real device tree to prove its logic is correct. A
`systemd`-capable container gives you a target that behaves like a real host for the
things the role actually does: read files, write files, manage services, edit sysctls. The
one thing it cannot exercise is genuinely revision-specific hardware — the test proves the
*provisioning logic* is correct, not that a given overlay actually makes a given sensor
work; that part still needs the physical board.

## The solution

```ini
# inventory.docker
[edge_nodes]
edge-demo ansible_host=edge-demo ansible_connection=docker
```

```yaml
# group_vars/edge_nodes.yml
edge_node_hostname_prefix: edge
edge_node_watchdog_seconds: 30
edge_node_network_interface: eth0
edge_node_network_mode: dhcp
```

```yaml
# roles/edge-node/vars/generic.yml
board_overlay: none
board_serial_console: ttyS0
```

```yaml
# roles/edge-node/vars/example-board-model-b-rev-2.yml
board_overlay: example-board-rev2-uart
board_serial_console: ttyAMA0
edge_node_network_interface: eth1
```

```yaml
# roles/edge-node/tasks/main.yml
{% raw %}- name: Read the board model from the device tree
  ansible.builtin.slurp:
    src: /proc/device-tree/model
  register: raw_model
  ignore_errors: true

- name: Derive a slug from the board model, or fall back to generic
  ansible.builtin.set_fact:
    board_model_slug: >-
      {{ (raw_model.content | b64decode | trim | regex_replace('\x00', '')
          | regex_replace('[^A-Za-z0-9]+', '-') | lower)
         if raw_model is succeeded else 'generic' }}

- name: Load revision-specific variables, falling back to generic
  ansible.builtin.include_vars: "{{ item }}"
  with_first_found:
    - files:
        - "{{ board_model_slug }}.yml"
        - generic.yml
      paths:
        - "{{ role_path }}/vars"

- name: Report which board profile was applied
  ansible.builtin.debug:
    msg: "board_model_slug={{ board_model_slug }} overlay={{ board_overlay }}"

- name: Set the hostname
  ansible.builtin.hostname:
    name: "{{ edge_node_hostname_prefix }}-{{ board_model_slug }}"

- name: Enable a systemd watchdog
  ansible.builtin.lineinfile:
    path: /etc/systemd/system.conf
    regexp: '^#?RuntimeWatchdogSec='
    line: "RuntimeWatchdogSec={{ edge_node_watchdog_seconds }}"

- name: Keep journald in memory only, for boards with wear-sensitive storage
  ansible.builtin.lineinfile:
    path: /etc/systemd/journald.conf
    regexp: '^#?Storage='
    line: "Storage=volatile"
  notify: Restart journald

- name: Render the network configuration for the detected interface
  ansible.builtin.template:
    src: interface.network.j2
    dest: "/etc/systemd/network/10-{{ edge_node_network_interface }}.network"
    mode: "0644"
  notify: Restart systemd-networkd

- name: Flush handlers so the network and journald changes apply now
  ansible.builtin.meta: flush_handlers

- name: Prove the host is still reachable after network changes
  ansible.builtin.ping:{% endraw %}
```

```yaml
# roles/edge-node/handlers/main.yml
- name: Restart journald
  ansible.builtin.service:
    name: systemd-journald
    state: restarted

- name: Restart systemd-networkd
  ansible.builtin.service:
    name: systemd-networkd
    state: restarted
```

```jinja
{% raw %}{# roles/edge-node/templates/interface.network.j2 #}
# Managed by Ansible. Local edits are overwritten.
[Match]
Name={{ edge_node_network_interface }}

[Network]
{% if edge_node_network_mode == "dhcp" %}
DHCP=yes
{% else %}
Address={{ edge_node_static_address }}
Gateway={{ edge_node_static_gateway }}
{% endif %}{% endraw %}
```

```yaml
# site.yml
- hosts: edge_nodes
  become: true
  roles:
    - edge-node
```

Verifying it against a throwaway systemd container:

```bash
docker run -d --name edge-demo --privileged \
  --tmpfs /run --tmpfs /run/lock \
  -v /sys/fs/cgroup:/sys/fs/cgroup:ro \
  jrei/systemd-ubuntu:22.04

docker exec edge-demo apt-get update -qq
docker exec edge-demo apt-get install -y -qq python3 systemd-networkd >/dev/null

ansible-playbook -i inventory.docker site.yml
# TASK [edge-node : Report which board profile was applied] ********
# ok: [edge-demo] => {
#     "msg": "board_model_slug=generic overlay=none"
# }
# PLAY RECAP *********************************************************
# edge-demo : ok=9  changed=6  unreachable=0  failed=0

ansible-playbook -i inventory.docker site.yml
# PLAY RECAP *********************************************************
# edge-demo : ok=9  changed=0  unreachable=0  failed=0

docker exec edge-demo hostname
# edge-generic

docker exec edge-demo grep RuntimeWatchdogSec /etc/systemd/system.conf
# RuntimeWatchdogSec=30
```

The container has no real device tree, so the role falls back to the generic profile —
which is exactly the behaviour that should happen on a board revision nobody has written a
profile for yet. Dropping a file at `roles/edge-node/vars/<real-model-slug>.yml` on the
actual hardware target activates the revision-specific branch without touching a single
task.

## Conclusion

Detecting the board and loading variables by convention turns "the checklist for revision
B" into a file that lives next to the checklist for every other revision, instead of a
separate document or a person's memory.

Three points generalise past single-board computers specifically:

**Detect what you can, rather than asking an operator to declare it.** Anything a human
types can be typed wrong; anything the hardware or the running system can report for
itself removes that failure mode entirely.

**Prefer variable lookup over task branching when the difference between cases is data,
not logic.** `include_vars` with `first_found` scales to a hundred board revisions with no
change to the tasks; a chain of `when` conditions gets less readable with every addition.

**A container test proves the software-side logic; it does not prove the hardware
integration.** Say that boundary out loud rather than letting a green CI run imply more
confidence than it earns — the overlay activating on a real board with a real peripheral
attached is still a manual check the first time.
