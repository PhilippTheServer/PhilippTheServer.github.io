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
`resume.json` stop being valid JSON, if the embedded JSON-LD drifts away from
`profile.json`, if the ORCID iD disappears from any file that must carry it,
if the landing page loses the structure that makes it readable (hero, named
sections including OpenTaberna, carded articles, fact list — see `check_site.rb`), if the subscribe
callout that tells readers how to put `/feed.xml` into a reader stops rendering on
the landing page, or stops sitting above the heading on `/posts/`, if an article's
meta description falls outside 70–160 characters (Google cuts the snippet
there), if an article from #117 on has a title over 60 characters, if the
OpenTaberna articles stop naming the project in their structured data, if the daily
article stops naming the self-hosted Qwen on atlas that mostly operates daily, if
`profile.json` or `resume.json` stop listing him as a founder of OpenTaberna, if the
pixel repair crew animation leaves the landing page or appears on any other page, if
it stops being the top layer or stops letting clicks through, if
the animation runs below 700 px width, if it
restarts on a height-only resize (a browser scrolling past its address
bar — `scripts/pixel-crew.test.mjs`, run with `node --test`), or if `CNAME` stops
naming the canonical domain.

`scripts/build-local.sh` runs the Node test on the host, because the `ruby:3.3`
image it builds in has no Node.

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
sets the canonical link and backdates it. Pasting the URL there is the one manual step.

When a deploy that adds a post succeeds, `.github/workflows/medium-import.yml` opens an
issue labelled `medium` with the article URL and the import steps, and never opens a
second one for the same URL. Closing the issue marks the article as imported.
`scripts/post-urls.sh` derives the URL from the filename; `verify.sh` fails if any URL
it derives has no built page.

## Machine-readable files

| Path | What it is |
| --- | --- |
| `/llms.txt` | Summary of who I am and what is on this site, for language models |
| `/llms-full.txt` | Full text of every page, in one file |
| `/profile.json` | schema.org `Person` JSON-LD — also embedded in every page's `<head>` |
| `/resume.json` | [JSON Resume](https://jsonresume.org) v1 |
| `/ai.txt` | Crawler and training policy |
| `/humans.txt` | The people and tools behind the site |
| `/feed.xml` | Atom feed |
| `/sitemap.xml` | Sitemap |
| `/impressum/` | Legal notice (Angaben gemäß § 5 DDG), in German |

`profile.json`, `resume.json` and the embedded JSON-LD are all generated from
`_data/person.yml` and `_data/resume.yml`, so they cannot disagree.
