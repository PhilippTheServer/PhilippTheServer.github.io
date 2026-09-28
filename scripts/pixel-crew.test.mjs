// Behaviour test for assets/js/pixel-crew.js (issue #43), run with `node --test`.
// The script runs against stubbed browser globals; nothing is installed.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";

const source = readFileSync(
  process.env.PIXEL_CREW_JS ?? new URL("../assets/js/pixel-crew.js", import.meta.url),
  "utf8",
);

function load({ width = 1280, height = 800, reducedMotion = false } = {}) {
  const listeners = {};
  const timers = new Map();
  let nextTimer = 1;
  let frameCallback = null;
  let framesRequested = 0;
  let now = 0;
  const canvas = {
    widthWrites: 0,
    _w: 0,
    get width() { return this._w; },
    set width(v) { this._w = v; this.widthWrites++; },
    height: 0,
    style: {},
    getContext: () => new Proxy({}, { get: () => () => {} }),
  };
  const window = {
    innerWidth: width,
    innerHeight: height,
    matchMedia: (q) => ({
      get matches() {
        if (q.includes("reduce")) return reducedMotion;
        const max = q.match(/max-width:\s*(\d+)px/);
        return max ? window.innerWidth <= Number(max[1]) : false;
      },
      addEventListener() {},
    }),
    addEventListener: (type, fn) => { (listeners[type] ??= []).push(fn); },
  };
  const context = {
    window,
    document: { getElementById: (id) => (id === "pixel-bg" ? canvas : null), documentElement: {} },
    getComputedStyle: () => ({ getPropertyValue: () => " #e2ded7" }),
    performance: { now: () => now },
    requestAnimationFrame: (fn) => { frameCallback = fn; framesRequested++; },
    setTimeout: (fn) => { timers.set(nextTimer, fn); return nextTimer++; },
    clearTimeout: (id) => { timers.delete(id); },
    Math,
  };
  runInNewContext(source, context);

  return {
    canvas,
    get framesRequested() { return framesRequested; },
    resize(w, h) {
      window.innerWidth = w;
      window.innerHeight = h;
      (listeners.resize ?? []).forEach((fn) => fn());
      const pending = [...timers.values()];
      timers.clear();
      pending.forEach((fn) => fn());
    },
    get looping() { return frameCallback !== null; },
    run(seconds) {
      for (let i = 0; i < seconds * 60; i++) {
        const fn = frameCallback;
        if (!fn) return;
        frameCallback = null;
        now += 1000 / 60;
        fn(now);
      }
    },
  };
}

test("a height-only resize does not rebuild the scene", () => {
  const crew = load();
  crew.run(3);
  assert.equal(crew.canvas.widthWrites, 1);
  crew.resize(1280, 720);
  crew.resize(1280, 800);
  assert.equal(crew.canvas.widthWrites, 1, "scene rebuilt when only the height changed");
});

test("a width change rebuilds the scene", () => {
  const crew = load();
  crew.resize(1024, 800);
  assert.equal(crew.canvas.widthWrites, 2);
});

test("full loops run without throwing, from the narrowest shown width to a wide screen", () => {
  load({ width: 700 }).run(90);
  load({ width: 1920 }).run(90);
});

test("phones get no animation: no canvas sizing, no animation frame", () => {
  const crew = load({ width: 390 });
  assert.equal(crew.canvas.widthWrites, 0);
  assert.equal(crew.framesRequested, 0);
});

test("crossing 700 px starts and stops the animation", () => {
  const crew = load({ width: 390 });
  crew.resize(1024, 800);
  assert.equal(crew.canvas.widthWrites, 1);
  assert.ok(crew.looping, "no loop after widening past 700 px");
  crew.run(1);
  crew.resize(390, 800);
  crew.run(1);
  assert.ok(!crew.looping, "loop still running after narrowing to a phone");
});

test("reduced motion draws once and schedules no animation frame", () => {
  const crew = load({ reducedMotion: true });
  assert.equal(crew.canvas.widthWrites, 1);
  assert.equal(crew.framesRequested, 0);
});
