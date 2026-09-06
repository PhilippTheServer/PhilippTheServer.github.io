---
layout: post
title: "Testing That a Canvas Diagram Actually Painted Pixels"
subtitle: "Why a DOM assertion cannot tell a working chart from a blank one."
date: 2026-07-21 09:00:00 +0200
tags: [testing, frontend]
description: >-
  A canvas element can exist, have the right size, and show a completely
  blank drawing surface, and a DOM-based test suite will pass every one of
  those assertions. This covers why canvas content needs a real browser and a
  pixel-level assertion to test properly, with a complete Playwright example.
---

## The problem

I was building a small internal dashboard with a diagram rendered on an HTML5 `<canvas>` —
nothing exotic, just circles and lines drawn imperatively with the 2D context. The test
suite looked reasonable:

```js
// Passes. Proves almost nothing.
test("renders the diagram canvas", () => {
  render(<Diagram />);
  const canvas = screen.getByTestId("diagram-canvas");
  expect(canvas).toBeInTheDocument();
  expect(canvas).toHaveAttribute("width", "400");
  expect(canvas).toHaveAttribute("height", "300");
});
```

Every one of those assertions is true, and every one of them was still true the day a
colour-transform helper started throwing on a value it was not expecting, the exception got
swallowed by an event handler further up the call stack, and the canvas rendered nothing —
element present, correct dimensions, entirely blank. The test suite stayed green through the
whole thing.

The reason is structural, not a gap in that particular test: everything a DOM assertion can
inspect about a `<canvas>` element is metadata — its existence, its size, its attributes.
None of that describes what is drawn on it, because canvas content is not part of the DOM at
all; it is pixels in a bitmap that the element happens to own. A test framework built around
querying and asserting on DOM nodes has nothing to query, because there is no node
representing "a blue circle at (100, 150)" — that information only exists as colour values in
a raster the browser is holding.

Running the suite under `jsdom` (which most unit-test setups use by default, because it is
fast and does not need a real browser) makes this worse rather than better: `jsdom` does not
implement real canvas rendering at all. `getContext('2d')` either returns `null` or a stub
that records calls without producing actual pixel output, depending on which shim, if any,
is installed. A test asserting canvas *content* under `jsdom` is not testing a simplified
version of rendering — it is testing nothing, because there is no renderer underneath it to
get right or wrong.

## Working through it

### Test canvas content only where a real renderer exists

The 2D canvas API is implemented by the browser's actual graphics stack. A test that wants
to know what was drawn needs that real stack running, which means a real browser rather than
a DOM simulator. Playwright drives an actual browser (Chromium, Firefox, or WebKit) for
exactly this reason: whatever `getContext('2d').getImageData()` returns inside the page is
real pixel data, produced by the same rendering path a user's browser would use.

### Read back pixels, not attributes

Inside the page, after triggering a render, call `getImageData` at a coordinate where the
test expects specific content, and assert on the actual colour values:

```js
const pixel = await page.evaluate(() => {
  const canvas = document.querySelector('[data-testid="diagram-canvas"]');
  const ctx = canvas.getContext('2d');
  const data = ctx.getImageData(100, 150, 1, 1).data;
  return Array.from(data); // [r, g, b, a]
});
expect(pixel).toEqual([59, 130, 246, 255]);
```

This is the assertion the DOM-based test could never make: not "a canvas exists at this
size" but "the pixel at this exact coordinate is this exact colour", which is only true if
the draw call that was supposed to produce it actually ran and did not throw partway through.

### Assert non-blank as a cheap first check, then specific pixels as the real one

A fully blank canvas is `rgba(0, 0, 0, 0)` at every coordinate (transparent black, the
default). Checking that at least one sampled pixel is non-transparent is a fast, low-effort
smoke test that catches the "rendering silently failed entirely" class of bug. It is not
sufficient on its own — a canvas that draws the wrong thing in the wrong colour still passes
a "something is not blank" check — so pair it with assertions at specific coordinates where
you know, from the drawing code, exactly what should be there.

### Decide what "close enough" means for colour comparisons

Anti-aliasing, sub-pixel positioning, and font or GPU differences between browsers or
between CI and a local machine can shift a colour value by a few units without the drawing
being wrong. Exact equality on RGBA values works fine for solid fills at coordinates well
inside a shape, away from any edge; for anything near a boundary or involving anti-aliased
strokes, assert a tolerance range instead of exact equality, or sample a coordinate far
enough inside the shape that anti-aliasing at its edge cannot reach it.

## The solution

