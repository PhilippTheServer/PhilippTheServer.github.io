---
layout: post
title: "Infrastructure as Code with Ansible: Making a Host Reproducible from the Repository"
subtitle: "Why a host that cannot be rebuilt from the repository is not really running."
date: 2025-10-28 09:00:00 +0200
tags: [infrastructure-as-code, ansible, linux, reliability]
description: >-
  Infrastructure as code only works if the repository is the single source of
  truth for a host's configuration, and that discipline is easy to state and
  easy to break under pressure. This walks through why partial coverage buys
  almost none of the benefit, why idempotence is the actual product rather
  than a nice property, and ends with a complete Ansible role, run against a
  throwaway container, that a reader can use to watch drift get corrected.
---

## The problem

There is a moment every sysadmin knows. Something is down, you are tired, and you can see
exactly which line in which config file would fix it. You are already logged in. Fixing it
by hand takes eleven seconds. Doing it properly — edit the repository, commit, run the
pipeline — takes four minutes.

Take the eleven seconds and you have just created a machine nobody can rebuild.

The failure this produces is **drift**: the code and the machine start out identical and
then quietly diverge, one eleven-second fix at a time. Drift is nasty because it is
invisible until you need it not to be. The playbook still runs green. It just no longer
describes reality, because reality has grown a hand-added package, a firewall rule opened
for a debugging session, a config edit nobody wrote down. None of it shows up as a diff
anywhere. All of it is load-bearing by the time you find out, usually while rebuilding the
host that had it.

This is easy to get wrong because nothing about it looks wrong at the time. Every
individual hand edit is small, reasonable, and reversible in principle. The playbook that
built the host originally still exists, still runs, still reports success. What it no
longer does is match the host, and there is no alarm for that — only the eventual moment
you try to rebuild and discover the gap the hard way.

## Working through it

### Coverage is a step function, not a percentage

The value of infrastructure as code is not linear in how much of a host is described by
the repository. It behaves like a step function. Ninety percent coverage gives you almost
none of the benefit, because the only question that matters is *can I rebuild this host
right now, from the repository, with nothing else?* — and "mostly" is not an answer to
that question. It just defers the answer to the worst possible moment.

The practical test is not whether a playbook exists. It is whether you would be willing to
wipe the machine tonight and run it.

### Idempotence is the product, not a side effect

A run that reports `changed=0` when nothing should have changed is not a cosmetic detail.
It is a proof — the machine matches the code, right now, verified by execution rather than
by hope. A run that reports `changed=1` on a host you did not touch is telling you either
that reality moved, or that the automation is not actually idempotent and is rewriting
something on every pass. Both are worth knowing immediately, and neither is visible unless
you run the playbook often enough, and read the result, every time.

This is why re-running has to be routine rather than an event. A playbook you run weekly
stays honest. One you run twice a year is fiction with a YAML syntax.

### Dry runs, and proving the dry run is honest

Anything touching a real host gets `--check --diff` first, not because the change is
expected to be wrong but because the gap between what a change is *believed* to do and
what it *actually* does is exactly where outages live.

There is a stronger version of this worth adopting: to prove a change is only what you
think it is, run the dry run with your change temporarily removed and confirm it reports
no difference at all against the current state. If the tool reproduces the existing
configuration byte for byte with nothing pending, then whatever it reports once your change
is back in is genuinely only your change — not your change plus something unrelated the
refactor also touched.

### Secrets never enter the repository

Not encrypted-in-a-pinch, not "it's a private repo", not base64, which is not encryption
and everyone building it knows that. A secret in git history is in every clone, every
fork, every backup and every laptop that ever pulled, and the only fix is rotation — which
is exactly the task nobody wants the moment they discover the problem. The repository
should say *which* secret to use, referenced by name from a secret store, never *what* the
secret is.

## The solution

The example below manages a single file on a throwaway container, badly enough to be
realistic and completely enough to run end to end. It demonstrates the whole argument in
miniature: a first run that creates the file, a second run that proves nothing changed, a
hand edit that simulates the eleven-second fix, and a third run that quietly corrects it —
which is drift correction, not magic.

Start a target that needs nothing pre-installed except Python 3, bootstrapped by Ansible's
`raw` module before anything else runs:

```bash
docker run -d --name iac-demo debian:12-slim sleep infinity
```

```ini
# inventory.ini
[demo]
iac-demo ansible_connection=community.docker.docker
```

```yaml
# requirements.yml
collections:
  - name: community.docker
```

```yaml
# bootstrap.yml
- name: Make sure the container can run Ansible modules at all
  hosts: demo
  gather_facts: false
  tasks:
    - name: Install Python 3 if it is missing
      ansible.builtin.raw: test -e /usr/bin/python3 || (apt-get update && apt-get install -y python3)
      changed_when: false
```

```yaml
# site.yml
- name: Converge the demo host to its declared configuration
  hosts: demo
  tasks:
    - name: Install the packages this host is declared to need
      ansible.builtin.apt:
        name:
          - curl
        state: present
        update_cache: true

    - name: Write the managed message-of-the-day
      ansible.builtin.copy:
        dest: /etc/motd
        content: |
          Managed by Ansible. Local edits are overwritten on the next run.
          role: iac-demo
        mode: "0644"
```

Running it:

```bash
ansible-galaxy collection install -r requirements.yml

ansible-playbook -i inventory.ini bootstrap.yml
ansible-playbook -i inventory.ini site.yml
# PLAY RECAP: iac-demo : ok=2  changed=2  unreachable=0  failed=0

ansible-playbook -i inventory.ini site.yml
# PLAY RECAP: iac-demo : ok=2  changed=0  unreachable=0  failed=0
```

That second `changed=0` is the whole test. Now simulate the eleven-second fix — someone
logs into the container directly and hand-edits the managed file:

```bash
docker exec iac-demo sed -i 's/Local edits are overwritten/Local edits are fine, probably/' /etc/motd

ansible-playbook -i inventory.ini site.yml --check --diff
```

```diff
--- before: /etc/motd
+++ after: /etc/motd
@@ -1,2 +1,2 @@
-Managed by Ansible. Local edits are fine, probably.
+Managed by Ansible. Local edits are overwritten on the next run.
 role: iac-demo
changed: [iac-demo]
```

The dry run shows exactly what the real run will do before it does it. Run it for real and
the file — and the repository's authority over it — is restored:

```bash
ansible-playbook -i inventory.ini site.yml
# PLAY RECAP: iac-demo : ok=2  changed=1  unreachable=0  failed=0
```

That `changed=1` is not noise. It is the automation telling you the machine had drifted,
which is precisely the signal a hand-edited host never produces on its own.

## Conclusion

None of this is really about Ansible. Three things generalise to whatever tool is doing
the converging:

**A rule with exceptions is a preference, and preferences lose to tiredness.** "The
repository describes the host" only holds if it holds every time, including at three in
the morning. The moment it has exceptions, you no longer know which ten percent is
missing, and you find out at the worst possible time.

**Idempotence is how you tell real drift from noise.** Without a `changed=0` baseline, a
`changed=1` run means nothing — it might be correcting drift, or it might just be an
un-idempotent task doing its normal thing. You cannot get the signal without first
guaranteeing the silence.

**A dry run is only trustworthy if you have proven it reports nothing when there is
nothing to report.** That is a five-minute check the first time you write a role, and it
is what turns "I think this is safe" into something closer to a measurement.
