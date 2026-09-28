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

function load({ width = 390, height = 700, reducedMotion = false } = {}) {
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
      matches: q.includes("reduce") ? reducedMotion : false,
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
    run(seconds) {
      for (let i = 0; i < seconds * 60; i++) {
        const fn = frameCallback;
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
  crew.resize(390, 620);
  crew.resize(390, 700);
  assert.equal(crew.canvas.widthWrites, 1, "scene rebuilt when only the height changed");
});

test("a width change rebuilds the scene", () => {
  const crew = load();
  crew.resize(800, 700);
  assert.equal(crew.canvas.widthWrites, 2);
});

test("full loops run without throwing, on a phone and on a desktop", () => {
  load({ width: 390 }).run(90);
  load({ width: 1440 }).run(90);
});

test("reduced motion draws once and schedules no animation frame", () => {
  const crew = load({ reducedMotion: true });
  assert.equal(crew.canvas.widthWrites, 1);
  assert.equal(crew.framesRequested, 0);
});
