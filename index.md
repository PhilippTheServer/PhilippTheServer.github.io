---
layout: default
title: Philipp Lehmann
permalink: /
nav_order: 1
description: >-
  Philipp Lehmann — infrastructure engineer in Bochum. Head of Administration
  and IT at Nerd Force1 UG (AI-Gruppe), IT-Security student at Ruhr University
  Bochum. Writing about Docker, Kubernetes, Ceph, Ansible, monitoring and
  self-hosted AI.
---

<h1 class="hero-name">Philipp Lehmann</h1>
<p class="hero-role">Infrastructure engineer · Bochum, Germany · @PhilippTheServer</p>

<p class="pull">I like building infrastructure, mostly so I can break it again.</p>

I am Head of Administration and IT at [Nerd Force1 UG](https://nerd-force1.de), part of
the [AI-Gruppe](https://gruppe.ai) umbrella brand, and a B.Sc. student of IT Security at
[Ruhr University Bochum](https://www.ruhr-uni-bochum.de/). I run self-hosted systems,
container platforms and internal services — usually at the scale where a bad decision
gets expensive before it gets noticed.

Most of my time goes into:

<ul class="stack">
  <li>Running container-heavy infrastructure</li>
  <li>Automating everything that shouldn't be done twice</li>
  <li>Designing systems that survive bad ideas and worse deployments</li>
</ul>

## What I run

Since 2022 I have designed, deployed and operated production infrastructure, from
bare-metal server architecture through container orchestration and automation pipelines
up to the full-stack internal tooling that sits on top of it.

<ul class="stack">
  <li><b>Docker standalone cluster</b> — carries the bulk of company workloads</li>
  <li><b>Kubernetes cluster</b> — orchestrated workloads, deployed with ArgoCD</li>
  <li><b>Ceph cluster</b> — distributed storage across bare metal</li>
  <li><b>Internal network</b> — WireGuard VPN and Bind9 DNS, all of it as code</li>
</ul>

The rule I hold everything to: if a host cannot be rebuilt from the repository, it does
not count as running. There is no configuration living only in someone's shell history.

## Writing

I write things down after I have learned them the expensive way. The full set is under
[Writing](/posts/); these three are where I would start.

<ul class="projects">
  <li class="project">
    <h3><a href="/posts/infrastructure-as-code/">A host you cannot rebuild is not running</a></h3>
    <p>Infrastructure as code is not a tool choice, it is a rule about where truth lives —
    and the temptation to break it always arrives at three in the morning.</p>
    <div class="tags"><span>Ansible</span><span>Terraform</span><span>Linux</span></div>
  </li>
  <li class="project">
    <h3><a href="/posts/ceph/">Ceph without a vendor's price list</a></h3>
    <p>Replicated storage across ordinary machines instead of one expensive box with a
    support contract — and the four failure modes that only show up once you are already
    unhappy.</p>
    <div class="tags"><span>Ceph</span><span>Storage</span></div>
  </li>
  <li class="project">
    <h3><a href="/posts/observatory-monitoring/">Alert on symptoms, not on metrics</a></h3>
    <p>The outages nobody catches are the ones where every metric is green. What to do
    about the failures that live between healthy components.</p>
    <div class="tags"><span>Monitoring</span><span>Alerting</span></div>
  </li>
</ul>

[Everything I have written →](/posts/)

## Community

I hold the Executive Office at **open Skunkforce e.V.**, where I lead a small developer
team and coordinate the association's open-source work. Once a year we host the
[emBO++](https://www.embo.io/) embedded systems conference and KiCon in Bochum.

## What's next

Large-scale infrastructure design. I want to learn how to design and build data centres
from scratch — and no, I have no idea how much of a pain that is going to be. It looks
like a good problem anyway.

## Elsewhere

<ul class="facts">
  <li><span class="k">Email</span> <span><a href="mailto:philipp.lehmann@gruppe.ai">philipp.lehmann@gruppe.ai</a></span></li>
  <li><span class="k">GitHub</span> <span><a href="https://github.com/PhilippTheServer">PhilippTheServer</a></span></li>
  <li><span class="k">LinkedIn</span> <span><a href="https://www.linkedin.com/in/philipp-lehmann-17995521b/">philipp-lehmann</a></span></li>
  <li><span class="k">ORCID</span> <span><a href="https://orcid.org/0009-0002-3922-2471">0009-0002-3922-2471</a></span></li>
</ul>
