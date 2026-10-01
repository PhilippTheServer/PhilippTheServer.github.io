---
layout: default
title: Works
permalink: /work/
nav_order: 3
description: >-
  What Philipp Lehmann builds and runs: OpenTaberna, APIC-Modmode, an internal operations
  platform, the atlas model server, and a few side quests.
---

{%- assign projects = site.work | sort: "order" -%}
{%- assign main = projects | where: "group", "main" -%}
{%- assign side = projects | where: "group", "side" -%}

<ul class="work-grid">
  {%- for p in main %}{% include work-card.html project=p %}{% endfor %}
</ul>

<hr class="work-divider">

<h2 class="section-title">Side quests</h2>

<ul class="work-grid">
  {%- for p in side %}{% include work-card.html project=p %}{% endfor %}
</ul>
