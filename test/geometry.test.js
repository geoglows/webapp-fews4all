// Runs with `npm test` (node --test). geometry.js is pure and imports nothing but
// maplibre's LngLatBounds, so it can be exercised straight from node — the reason
// the clipping lives in its own module.
//
// The fixtures are the real build outputs when they are present; the test skips
// rather than fails if public/ has not been built yet.
import {test, skip} from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import {fileURLToPath} from "node:url";
import path from "node:path";

import {clipToBand, lonRange} from "../src/geometry.js";

const ROOT = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const load = (name) => {
  const p = path.join(ROOT, "public", name);
  return fs.existsSync(p) ? JSON.parse(fs.readFileSync(p, "utf8")) : null;
};

const ringArea = (r) => {
  let s = 0;
  for (let i = 0; i < r.length - 1; i++) s += r[i][0] * r[i + 1][1] - r[i + 1][0] * r[i][1];
  return Math.abs(s / 2);
};
const area = (g) => {
  if (!g) return 0;
  const parts = g.type === "Polygon" ? [g.coordinates] : g.coordinates;
  return parts.reduce((t, rings) =>
    t + rings.reduce((u, r, i) => u + (i ? -1 : 1) * ringArea(r), 0), 0);
};

// Slice a geometry the way visibleFeatures does, into n equal longitude bands.
function slice(geom, n) {
  const [x0, x1] = lonRange(geom);
  const span = x1 - x0;
  return [...Array(n)].map((_, i) =>
    clipToBand(geom, x0 + (span * i) / n, x0 + (span * (i + 1)) / n));
}

test("a band keeps every vertex inside its own longitude window", () => {
  const hex = {type: "Polygon", coordinates: [[[0, 0], [2, 1], [4, 0], [4, -2], [2, -3], [0, -2], [0, 0]]]};
  const bands = slice(hex, 2);
  assert.equal(bands.filter(Boolean).length, 2);
  bands.forEach((g, i) => {
    const lo = i === 0 ? -1e-9 : 2 - 1e-9;
    const hi = i === 0 ? 2 + 1e-9 : 4 + 1e-9;
    const walk = (c) => {
      if (Array.isArray(c[0])) return c.forEach(walk);
      assert.ok(c[0] >= lo && c[0] <= hi, `vertex ${c} outside band ${i}`);
    };
    walk(g.coordinates);
  });
});

test("slicing conserves area", () => {
  const hex = {type: "Polygon", coordinates: [[[0, 0], [2, 1], [4, 0], [4, -2], [2, -3], [0, -2], [0, 0]]]};
  for (const n of [2, 3, 4]) {
    const sum = slice(hex, n).reduce((t, g) => t + area(g), 0);
    assert.ok(Math.abs(sum - area(hex)) / area(hex) < 1e-12, `n=${n}`);
  }
});

test("a polygon with a hole keeps the hole", () => {
  const donut = {
    type: "Polygon",
    coordinates: [
      [[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]],
      [[3, 3], [3, 7], [7, 7], [7, 3], [3, 3]],
    ],
  };
  const whole = area(donut);
  const sum = slice(donut, 2).reduce((t, g) => t + area(g), 0);
  assert.ok(Math.abs(sum - whole) / whole < 1e-12);
  assert.ok(whole < 100, "hole must be subtracted");
});

test("real basin geometry survives slicing", (t) => {
  const data = load("data_basins.geojson");
  if (!data) return skip("public/data_basins.geojson not built");
  let checked = 0, empties = 0, worst = 0;
  for (const f of data.features.slice(0, 2000)) {
    const whole = area(f.geometry);
    const [x0, x1] = lonRange(f.geometry);
    if (!whole || !(x1 > x0)) continue;
    const bands = slice(f.geometry, 2);
    if (bands.some((g) => !g)) { empties++; continue; }   // caller falls back to undivided
    worst = Math.max(worst, Math.abs(bands.reduce((s, g) => s + area(g), 0) - whole) / whole);
    checked++;
  }
  assert.ok(checked > 100, `only ${checked} basins checked`);
  // Real coordinates carry rounding through the crossing-point interpolation, so
  // the bound is generous next to the exact cases above — still ~1e6 times finer
  // than a pixel at any zoom the map offers.
  assert.ok(worst < 1e-6, `worst area error ${worst}`);
  t.diagnostic(`${checked} basins, ${empties} undivided, worst area error ${worst}`);
});

test("real H3 hexagons survive slicing at every resolution", (t) => {
  const data = load("data_h3cells.geojson");
  if (!data) return skip("public/data_h3cells.geojson not built");
  const seen = new Set();
  for (const f of data.features) {
    const res = f.properties.res;
    if (seen.has(res)) continue;
    seen.add(res);
    const whole = area(f.geometry);
    const sum = slice(f.geometry, 2).reduce((s, g) => s + area(g), 0);
    assert.ok(Math.abs(sum - whole) / whole < 1e-9, `res ${res}`);
  }
  t.diagnostic(`resolutions checked: ${[...seen].sort().join(", ")}`);
});