A minimal drawing function, a Playwright test asserting on real pixel data at a known
coordinate, and the package versions needed to run it.

```html
<!-- index.html -->
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>Canvas test fixture</title></head>
<body>
  <canvas data-testid="diagram-canvas" width="400" height="300"></canvas>
  <script src="draw.js"></script>
</body>
</html>
```

```js
// draw.js
function drawDiagram(canvas) {
  const ctx = canvas.getContext('2d');
  ctx.clearRect(0, 0, canvas.width, canvas.height);

  // A solid blue circle centred at (100, 150), radius 40.
  ctx.fillStyle = 'rgb(59, 130, 246)';
  ctx.beginPath();
  ctx.arc(100, 150, 40, 0, Math.PI * 2);
  ctx.fill();
}

document.addEventListener('DOMContentLoaded', () => {
  const canvas = document.querySelector('[data-testid="diagram-canvas"]');
  drawDiagram(canvas);
});
```

```js
// diagram.spec.js
const { test, expect } = require('@playwright/test');
const path = require('path');

test('paints a solid blue circle at the expected coordinate', async ({ page }) => {
  await page.goto('file://' + path.resolve(__dirname, 'index.html'));

  const pixel = await page.evaluate(() => {
    const canvas = document.querySelector('[data-testid="diagram-canvas"]');
    const ctx = canvas.getContext('2d');
    return Array.from(ctx.getImageData(100, 150, 1, 1).data);
  });

  expect(pixel).toEqual([59, 130, 246, 255]);
});

test('does not leave the canvas blank', async ({ page }) => {
  await page.goto('file://' + path.resolve(__dirname, 'index.html'));

  const isBlank = await page.evaluate(() => {
    const canvas = document.querySelector('[data-testid="diagram-canvas"]');
    const ctx = canvas.getContext('2d');
    const data = ctx.getImageData(0, 0, canvas.width, canvas.height).data;
    for (let i = 3; i < data.length; i += 4) {
      if (data[i] !== 0) return false; // found a non-transparent pixel
    }
    return true;
  });

  expect(isBlank).toBe(false);
});
```

```json
// package.json
{
  "name": "canvas-pixel-testing-example",
  "private": true,
  "devDependencies": {
    "@playwright/test": "1.48.2"
  },
  "scripts": {
    "test": "playwright test"
  }
}
```

Run it:

```bash
npm install
npx playwright install --with-deps chromium
npm test
```

Expected output:

```
Running 2 tests using 1 worker

  ✓  diagram.spec.js:5:1 › paints a solid blue circle at the expected coordinate
  ✓  diagram.spec.js:19:1 › does not leave the canvas blank

  2 passed (1.2s)
```

To see the test correctly fail, comment out the `ctx.fill()` call in `draw.js` (simulating
the "draw call silently did nothing" bug) and run `npm test` again:

```
  1) diagram.spec.js:5:1 › paints a solid blue circle at the expected coordinate

    Error: expect(received).toEqual(expected)

    - Expected  - 1
    + Received  + 1

      Array [
    -   59,
    -   130,
    -   246,
    +   0,
    +   0,
    +   0,
        255,
      ]

  2) diagram.spec.js:19:1 › does not leave the canvas blank

    Error: expect(received).toBe(expected)
    Expected: false
    Received: true

  2 failed
```

Both tests fail, for the reason that matters: the pixel at (100, 150) is transparent black
instead of blue, and the canvas is entirely blank — exactly the state a DOM-only test suite
would have missed completely, since the element, its attributes, and its dimensions are all
still correct.

## Conclusion

A `<canvas>` element's DOM footprint and its visual content are unrelated facts, and testing
one tells you nothing about the other. Any assertion strategy built purely on DOM queries and
attributes will pass a canvas that renders nothing, because from the DOM's perspective a
blank canvas and a correctly drawn one are identical.

Two points generalise beyond canvas specifically:

**Test the layer where the bug actually lives, not the layer that is easiest to query.** DOM
assertions are cheap and fast, which is exactly why it is tempting to let them stand in for
content assertions they cannot make. If the thing you care about is pixels drawn by a real
graphics API, jsdom's approximation of the DOM is the wrong tool no matter how convenient it
is for everything else in the suite.

**A convenient test double is not equivalent to the real thing it stands in for, and the gap
matters most exactly where the double is weakest.** jsdom is an excellent stand-in for DOM
structure and event handling; it is not a canvas renderer, and pretending it is one for the
sake of a faster test suite trades a real assertion for a comforting green checkmark.
