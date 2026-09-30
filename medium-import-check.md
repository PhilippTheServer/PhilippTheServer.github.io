---
layout: post
title: "Medium import check"
subtitle: "Code block variants for issue 56. Not an article; removed once the import is understood."
date: 2026-09-28 12:00:00 +0200
permalink: /medium-import-check/
sitemap: false
description: >-
  Temporary check page for issue 56: code block markup variants for Medium's Import a story
  tool. Not an article.
---

This page holds a handful of code block variants for one purpose: to see, from a single
import into Medium, which markup keeps its indentation, which keeps its tabs, and which one
does not leave an empty code block behind. Each block names its variant in its first line,
and a short paragraph follows every block, so an empty block can be traced to the block
before it.

Variant A goes through the site's real Markdown pipeline and is what every article ships
today. The others are written by hand, each changing one thing, so that whatever differs in
the imported draft can be pinned to that one change.

```python
# variant A: the pipeline, as every article ships today
def f(x):
    if x:
        return 1

    return 0
```

After variant A.

{::nomarkdown}<pre># variant B: bare pre, no attributes<br>def f(x):<br>&nbsp;&nbsp;&nbsp;&nbsp;if x:<br>&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;return 1<br>&nbsp;<br>&nbsp;&nbsp;&nbsp;&nbsp;return 0</pre><p>After variant B.</p>{:/}

{::nomarkdown}<pre><span># variant C: text in a span, like Medium's own markup<br>def f(x):<br>&nbsp;&nbsp;&nbsp;&nbsp;if x:<br>&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;return 1<br>&nbsp;<br>&nbsp;&nbsp;&nbsp;&nbsp;return 0</span></pre><p>After variant C.</p>{:/}

{::nomarkdown}<pre># variant D: trailing br<br>def f(x):<br>&nbsp;&nbsp;&nbsp;&nbsp;if x:<br>&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;return 1<br>&nbsp;<br>&nbsp;&nbsp;&nbsp;&nbsp;return 0<br></pre><p>After variant D.</p>{:/}

{::nomarkdown}<pre>// variant T1: each tab as four nbsp<br>func f() {<br>&nbsp;&nbsp;&nbsp;&nbsp;if true {<br>&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;return<br>&nbsp;&nbsp;&nbsp;&nbsp;}<br>&nbsp;<br>&nbsp;&nbsp;&nbsp;&nbsp;x := []int{1,&nbsp;&nbsp;2}<br>}</pre><p>After variant T1.</p>{:/}

{::nomarkdown}<pre>// variant T2: each tab as four figure spaces<br>func f() {<br>&#8199;&#8199;&#8199;&#8199;if true {<br>&#8199;&#8199;&#8199;&#8199;&#8199;&#8199;&#8199;&#8199;return<br>&#8199;&#8199;&#8199;&#8199;}<br>&nbsp;<br>&#8199;&#8199;&#8199;&#8199;x := []int{1,&nbsp;&nbsp;2}<br>}</pre><p>After variant T2.</p>{:/}

That is all of them. The draft Medium makes from this page is compared block by block with
the markup above, and the site then ships whichever form survives intact.
