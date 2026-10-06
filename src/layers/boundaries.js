// ADM boundaries, from backend/static/tiles/adm.pmtiles (built by
// backend/static/build_adm_tiles.py): geoBoundaries CGAZ at ADM0 countries, ADM1
// regions and ADM2 districts, one level per zoom band rather than all three at once.
//
// Showing one level at a time is what keeps this readable: ADM1 units tile their
// country and ADM2 tile their ADM1, so the national border is still on screen at
// every zoom — drawn as the outer edge of whichever level is active — without three
// sets of lines stacking up on top of each other.
//
// The telescoping used to be a filter over one GeoJSON source, re-applied on every
// zoomend. Each level is now its own tile layer over its own zoom band, so MapLibre
// does the swap itself as part of deciding what to draw. That removes the filter,
// the zoom listener, and the 115 MB download that had to finish before any of it
// could happen — the archive is range-requested, so a world view costs one tile
// rather than 5.3 million vertices.
import {
  ADM_ATTRIBUTION, ADM_FILL, ADM_LABEL, ADM_LEVELS, ADM_LINE, ADM_SOURCE_LAYER,
  ADM_TILES_URL, ADM_ZOOM, BOUNDARIES_SRC, BOUNDARY_COLOR,
} from "../config.js";
import {applyLayerOrder, map, tooltip, tooltipFeaturesAt} from "../map.js";

let on = false;
let built = false;

// A level is drawn from its own floor up to where the next level takes over. The
// deepest has no ceiling: MapLibre overzooms past the archive's maxzoom by scaling
// its deepest tiles, and vector geometry stays sharp doing it.
function band(level) {
  const order = [...ADM_LEVELS].sort((a, b) => a - b);
  const i = order.indexOf(level);
  const next = order[i + 1];
  return {minzoom: ADM_ZOOM[level], ...(next === undefined ? {} : {maxzoom: ADM_ZOOM[next]})};
}

function ensureLayers() {
  if (built) return;
  built = true;

  map.addSource(BOUNDARIES_SRC, {
    type: "vector", url: ADM_TILES_URL, attribution: ADM_ATTRIBUTION,
  });

  for (const level of ADM_LEVELS) {
    const shared = {
      source: BOUNDARIES_SRC,
      "source-layer": ADM_SOURCE_LAYER(level),
      ...band(level),
    };
    // A near-invisible fill: the outlines alone are too thin to hover, so this
    // gives the whole unit a hit target.
    map.addLayer({
      id: ADM_FILL(level), type: "fill", ...shared,
      paint: {"fill-color": BOUNDARY_COLOR, "fill-opacity": 0.001},
    });
    map.addLayer({
      id: ADM_LINE(level), type: "line", ...shared,
      layout: {"line-cap": "round", "line-join": "round"},
      paint: {
        "line-color": BOUNDARY_COLOR,
        "line-opacity": 0.55,
        "line-width": ["interpolate", ["linear"], ["zoom"], 1, 0.7, 6, 1, 12, 1.5],
      },
    });
    bindHover(level);
  }
}

// Hover the unit to see its name. Boundaries sit under everything else, so the
// label only appears where nothing the map is actually about is on top of it.
function bindHover(level) {
  map.on("mousemove", ADM_FILL(level), (e) => {
    const f = e.features && e.features[0];
    if (!f || anythingAbove(e.point)) return;
    const p = f.properties;
    // A few dozen ADM2 units ship with an empty shapeName (mostly CHN and JPN);
    // fall back to the country code rather than showing a bare dash.
    const name = p.name || p.group || "—";
    const group = p.group && level > 0 && p.group !== name
      ? ` <span style="opacity:.6">${p.group}</span>` : "";
    tooltip.setLngLat(e.lngLat)
      .setHTML(`<span style="opacity:.6">${ADM_LABEL[level]}</span> <b>${name}</b>${group}`)
      .addTo(map);
  });
  map.on("mouseleave", ADM_FILL(level), () => tooltip.remove());
}

// The boundary label yields to every layer that has a label of its own — a flagged
// cell at any resolution, a split cell's colour band, a flash polygon. Those layers
// register themselves when they are built, so this cannot fall out of step with
// them the way an enumerated list did.
function anythingAbove(point) {
  return tooltipFeaturesAt(point).length > 0;
}

// Kept so main.js's zoomend hook stays valid. The zoom bands are part of the layer
// definitions now, so there is nothing left to re-apply.
export function updateBoundariesLOD() {}

export function setBoundariesOn(next) {
  on = next;
  if (!on) {
    if (built) {
      for (const level of ADM_LEVELS) {
        for (const id of [ADM_FILL(level), ADM_LINE(level)]) {
          if (map.getLayer(id)) map.setLayoutProperty(id, "visibility", "none");
        }
      }
    }
    tooltip.remove();
    return;
  }
  ensureLayers();
  for (const level of ADM_LEVELS) {
    for (const id of [ADM_FILL(level), ADM_LINE(level)]) {
      if (map.getLayer(id)) map.setLayoutProperty(id, "visibility", "visible");
    }
  }
  applyLayerOrder();
}

export const boundariesOn = () => on;
