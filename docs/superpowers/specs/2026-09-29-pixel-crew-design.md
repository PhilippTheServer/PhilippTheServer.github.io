# Pixel repair crew on the landing page — design

Date: 2026-09-29
Issue: #43

## Goal

The site reads as text only. Give the landing page some personality that matches the
tagline ("I like building infrastructure, mostly so I can break it again") without
touching how articles read.

## What it is

A pixel-art scene drawn on one `<canvas>` along the bottom of the viewport: a worker
walks up to a server built from five bricks, two of which blink red. He hits it three
times, it bursts apart, he carries each brick back, the lights go amber then green,
he cheers and walks off. After a pause it loops with different bricks failing.

It started as a standalone file (`pixel-repair-bg.html`). No libraries, no images:
everything is drawn in code.

## Where it appears

Only on pages whose front matter sets `pixel_crew: true` — that is `index.md` alone.
`_layouts/default.html` renders the canvas and the script tag behind that switch,
the same pattern as `subscribe_box`. Articles stay calm for reading.

## Files

| File | Change |
| --- | --- |
| `assets/js/pixel-crew.js` | the script, adapted (below) |
| `assets/css/site.css` | the `#pixel-bg` rule |
| `_layouts/default.html` | canvas + `<script defer>` when `page.pixel_crew` |
| `index.md` | `pixel_crew: true` |
| `README.md` | the switch and the new checks |
| `scripts/check_site.rb` | structural check |
| `scripts/pixel-crew.test.mjs` | behaviour test |
| `scripts/verify.sh`, `scripts/build-local.sh` | run the behaviour test |

## Changes from the standalone file

1. **The canvas is a strip, not the full viewport**, and the scene rebuilds only
   when the viewport *width* changes. Mobile browsers fire `resize` whenever the
   address bar shows or hides, which changes only the height; the original rebuilt
   the scene on every one of those, so on a phone the worker restarted while
   scrolling.
2. **The ground line takes its colour from `--rule`**, read from the computed style,
   so it shows on the light theme (the original was white at 10 % opacity, invisible
   on `#fbfaf8`).
3. **Explosion landing spots are computed from the brick count**, alternating left
   and right of the server, so `units` above 5 no longer lands bricks on top of each
   other.
4. The demo page and its copy-paste instructions are dropped.

Unchanged on purpose: the worker walks in front of the server when fetching bricks
on the far side, and the brick colours stay as they are.

## Behaviour kept

- `pointer-events: none` and `aria-hidden="true"`: it never blocks the page and is
  invisible to screen readers.
- Drawn on top of the text (`z-index: 5`, below the skip link), not behind it, so
  text scrolls under the sprites instead of cutting through them. The ground line
  is off: on top of the text it read as a strikethrough. Changed after launch in
  issue #47.
- `prefers-reduced-motion: reduce` draws one still frame and starts no loop.
- Pixel size 4. Below 700 px width (phones in portrait) nothing is drawn and no
  frame loop runs; the canvas is also hidden in CSS. Added after launch in issue #45.

## Verification

- `check_site.rb`: `index.html` has `id="pixel-bg"` and loads
  `/assets/js/pixel-crew.js`; `about/index.html`, `posts/index.html` and every
  article do not.
- `scripts/pixel-crew.test.mjs` (`node --test`, no dependencies): loads the script
  against a stubbed `window`, `document` and 2D context, advances the animation,
  then fires a height-only resize and asserts the scene was not rebuilt; a width
  change must rebuild it. The script exposes nothing globally, so the test observes
  rebuilds by counting assignments to `canvas.width`, which only a rebuild makes.
- CI runs both through `verify.sh`. `build-local.sh` runs the Node test on the host
  (the `ruby:3.3` image has no Node) and tells `verify.sh` inside the container that
  it already ran.
- Manual: look at it in a browser in light mode, dark mode and at phone width.
