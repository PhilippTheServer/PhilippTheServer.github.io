---
layout: post
title: "A host you cannot rebuild is not running"
subtitle: "Infrastructure as code, and the discipline that makes it mean something."
date: 2026-09-06 07:00:00 +0200
tags: [Ansible, Terraform, Linux]
description: >-
  Infrastructure as code is not a tool choice, it is a rule about where truth
  lives. The rule only works if you never break it — and the temptation to break
  it always arrives at three in the morning.
---

There is a moment every sysadmin knows. Something is down, you are tired, and you can see
exactly which line in which config file would fix it. You are already logged in. Fixing it
by hand takes eleven seconds. Doing it properly — edit the repository, commit, run the
pipeline — takes four minutes.

Take the eleven seconds and you have just created a machine nobody can rebuild.

That is the whole argument for infrastructure as code, and it has very little to do with
Ansible or Terraform. It is a claim about where the truth about a system lives. Either the
repository describes the machine, or the machine does — and if it is the machine, then the
knowledge is one disk failure away from gone.

## The rule

I hold everything to one line: **if a host cannot be rebuilt from the repository, it does
not count as running.**

It sounds absolute because it has to be. A rule with exceptions is not a rule, it is a
preference, and preferences lose to tiredness. The value of infrastructure as code is not
linear in how much of your infrastructure is covered. It is closer to a step function.
Ninety percent coverage gives you almost none of the benefit, because you still cannot
answer the only question that matters — *can I rebuild this?* — with yes. You have to
answer with "mostly", and "mostly" means you will find out which ten percent was missing
at the worst possible time.

The practical test is not whether you *have* a playbook. It is whether you would be
willing to wipe the machine right now and run it.

## What actually goes wrong

The failure mode is not that people do not write automation. Almost everyone writes
automation. The failure is **drift**: the code and the machine start out identical and
then quietly diverge, one eleven-second fix at a time.

Drift is nasty because it is invisible until you need it not to be. The playbook still
runs green. It just no longer describes reality, because reality has grown a hand-added
sysctl, a firewall rule someone opened for a debugging session in March, a package
installed to test something. None of it is written down. All of it is load-bearing by the
time you find out.

The defence is to make the code the only path. Not the preferred path — the only one. In
practice that means:

- **Deployments run from a pipeline, not from a laptop.** A pipeline leaves a record, uses
  the committed state, and cannot be persuaded to skip a step because you are in a hurry.
- **Re-running is normal, not an event.** If applying your configuration is scary, you
  will not do it often, and if you do not do it often, drift accumulates between runs. A
  playbook you run weekly stays honest. One you run twice a year is fiction.
- **Idempotence is not a nice property, it is the whole product.** A run that reports
  "changed" when nothing should have changed is telling you something, and if you have
  trained yourself to ignore it, you have thrown away your only drift detector.

That last point is the one people underrate. Once a playbook is genuinely idempotent, a
`changed=0` run is a *proof* — the machine matches the code, right now, verified by
execution rather than by hope. That signal is worth more than the automation itself. I
would rather have a slow, ugly, idempotent playbook than an elegant one that always
reports changes.

## Dry runs are not optional

Anything touching real infrastructure gets a dry run first. `--check`, `plan`, `--diff`,
whatever the tool calls it. Not because I expect the change to be wrong, but because the
difference between what I *think* a change does and what it *does* is exactly where
outages live.

There is a stronger version of this that I have come to rely on: to prove a change is only
what you think it is, run it with your change removed and confirm the tool reports *no
difference at all*. If the tool reproduces the entire existing state byte for byte, then
whatever it reports with your change back in is genuinely only your change. That turns "I
think this is safe" into something closer to a measurement.

It has caught things I would have sworn were fine. A refactor that was supposed to be
cosmetic, quietly changing the order of records. A default that looked inert and was not.

## What does not belong in the repository

Secrets. Ever. Not encrypted-in-a-pinch, not "it is a private repo", not base64 — which is
not encryption and everyone knows it. Secrets live in a secret store and are referenced by
name. The repository says *which* secret, never *what* it is.

This is worth being rigid about because the failure is unrecoverable in a specific way:
once a secret is in git history it is in every clone, every fork, every backup, and every
laptop that ever pulled. Rotating it is the only fix, and rotation is exactly the thing
nobody wants to do at the moment they discover the problem.

## Where the code should not go

Infrastructure as code is not an argument for describing everything. Some things are
genuinely better as data, some as documentation, and some should not exist at all.

The trap is building an abstraction layer over a mess instead of removing the mess. A role
with fourteen boolean flags to accommodate four hosts that drifted apart is not
automation, it is drift with a YAML interface. The honest fix is usually to make the four
hosts the same.

I have written that role. Twice. The second time I noticed sooner.

## Why it is worth it

The pitch for infrastructure as code is normally disaster recovery, and that is real but
it undersells it. I have rebuilt a machine from scratch maybe a handful of times. I have
*read* the repository to find out how something works hundreds of times.

That is the actual return. A system whose configuration is written down is a system you
can reason about without logging in, hand over without a three-hour walkthrough, and
change without the specific fear that comes from not knowing what you are about to break.
Recovery is the insurance policy. Comprehensibility is what you use every day.

And the eleven-second fix at three in the morning? Do it, if the alternative is staying
down. Then write the four-minute version before you go to bed, while you still remember
what you did. The rule is not that you never touch a machine by hand. It is that the
machine never keeps a secret from the repository overnight.
