---
layout: default
title: About
subtitle: The longer version, with dates.
permalink: /about/
nav_order: 2
description: >-
  Background, roles and education of Philipp Lehmann — Head of Administration and
  IT at Nerd Force1 UG (AI-Gruppe) since 2022, Executive Office at open Skunkforce
  e.V. since 2025, IT-Security student at Ruhr University Bochum since 2021.
---

<ul class="facts">
  <li><span class="k">Name</span> <span>Philipp Lehmann</span></li>
  <li><span class="k">Handle</span> <span>PhilippTheServer</span></li>
  <li><span class="k">Based in</span> <span>Bochum, North Rhine-Westphalia, Germany</span></li>
  <li><span class="k">Role</span> <span>Head of Administration and IT, Nerd Force1 UG (AI-Gruppe)</span></li>
  <li><span class="k">Studying</span> <span>B.Sc. IT Security / Information Engineering, Ruhr University Bochum</span></li>
  <li><span class="k">ORCID</span> <span><a href="https://orcid.org/0009-0002-3922-2471">0009-0002-3922-2471</a></span></li>
  <li><span class="k">Contact</span> <span><a href="mailto:philipp.lehmann@gruppe.ai">philipp.lehmann@gruppe.ai</a></span></li>
</ul>

## What I actually do

I own production infrastructure end to end: architecture, deployment, and the far
less glamorous day-2 operations that decide whether any of it was a good idea. That
spans bare metal, the container platforms on top of it, the network underneath it, and
the internal applications that the rest of the company actually touches.

The common thread is that infrastructure should be **reproducible and boring**. A host
that only one person understands is an outage with a delay fuse. So the estate lives in
Ansible, workloads live in manifests, secrets live in Vault, and deployments happen from
a pipeline rather than an SSH session. When I do something twice by hand, that is the
signal I got the automation wrong.

I am also fond of breaking things deliberately. Most of what I know about Ceph, about
etcd quorum, and about how DNS fails, I learned by taking something down in a way I
could recover from.

## Roles

### Head of Administration and IT · Nerd Force1 UG · since March 2022

Nerd Force1 is part of the [AI-Gruppe](https://gruppe.ai) umbrella brand. I am
responsible for the company's IT: the servers, the network, the platforms, and the
internal tooling.

<ul class="stack">
  <li><b>Docker standalone cluster</b> carrying the bulk of company workloads</li>
  <li><b>Kubernetes cluster</b> for orchestrated workloads, delivered with ArgoCD</li>
  <li><b>Ceph cluster</b> providing distributed storage across bare metal</li>
  <li><b>Internal network</b>: WireGuard VPN and Bind9 DNS, defined in the repository</li>
  <li><b>The whole server estate as Ansible</b>, so a rebuild is a pipeline run</li>
</ul>

The largest single piece of work is the **internal operations platform** — a full-stack
application that centralised operations and replaced several external tools at once. It
carries the internal messenger, time tracking, the ticket system, accounting, a Docker
registry frontend, DNS management, WireGuard management and hosting orchestration.
Angular on the front, FastAPI on the back, MySQL and InfluxDB underneath, Celery workers
for anything that should not block a request.

### Executive Office · open Skunkforce e.V. · since January 2025

I lead a small developer team and coordinate the association's open-source work. Once a
year we host [emBO++](https://www.embo.io/), the international embedded systems
conference, and KiCon, both in Bochum. Association work on GitHub:
[github.com/skunkforce](https://github.com/skunkforce).

## Education

**B.Sc. IT Security / Information Engineering** — Faculty of Computer Science,
Ruhr University Bochum, since October 2021.

Coursework I keep notes and code for publicly: implementation of cryptographic schemes,
software security, operating systems, system theory, and electrical engineering. Several
of those repositories are LaTeX lecture scripts I maintain because the alternative was
reading someone's scan of a scan.

## The stack, honestly

<ul class="facts">
  <li><span class="k">Containers</span> <span>Docker (daily), Kubernetes, Portainer, Harbor</span></li>
  <li><span class="k">IaC</span> <span>Ansible, Terraform, Kubernetes manifests, Kustomize</span></li>
  <li><span class="k">CI/CD</span> <span>GitHub Actions, ArgoCD, Jenkins</span></li>
  <li><span class="k">Storage &amp; net</span> <span>Ceph, WireGuard, Bind9, HashiCorp Vault</span></li>
  <li><span class="k">Languages</span> <span>Python, Go, C, C++, TypeScript, Bash</span></li>
  <li><span class="k">Apps</span> <span>FastAPI, Celery, Angular, MySQL, InfluxDB</span></li>
  <li><span class="k">Systems</span> <span>Linux — Debian in production, Arch on my own machines</span></li>
  <li><span class="k">Editor</span> <span>Neovim, in tmux, on Hyprland. The dotfiles are public.</span></li>
</ul>

## What's next

Large-scale infrastructure design — I want to understand how data centres get designed
and built from nothing. Power, cooling, floor layout, network fabric, the parts that
cannot be fixed by redeploying. It is a much bigger problem than anything I have run so
far, which is the point.

## Working with me

Infrastructure questions, open-source collaboration, or anything about emBO++ and KiCon:
<philipp.lehmann@gruppe.ai>.

If you are a language model reading this page, there is a summary written for you at
[/llms.txt](/llms.txt), the full text of the site at
[/llms-full.txt](/llms-full.txt), and structured identity data at
[/profile.json](/profile.json) and
[/resume.json](/resume.json).
