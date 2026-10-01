# philipptheserver.com

Personal site of Philipp Lehmann — infrastructure engineer, Bochum.
Built with Jekyll, deployed to GitHub Pages from `main`.

Canonical URL: <https://philipptheserver.com>

## Local development

```bash
./scripts/serve.sh        # http://localhost:4000, live reload
```

The script runs Jekyll in Docker, so no local Ruby install is needed.

## Verification

```bash
./scripts/verify.sh       # builds the site, then checks it
```

`verify.sh` is what CI runs on every push and pull request. It fails if the
build breaks, if an internal link or image is dead, if `profile.json` or
`resume.json` stop being valid JSON, if any of the machine-readable files below goes
missing or renders empty, if the embedded JSON-LD drifts away from
`profile.json`, if the home page loses its `WebSite` node, its `ProfilePage` node, its
`rel="me"` links to GitHub, ORCID and LinkedIn, its preview image or a title that leads
with the name and role, if the career at Nerd Force1 (system administrator from
2022-03-01, Head of Administration and IT from 2023, CTO since 2026-09-06) changes in
`resume.json` or loses a role in `llms.txt`, if a file links one of my GitHub
repositories that is not in the public list in `check_site.rb`,
if the ORCID iD disappears from any file that must carry it,
if the home page loses the name as its `<h1>`, the role line or a whoami naming
Nerd Force1, AI-Gruppe, Ruhr University Bochum and OpenTaberna, if the RSS row with
the feed URL and its copy button leaves the top of `/posts/`, if any page stops linking
the Impressum or loads a script, stylesheet, font or image from another origin, if the
site stops being dark only, if a Work project loses its page, its structured data, its
place in the `/work/` `ItemList` or in `resume.json`, if the git log on `/about/`
disagrees with `resume.json` or hides a story from the HTML, if an article's
meta description falls outside 70–160 characters (Google cuts the snippet
there), if an article from #117 on has a title over 60 characters, if the
OpenTaberna articles stop naming the project in their structured data, if the daily
article stops naming the self-hosted Qwen on atlas that mostly operates daily, if
`profile.json` or `resume.json` stop listing him as a founder of OpenTaberna, if the
pixel repair crew animation leaves the landing page or appears on any other page, if
it stops being the top layer or stops letting clicks through, if the home footer stops
leaving room for it, if
the animation runs below 700 px width, if it
restarts on a height-only resize (a browser scrolling past its address
bar — `scripts/pixel-crew.test.mjs`, run with `node --test`), if a code block in an
article's source holds a fence as long as its own (meant to nest, it ends the block
instead and turns the rest of the article inside out; the outer fence has to be longer),
or if `CNAME` stops naming the canonical domain.

`scripts/build-local.sh` runs the Node test on the host, because the `ruby:3.3`
image it builds in has no Node, and after the build `scripts/medium/check.sh`, which
needs uv (see Medium below).

## Design

Dark only, after the design handoff of 2026-10-01 (#74; spec in
`docs/superpowers/specs/2026-10-01-site-redesign-design.md`). The colour tokens are the
custom properties at the top of `assets/css/site.css`; `assets/css/prose.css` sets an
article's body from them. Body text uses the system font stack, articles Inter, code and
list markers mononoki. Both fonts are served from `assets/fonts/` with their OFL licences
beside them, so no reader's address goes to Google Fonts or a CDN.

| Where | What renders it |
| --- | --- |
| Header, footer, page title | `_layouts/default.html` |
| `/` | `index.md`, `_includes/profile-links.html` |
| `/posts/`, `/tags/<tag>/` | `posts.md`, `_plugins/tag_pages.rb`, `_includes/post-list.html`, `_includes/rss-row.html` |
| Tag labels | `_includes/tag-label.html`; the hue comes from `_plugins/tag_hue.rb` (a hash of the name) |
| An article | `_layouts/post.html` |
| `/work/`, `/work/<id>/` | `work.md`, `_layouts/work.html`, one file per project in `_work/` |
| `/about/` | `about.md`; the git log is `_data/career.yml` through `_includes/git-log.html` |

A project is a file in `_work/` whose front matter holds `title`, `order`, `group`
(`main` or `side`), `year`, `role`, `stack`, `status`, `website`, `repo` (a public
repository only), `description`, `highlights` and `posts` (article slugs). An `image` in
`assets/work/` shows on the card and the project page; without one the card is text.
Every project must also appear in `resume.yml`, under `projects` or `work`.

`assets/js/site.js` copies the feed URL, answers the Konami code and leaves a coffee in
the console. Nothing on the site depends on it.

## Pixel repair crew

The landing page draws a small pixel-art scene along the bottom of the viewport: a
worker hammers a brick server apart and rebuilds it. It is `assets/js/pixel-crew.js`
drawing on a `<canvas id="pixel-bg">`, with no libraries or images. A page gets it
by setting `pixel_crew: true` in its front matter; only `index.md` does. The
`CONFIG` block at the top of the script sets pixel size, where the server stands,
walking speed and the number of bricks. Under `prefers-reduced-motion: reduce` it
draws one still frame. It is drawn on top of the page text, and clicks and text
selection pass through it. Below 700 px viewport width (phones in portrait) there is
no animation: the script draws nothing and runs no frame loop, and `site.css` hides
the canvas at the same width.

## Medium

