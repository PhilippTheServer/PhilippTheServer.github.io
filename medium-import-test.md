---
layout: post
title: "Medium import test"
subtitle: "The same code block in four markups, to find the one Medium's importer keeps."
date: 2026-09-29 12:00:00 +0200
permalink: /medium-import-test/
sitemap: false
description: >-
  Temporary test page for issue 56: which code block markup survives Medium's Import a
  story tool. Not an article.
---

This page exists to answer one question: which HTML form of a code block survives Medium's
"Import a story" tool. The same four-line Python function appears below in four different
markups. Each copy carries a comment naming its variant, so an imported draft shows at a
glance which forms Medium kept as code blocks, which it flattened into paragraphs, and
which it dropped entirely.

The function itself is the one from the article on meal tracking: it normalises a food name
so that differently spaced or capitalised names of the same food compare equal. It is short
enough to read in every variant and long enough to show whether indentation and line breaks
survive, which is the part of a code block most likely to be lost when an importer treats it
as ordinary text.

A table follows the four variants, because Medium has no table element and its importer has
to do something with one. What it does is worth knowing before importing articles that rely
on tables.

## Variant A: today's Rouge markup

```python
# variant A survived
def food_key(name: str) -> str:
    return " ".join(name.casefold().split())
```

## Variant B: pre and code with highlight spans, no wrapping divs

<pre class="highlight language-python"><code><span class="c1"># variant B survived</span>
<span class="k">def</span> <span class="nf">food_key</span><span class="p">(</span><span class="n">name</span><span class="p">:</span> <span class="nb">str</span><span class="p">)</span> <span class="o">-&gt;</span> <span class="nb">str</span><span class="p">:</span>
    <span class="k">return</span> <span class="s">" "</span><span class="p">.</span><span class="nf">join</span><span class="p">(</span><span class="n">name</span><span class="p">.</span><span class="nf">casefold</span><span class="p">().</span><span class="nf">split</span><span class="p">())</span></code></pre>

## Variant C: pre and code, plain text

<pre><code class="language-python"># variant C survived
def food_key(name: str) -&gt; str:
    return " ".join(name.casefold().split())</code></pre>

## Variant D: bare pre

<pre># variant D survived
def food_key(name: str) -&gt; str:
    return " ".join(name.casefold().split())</pre>

## Table

| Variant | Markup |
| --- | --- |
| A | `div > div > pre > code > span` |
| B | `pre > code > span` |
| C | `pre > code` |
| D | `pre` |
