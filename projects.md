---
layout: default
title: Projects
subtitle: What I build when nothing is on fire.
permalink: /projects/
nav_order: 3
description: >-
  Projects by Philipp Lehmann — infrastructure as code, self-hosted model serving,
  network monitoring in Go, FastAPI services and Angular frontends, plus the
  lecture notes and dotfiles that come with being a student.
---

Everything below is public on [GitHub](https://github.com/PhilippTheServer). The
company's internal operations platform is described on the [about page](/about/)
but is not open source.

## Infrastructure

<ul class="projects">
  <li class="project">
    <h3><a href="https://github.com/PhilippTheServer/Server-Administration_ansible">Server-Administration</a></h3>
    <p>The company's server infrastructure as code. Roles, playbooks and inventory for
    the whole estate, so a host is something you rebuild rather than something you
    remember.</p>
    <div class="tags"><span>Ansible</span><span>Shell</span><span>Linux</span></div>
  </li>
  <li class="project">
    <h3><a href="https://github.com/PhilippTheServer/k8s-prod-cluster">k8s-prod-cluster</a></h3>
    <p>Notes and manifests for the production Kubernetes cluster — the wiki I wanted to
    exist while I was building it.</p>
    <div class="tags"><span>Kubernetes</span><span>Helm</span><span>ArgoCD</span></div>
  </li>
  <li class="project">
    <h3><a href="https://github.com/PhilippTheServer/homelab">homelab</a></h3>
    <p>The machines at home, defined the same way as the ones at work. Where new ideas
    get to fail cheaply first.</p>
    <div class="tags"><span>Ansible</span><span>Jinja</span><span>Docker</span></div>
  </li>
  <li class="project">
    <h3><a href="https://github.com/PhilippTheServer/IaC">IaC</a></h3>
    <p>An attempt at writing down what an ideal IT infrastructure looks like as code,
    rather than as an architecture diagram nobody updates.</p>
    <div class="tags"><span>Terraform</span><span>Ansible</span></div>
  </li>
  <li class="project">
    <h3><a href="https://github.com/PhilippTheServer/ceph-cluster-on-a-budget">ceph-cluster-on-a-budget</a></h3>
    <p>Talk on building a usable Ceph cluster without a storage vendor's price list.</p>
    <div class="tags"><span>Ceph</span><span>Talk</span></div>
  </li>
</ul>

## Self-hosted AI

<ul class="projects">
  <li class="project">
    <h3><a href="https://github.com/PhilippTheServer/atlas">atlas</a></h3>
    <p>Umbrella repository for the self-hosted model stack: documentation and references
    for every atlas component, consumed by the others as a submodule.</p>
    <div class="tags"><span>Python</span><span>Documentation</span></div>
  </li>
  <li class="project">
    <h3><a href="https://github.com/PhilippTheServer/atlas-api">atlas-api</a></h3>
    <p>Composable FastAPI service — uv, ruff, Docker, self-contained feature modules.
    The front door to the locally served models.</p>
    <div class="tags"><span>Python</span><span>FastAPI</span><span>uv</span><span>Docker</span></div>
  </li>
  <li class="project">
    <h3><a href="https://github.com/PhilippTheServer/atlas-bench">atlas-bench</a></h3>
    <p>Repeatable capability benchmarks for the models served by llama-swap on atlas.
    Because "it feels smarter" is not a measurement.</p>
    <div class="tags"><span>Python</span><span>Benchmarking</span></div>
  </li>
  <li class="project">
    <h3><a href="https://github.com/PhilippTheServer/atlas-poller">atlas-poller</a></h3>
    <p>Metrics poller for the atlas host.</p>
    <div class="tags"><span>Python</span><span>Metrics</span></div>
  </li>
  <li class="project">
    <h3><a href="https://github.com/PhilippTheServer/daedalus-agentic-coding-system">daedalus</a></h3>
    <p>A self-hosted agentic coding system, built to find out how much of the workflow
    can run on hardware I own.</p>
    <div class="tags"><span>Python</span><span>Agents</span></div>
  </li>
</ul>

## Monitoring

<ul class="projects">
  <li class="project">
    <h3><a href="https://github.com/PhilippTheServer/neteye-agent">neteye-agent</a></h3>
    <p>Network agent in Go, deployed on every node, collecting what the node can see
    that the centre cannot.</p>
    <div class="tags"><span>Go</span></div>
  </li>
  <li class="project">
    <h3><a href="https://github.com/PhilippTheServer/neteye-center">neteye-center</a></h3>
    <p>The aggregator the agents report to.</p>
    <div class="tags"><span>Go</span></div>
  </li>
  <li class="project">
    <h3><a href="https://github.com/PhilippTheServer/neteye-frontend">neteye-frontend</a></h3>
    <p>Angular frontend for the collected network metrics.</p>
    <div class="tags"><span>TypeScript</span><span>Angular</span></div>
  </li>
</ul>

## Services and tools

<ul class="projects">
  <li class="project">
    <h3><a href="https://github.com/PhilippTheServer/energy-data-api">energy-data-api</a></h3>
    <p>FastAPI service over the German SMARD electricity market data, with an
    <a href="https://github.com/PhilippTheServer/energy-data-frontend">Angular frontend</a>
    for looking at where the grid's power actually came from.</p>
    <div class="tags"><span>Python</span><span>FastAPI</span><span>Angular</span></div>
  </li>
  <li class="project">
    <h3><a href="https://github.com/PhilippTheServer/create_project_cli-tool">create_project_cli-tool</a></h3>
    <p>CLI that lays out a FastAPI or Angular repository, for people too lazy to build
    the same directory tree twice. I am the target audience.</p>
    <div class="tags"><span>Python</span><span>CLI</span></div>
  </li>
  <li class="project">
    <h3><a href="https://github.com/PhilippTheServer/docker-build-api">docker-build-api</a></h3>
    <p>A Go implementation of the Docker image build API.</p>
    <div class="tags"><span>Go</span><span>Docker</span></div>
  </li>
  <li class="project">
    <h3><a href="https://github.com/PhilippTheServer/gym-bro">gym-bro</a></h3>
    <p>Workout tracker — Angular PWA and FastAPI backend, deployed to the homelab via
    Harbor and Ansible. Small enough to be a good end-to-end test of the pipeline.</p>
    <div class="tags"><span>Python</span><span>Angular</span><span>PWA</span></div>
  </li>
  <li class="project">
    <h3><a href="https://github.com/PhilippTheServer/apic">apic</a></h3>
    <p>APIC ModMode shop — umbrella repository and documentation wiki, with a
    <a href="https://github.com/PhilippTheServer/apic_shop_backend">backend</a> and
    customer and admin frontends.</p>
    <div class="tags"><span>Python</span><span>TypeScript</span><span>Shop</span></div>
  </li>
</ul>

## Notes, configuration, learning

<ul class="projects">
  <li class="project">
    <h3>Lecture scripts</h3>
    <p>LaTeX notes I maintain for my own courses and share, because the alternative was
    a scan of a scan:
    <a href="https://github.com/PhilippTheServer/Systemtheorie">Systemtheorie</a>,
    <a href="https://github.com/PhilippTheServer/Elektrotechnik">Elektrotechnik</a>,
    <a href="https://github.com/PhilippTheServer/Mathe_Wafi">Mathematik / Wahrscheinlichkeit</a>,
    and a <a href="https://github.com/PhilippTheServer/LateX-Script-Template">template</a> for the next one.</p>
    <div class="tags"><span>TeX</span></div>
  </li>
  <li class="project">
    <h3>Coursework</h3>
    <p><a href="https://github.com/PhilippTheServer/Implementierung-Kryptographischer-Verfahren">Implementation of cryptographic schemes</a> in C,
    <a href="https://github.com/PhilippTheServer/Software-Security">software security</a>,
    <a href="https://github.com/PhilippTheServer/Betriebssysteme">operating systems</a>,
    and C++ learning by <a href="https://github.com/PhilippTheServer/game-of-life">writing a naive Game of Life</a>.</p>
    <div class="tags"><span>C</span><span>C++</span><span>Python</span></div>
  </li>
  <li class="project">
    <h3>Dotfiles</h3>
    <p>The environment, kept in git so a new machine is an afternoon and not a week:
    <a href="https://github.com/PhilippTheServer/neovim-dotfiles">Neovim</a>,
    <a href="https://github.com/PhilippTheServer/tmux-dotfiles">tmux</a>,
    <a href="https://github.com/PhilippTheServer/hyprland-dotfiles">Hyprland</a>,
    <a href="https://github.com/PhilippTheServer/alacritty-dotfiles">Alacritty</a>,
    plus <a href="https://github.com/PhilippTheServer/claude-config">Claude Code</a> and
    <a href="https://github.com/PhilippTheServer/opencode-setup">opencode</a> configuration.</p>
    <div class="tags"><span>Lua</span><span>Shell</span></div>
  </li>
</ul>
