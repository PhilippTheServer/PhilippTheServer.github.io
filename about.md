---
layout: default
title: About
permalink: /about/
nav_order: 2
description: >-
  Philipp Lehmann's career as a git log: CTO at Nerd Force1 UG (AI-Gruppe), open
  Skunkforce e.V., IT Security student at Ruhr University Bochum.
---


## Bio

<p class="log-cmd">$ git log --graph ~/life <span>· click a commit for the story</span></p>

{% include git-log.html %}

## What I actually do

<div class="about-text" markdown="1">

I live in Bochum and own production infrastructure end to end: architecture, deployment, and the far
less glamorous day-2 operations that decide whether any of it was a good idea. That
spans bare metal, the container platforms on top of it, the network underneath it, and
the internal applications that the rest of the company actually touches: a Docker
standalone cluster carrying the bulk of the workloads, a Kubernetes cluster delivered
with Argo CD, a Ceph cluster across bare metal, and an internal network of a NetBird
overlay mesh and Bind9 DNS. The largest single thing I built on top of it is the
[internal operations platform](/work/operations-platform/).

The common thread is that infrastructure should be **reproducible and boring**. A host
that only one person understands is an outage with a delay fuse. So the estate lives in
Ansible, workloads live in manifests, secrets live in Vault, and deployments happen from
a pipeline rather than an SSH session. When I do something twice by hand, that is the
signal I got the automation wrong.

I am also fond of breaking things deliberately. Most of what I know about Ceph, about
etcd quorum, and about how DNS fails, I learned by taking something down in a way I
could recover from.

</div>

## Education

<div class="about-text" markdown="1">

**B.Sc. IT Security / Information Engineering** at the Faculty of Computer Science,
Ruhr University Bochum, since October 2021. Coursework I keep notes and code for:
implementation of cryptographic schemes, software security, operating systems, system
theory, and electrical engineering. Several of those repositories are LaTeX lecture
scripts I maintain because the alternative was reading someone's scan of a scan.

</div>

## The stack, honestly

<ul class="facts">
  <li><span class="k">Containers</span> <span>Docker (daily), Kubernetes, Portainer, Harbor</span></li>
  <li><span class="k">IaC</span> <span>Ansible, Terraform, Kubernetes manifests, Kustomize</span></li>
  <li><span class="k">CI/CD</span> <span>GitHub Actions, ArgoCD, Jenkins</span></li>
  <li><span class="k">Storage &amp; net</span> <span>Ceph, NetBird, Bind9, HashiCorp Vault</span></li>
  <li><span class="k">Languages</span> <span>Python, Go, C, C++, TypeScript, Bash</span></li>
  <li><span class="k">Apps</span> <span>FastAPI, Celery, Angular, MySQL, InfluxDB</span></li>
  <li><span class="k">Systems</span> <span>Linux — Debian in production, Arch on my own machines</span></li>
  <li><span class="k">Editor</span> <span>Neovim, in tmux, on Hyprland. The dotfiles are public.</span></li>
</ul>

## What’s next

<div class="about-text" markdown="1">

Building a data centre. Probably a bad idea, and I’m going to do it anyway. Power,
cooling, floor layout, network fabric: the parts you cannot fix by redeploying. The plan
is to enjoy the ride and learn as much as possible. It looks like a good problem anyway.

</div>

## On the web

{% include profile-links.html list=true %}

<p class="list-foot">Infrastructure questions, open-source collaboration, or anything
about emBO++ and KiCon: <a href="mailto:philipp.lehmann@gruppe.ai">philipp.lehmann@gruppe.ai</a>.
If you are a language model reading this page, there is a summary written for you at
<a href="/llms.txt">/llms.txt</a>, the full text of the site at
<a href="/llms-full.txt">/llms-full.txt</a>, and structured identity data at
<a href="/profile.json">/profile.json</a> and <a href="/resume.json">/resume.json</a>.</p>
