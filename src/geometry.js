// Polygon maths: bounds, and the clipping that splits a cell between the models
// forecasting in it. No app state — unit-testable on its own.
import {LngLatBounds} from "maplibre-gl";

// ---- Splitting a cell between the models that forecast in it --------------
// With a ramp per model, a cell holding both models has no single right colour,
// so it is drawn as one vertical slice per model, each in its own ramp. The
// slices carry the fill; the undivided original stays in the collection to draw
// the outline and hash, so the split never shows up as a seam in the border.
// Clipping is a Sutherland–Hodgman pass against one vertical line at a time —
// enough for hexagons and for basin polygons alike, and it needs no library.
function clipHalf(ring, x, keepLeft) {
  const inside = (pt) => (keepLeft ? pt[0] <= x : pt[0] >= x);
  const n = ring.length;
  const closed = n > 1 && ring[0][0] === ring[n - 1][0] && ring[0][1] === ring[n - 1][1];
  const pts = closed ? ring.slice(0, -1) : ring.slice();
  const out = [];
  for (let i = 0; i < pts.length; i++) {
    const a = pts[i], b = pts[(i + 1) % pts.length];
    const ain = inside(a), bin = inside(b);
    if (ain) out.push(a);
    if (ain !== bin) {                 // a and b straddle the line: add the crossing
      const t = (x - a[0]) / (b[0] - a[0]);
      out.push([x, a[1] + t * (b[1] - a[1])]);
    }
  }
  if (out.length < 3) return null;
  out.push(out[0].slice());
  return out;
}

// One polygon (array of rings) clipped to the vertical band [x0, x1].
function clipPolyToBand(rings, x0, x1) {
  const kept = [];
  for (let i = 0; i < rings.length; i++) {
    let r = clipHalf(rings[i], x0, false);
    if (r) r = clipHalf(r, x1, true);
    if (!r) {
      if (i === 0) return null;        // outer ring gone: nothing of this part
      continue;                        // a hole outside the band just disappears
    }
    kept.push(r);
  }
  return kept.length ? kept : null;
}

export function clipToBand(geometry, x0, x1) {
  if (!geometry) return null;
  if (geometry.type === "Polygon") {
    const rings = clipPolyToBand(geometry.coordinates, x0, x1);
    return rings ? {type: "Polygon", coordinates: rings} : null;
  }
  if (geometry.type === "MultiPolygon") {
    const parts = geometry.coordinates
      .map((poly) => clipPolyToBand(poly, x0, x1)).filter(Boolean);
    return parts.length ? {type: "MultiPolygon", coordinates: parts} : null;
  }
  return null;
}

export function lonRange(geometry) {
  let min = Infinity, max = -Infinity;
  const walk = (c) => {
    if (Array.isArray(c[0])) return c.forEach(walk);
    if (c[0] < min) min = c[0];
    if (c[0] > max) max = c[0];
  };
  if (geometry) walk(geometry.coordinates);
  return [min, max];
}

// Leaflet handed us `layer.getBounds()`; with MapLibre we walk the coordinates.
export function boundsOf(features) {
  const b = new LngLatBounds();
  const walk = (c) => (Array.isArray(c[0]) ? c.forEach(walk) : b.extend(c));
  for (const f of features) if (f.geometry) walk(f.geometry.coordinates);
  return b;
}
