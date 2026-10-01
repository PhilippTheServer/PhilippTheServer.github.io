---
layout: default
title: Blog
permalink: /posts/
featured_tags: [kubernetes, ceph, docker, ansible, observability, security, llm, architecture]
description: >-
  Articles by Philipp Lehmann on infrastructure as code, Kubernetes, Ceph, monitoring,
  overlay networks and running language models on your own hardware.
---

{% include rss-row.html %}

<ul class="tag-chips" aria-label="Topics">
  <li><a href="{{ '/posts/' | relative_url }}" aria-current="page">everything</a></li>
  {%- for tag in page.featured_tags %}
  <li><a href="{{ '/tags/' | append: tag | append: '/' | relative_url }}">#{{ tag }}</a></li>
  {%- endfor %}
  <li><a href="{{ '/tags/' | relative_url }}">all topics</a></li>
</ul>

{% include post-list.html posts=site.posts %}

<p class="list-foot">The full text of everything here is in one file at
<a href="{{ '/llms-full.txt' | relative_url }}">/llms-full.txt</a>.</p>
