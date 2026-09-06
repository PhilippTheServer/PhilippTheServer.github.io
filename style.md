---
layout: post
title: "Style reference"
subtitle: "Every component an article can contain, on one page."
date: 2026-09-06 12:00:00 +0200
permalink: /style/
sitemap: false
tags: [documentation]
description: >-
  The rendering reference for philipptheserver.com: every Markdown construct an
  article can use, shown once, so the article stylesheet can be reviewed and a
  regression can be seen rather than guessed at.
---

## The problem

A stylesheet is only as good as the components you remembered to look at. Every element
below appeared in an article before it appeared in `prose.css`, which is the wrong order —
the code blocks on this site rendered as a stack of individually-boxed lines for a while
because nothing exercised them next to everything else.

This page is the fix: one place where each construct is rendered once, so the styling has
somewhere to be wrong visibly.

## Working through it

### Headings

Second-level headings carry a rule under them, because they are the structural divisions of
an article. Everything below is progressively quieter.

#### Fourth level

##### Fifth level, set as a label

### Text

Ordinary prose, with **bold**, *italic*, ***both***, ~~struck through~~, `inline code`,
<mark>highlighted</mark>, an <abbr title="Hypertext Markup Language">abbreviation</abbr>,
and a [link](/posts/). Press <kbd>Ctrl</kbd> + <kbd>C</kbd> to stop it.

A second paragraph, so the spacing between paragraphs is visible rather than theoretical.

> A blockquote. Used for something said elsewhere, or for the point an article is arguing
> against before it argues with it.
>
> It can run to more than one paragraph.

### Lists

- An unordered item
- Another, with `code` in it
  - A nested item
  - Another nested one
    - And a third level
- A final item

1. First, ordered
2. Second
   1. Nested and ordered
   2. Still nested
3. Third

Definition lists, which kramdown produces from a term and an indented colon:

Idempotence
: Running the thing twice leaves the same result as running it once.

Drift
: The distance between what the repository says and what the machine does.

### Code

Inline `code` sits in a small box. A fenced block gets one box around the whole thing, its
own horizontal scroll, and a language label in the corner:

```yaml
# docker-compose.yml — the shape this page exists to check
services:
  web:
    image: nginx:1.27-alpine
    ports: ["8080:80"]
    healthcheck:
      test: ["CMD", "wget", "-qO-", "http://localhost/"]
      interval: 2s
      retries: 10
    environment:
      LOG_LEVEL: "info"      # a comment, a string, a number below
      WORKERS: 4
```

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class Record:
    """A docstring, so the comment and string colours differ visibly."""

    name: str
    count: int = 0

    def merged(self, other: "Record") -> "Record":
        if self.name != other.name:
            raise ValueError(f"cannot merge {self.name!r} with {other.name!r}")
        return Record(self.name, self.count + other.count)
```

```go
package main

import (
	"errors"
	"fmt"
)

var ErrEmpty = errors.New("nothing to roll up")

func worst(statuses []string) (string, error) {
	if len(statuses) == 0 {
		return "", ErrEmpty
	}
	order := map[string]int{"ok": 0, "warn": 1, "crit": 2}
	out := statuses[0]
	for _, s := range statuses[1:] {
		if order[s] > order[out] {
			out = s
		}
	}
	return fmt.Sprintf("%s", out), nil
}
```

```bash
# A shell block, with a prompt, a pipeline and a heredoc
set -euo pipefail

curl -sf http://localhost:8080/healthz \
  | jq -r '.status' \
  | grep -qx 'ok' || { echo "not healthy" >&2; exit 1; }

cat <<'EOF' > /tmp/note
Quoted heredocs do not expand $VARIABLES.
EOF
```

```sql
SELECT date_trunc('day', timezone('Europe/Berlin', created_at)) AS day,
       currency,
       sum(total_cents) / 100.0 AS revenue
  FROM orders
 WHERE status IN ('paid', 'shipped')
 GROUP BY day, currency
 ORDER BY day DESC
 LIMIT 30;
```

```diff
 services:
   web:
-    image: nginx:latest
+    image: nginx:1.27-alpine
     ports: ["8080:80"]
```

A block with no language declared gets the box and the scroll, and no label:

```
plain text, no highlighting, and a deliberately long line to prove the block scrolls inside itself instead of widening the page it sits on
```

### Tables

| Component | Where it is styled | Scrolls |
| --- | --- | ---: |
| Code block | `prose.css`, `.highlight` | yes |
| Table | `prose.css`, `.table-scroll` | yes |
| Blockquote | `prose.css`, `blockquote` | no |
| Inline code | `prose.css`, `:not(pre) > code` | no |

### Rules and footnotes

A horizontal rule separates sections that are not worth a heading:

---

A footnote reference looks like this[^why], and its text lands at the end of the article.

[^why]: Footnotes are worth styling because kramdown produces them whether or not anyone
    planned for it, and an unstyled footnote block looks like a mistake.

## The solution

`assets/css/prose.css` holds every rule above, scoped to `.prose` so none of it can leak
into the navigation, the article index or the tag pages. Colours come from the tokens in
`site.css`, so light and dark are one definition rather than two.

Two things Markdown does not produce on its own are added by `_plugins/prose_markup.rb`
after conversion: `data-lang` on a code block, copied from the class Rouge already writes,
so one CSS rule labels every language there will ever be; and a scroll container around
every table, because otherwise the only thing that can scroll sideways is the page.

## Conclusion

The rule this page enforces is that **a component is styled once, in one file, and looked
at at least once**. The boxed-per-line code blocks survived 108 articles because the CSS
was written for a page that had no code on it, and nothing ever put the two side by side.

Anything added to an article that is not on this page is, by definition, not yet designed —
so add it here first.