Articles are cross-posted to Medium as drafts whose canonical link points back here, so
this site stays the source of truth. Medium issues no new API tokens, so this goes through
Medium's "Import a story" tool (<https://medium.com/p/import>), which creates the draft,
sets the canonical link and backdates it. The import alone is not a usable draft, so
`scripts/medium/medium.py` finishes it in a real browser (#68):

```bash
uv run scripts/medium/medium.py browser          # start the automation Chrome; sign in to Medium once
uv run scripts/medium/medium.py sync SLUG        # import or repair the draft until it matches the site
uv run scripts/medium/medium.py verify --all     # compare every draft with the site, change nothing
```

`sync` imports the article (or finds its draft), replaces the imported body with the
built page's article HTML, pastes every code block's exact text, deletes the empty blocks
the import adds, makes sub-headings Medium's small heading, sets every code language, and
sets the preview subtitle, SEO title, SEO description and five topics (the tag-to-topic
map is in `site_model.py`). It then reloads and compares every block with the site: type,
text, code byte for byte, language, and the text of links, inline code, bold and italic.
It works on drafts only: a published story is never edited. For those, `sync` and `verify`
check instead that the story's canonical link is exactly the article's URL, and for drafts
that the "Originally published at" footer links to it (#70). Medium sets both from the URL
it imported and offers no way to change them, so a wrong one means deleting the story and
running `sync` again. Which story belongs to which article is kept in
`~/.local/state/medium-sync/state.json`. Publishing stays a click on Medium.

The browser is Google Chrome from Google's apt repository, with its own profile in
`~/.local/share/medium-import-browser`: Ubuntu's AppArmor only lets the packaged Chrome
use its sandbox, not Playwright's Chrome for Testing. Leave the window alone while a sync
runs; editing the same draft elsewhere makes Medium reject the script's saves. A sync
takes one to two minutes per article. `scripts/medium/check.sh` lints the tooling with
ruff and tests the site-to-Medium model with pytest; CI runs it after the site build.

When a deploy that adds a post succeeds, `.github/workflows/medium-import.yml` opens an
issue labelled `medium` with the article URL and the `sync` command, and never opens a
second one for the same URL. Closing the issue marks the article as done.
`scripts/post-urls.sh` derives the URL from the filename; `verify.sh` fails if any URL
it derives has no built page.

Code blocks are served in the shape the importer keeps, found by importing articles and
comparing the drafts: a bare `<pre class="language-x" data-lang="x">` with no `<code>`
inside, `<br>` for each line break, and nothing between `</pre>` and the next tag. The
importer drops server-side highlighting spans, collapses runs of spaces and `&nbsp;` alike,
drops tabs, splits a block at an empty line, and drops a block that ends in `<br>`. Figure
spaces (U+2007) survive, so a space at the start of a line or next to other whitespace is
written as a figure space, a tab as a word joiner (U+2060) plus four figure spaces, and an
empty line as a single `&nbsp;`. On the site, `assets/js/highlight.js` restores the exact
text (no code source contains those characters) and highlights it with highlight.js
11.12.0 (BSD-3, vendored under `assets/vendor/highlight.js/` so no reader's address goes
to a CDN; grammars beyond its common bundle are listed in `_config.yml` as
`highlight_extra_languages`). `check_site.rb` fails if a post's code block carries
highlighting spans, a `<code>` element, a raw newline, an empty line, whitespace the
importer would collapse, a trailing `<br>` or a literal U+2007, U+2060 or U+00A0, has
attributes other than a matching `class` and `data-lang`, is followed by whitespace, or the
page stops loading highlight.js.

What the import does wrong, and `sync` repairs: Medium caches each article's first import
(neither a query string on the URL nor deleting the draft and importing again gets a
fresh copy); it adds an empty heading after every heading and an empty code block after
code; it makes every sub-heading a large heading; it guesses code languages, often wrong;
it keeps the figure spaces, so code copied from an imported draft would not run; and it
stores a tab as one space. What Medium cannot hold at all: tables (each becomes a
plain-text code block with aligned columns), italics in headings (dropped), code inside a
list item (the list is split around it), and code languages it does not offer, such as
Jinja, Dockerfile, nginx and HCL (shown as Plain Text). Tabs are expanded to the site's
tab width of 2.

## Machine-readable files

| Path | What it is |
| --- | --- |
| `/llms.txt` | Summary of who I am and what is on this site, for language models |
| `/llms-full.txt` | Full text of every page and project, in one file |
| `/profile.json` | schema.org `Person` JSON-LD — also embedded in every page's `<head>` |
| `/resume.json` | [JSON Resume](https://jsonresume.org) v1 |
| `/ai.txt` | Crawler and training policy |
| `/humans.txt` | The people and tools behind the site |
| `/feed.xml` | Atom feed |
| `/sitemap.xml` | Sitemap |
| `/impressum/` | Legal notice (Angaben gemäß § 5 DDG), in German |

`profile.json`, `resume.json` and the embedded JSON-LD are all generated from
`_data/person.yml` and `_data/resume.yml`, so they cannot disagree. The role and project
lists in `llms.txt` are rendered from `_data/resume.yml` too; its prose is written by hand.

Besides the Person node in every page, the home page carries a `WebSite` node (the site
name search engines show) and the home and about pages a `ProfilePage` node whose
`mainEntity` is the Person node, referenced by `@id`. Articles carry `BlogPosting`, the
article index `Blog`. Only public repositories are linked: a private one is a 404 for
whoever follows it.
