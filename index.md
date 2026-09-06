---
layout: default
title: Philipp Lehmann
permalink: /
nav_order: 1
description: >-
  Philipp Lehmann — infrastructure engineer in Bochum. Head of Administration
  and IT at Nerd Force1 UG (AI-Gruppe), IT-Security student at Ruhr University
  Bochum. Docker, Kubernetes, Ceph, Ansible, and the internal tooling on top.
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

## Selected work

<ul class="projects">
  <li class="project">
    <h3>Internal Operations Platform</h3>
    <p>The company's own platform, which centralised operations and replaced several
    external tools: internal messenger, time tracking, tickets, accounting, Docker
    registry frontend, DNS management, WireGuard management and hosting orchestration.</p>
    <div class="tags"><span>Angular</span><span>FastAPI</span><span>Celery</span><span>MySQL</span><span>InfluxDB</span></div>
  </li>
  <li class="project">
    <h3><a href="https://github.com/PhilippTheServer/atlas">atlas</a></h3>
    <p>Self-hosted model serving — an OpenAI-compatible API, repeatable capability
    benchmarks for the locally served models, and a metrics poller. Because sending
    everything to someone else's GPU is a decision, not a default.</p>
    <div class="tags"><span>Python</span><span>FastAPI</span><span>llama-swap</span><span>uv</span></div>
  </li>
  <li class="project">
    <h3><a href="https://github.com/PhilippTheServer/Server-Administration_ansible">Server-Administration</a></h3>
    <p>The company's server infrastructure as code. The reason a rebuild is a pipeline
    run rather than an afternoon.</p>
    <div class="tags"><span>Ansible</span><span>Linux</span></div>
  </li>
</ul>

[All projects →](/projects/)

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
