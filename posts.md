---
layout: default
title: Writing
subtitle: What I have learned running this, written down before I forget it.
permalink: /posts/
description: >-
  Articles by Philipp Lehmann on infrastructure as code, Kubernetes, Ceph
  storage, monitoring, overlay VPNs, identity management, and running language
  models on your own hardware.
---

Most of these started as something that broke, or as an argument I kept having. I write
them down because the second time I hit the same problem I would rather read than
re-derive.

<ul class="post-list">
  {%- for post in site.posts %}
  <li class="post-entry">
    <h2><a href="{{ post.url | relative_url }}">{{ post.title }}</a></h2>
    <p class="post-entry-meta">
      <time datetime="{{ post.date | date_to_xmlschema }}">{{ post.date | date: "%-d %B %Y" }}</time>
      {%- if post.tags and post.tags.size > 0 %}
      <span class="post-tags">{% for tag in post.tags %}<span>{{ tag }}</span>{% endfor %}</span>
      {%- endif %}
    </p>
    <p>{{ post.description }}</p>
  </li>
  {%- endfor %}
</ul>

There is an [Atom feed]({{ '/feed.xml' | relative_url }}) if you would rather not check
back, and the full text of everything here is in one file at
[/llms-full.txt]({{ '/llms-full.txt' | relative_url }}).
