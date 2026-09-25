// The Flood Hub flash-flood overlay (public/data_flash_floods.geojson): a global
// layer, independent of the flagged-area dataset.
import {DATA} from "../sources.js";
import {FLASH_FILL_LAYERS, FLASH_LAYERS, FLASH_SRC, FLASH_TIER, FLASH_TIERS, FLASH_TIER_LAYERS, stateOn} from "../config.js";
import {applyLayerOrder, map, tooltip} from "../map.js";
import {boundsOf} from "../geometry.js";
import {flashChanceLabel} from "../format.js";
import {flashFill, flashOutline, view, visibleFlashModels, visibleTiers} from "../settings.js";
import {rerenderPanel, scrollPanelToSection, setFlashProps} from "../panel.js";

// Flood Hub flash-flood polygons — a global overlay (available in every dataset
// view) toggled from the "Flash Floods" section at the bottom of the side panel.
// Two polygon types: "highly_likely" and "likely".
let flashOn = false;
let flashData = null;
let flashLoading = false;

// The flash polygon the user last clicked (null = standby). Its properties fill
// the Flood Hub tile, just as a selected basin fills the river tiles. The id is
// tracked separately to carry the `selected` feature-state on the map.
let selectedFlash = null;
let selectedFlashId = null;

// ---- Flood Hub flash-flood polygons (global overlay) ----------------------

function loadFlash(cb) {
  if (flashData) return cb(flashData);
  if (flashLoading) return;
  flashLoading = true;
  fetch(DATA.flash)
    .then((r) => { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); })
    .then((data) => { flashData = data; flashLoading = false; cb(data); })
    .catch((err) => { flashLoading = false; console.warn("flash floods:", err.message); cb(null); });
}

// Violet, distinct from severity (yellow/orange/red), streams (cyan) and districts
// (magenta). "highly_likely" = darker fill + solid outline; "likely" = lighter fill
// + dashed outline.
function ensureFlashLayers() {
  if (map.getSource(FLASH_SRC) || !flashData) return;
  // promoteId makes each polygon's `polygonId` its feature id, so a clicked
  // polygon can carry a `selected` feature-state (white outline) like a basin.
  map.addSource(FLASH_SRC, {type: "geojson", data: flashData, promoteId: "polygonId"});
  // Added likely group first, then highly_likely, so highly_likely sits on top.
  map.addLayer({
    id: "flash-fill-likely", type: "fill", source: FLASH_SRC,
    filter: ["==", ["get", "polygon_type"], "likely"],
    paint: {"fill-color": flashFill("likely"), "fill-opacity": flashOpacityExpr("likely")},
  });
  map.addLayer({
    id: "flash-line-likely", type: "line", source: FLASH_SRC,
    filter: ["==", ["get", "polygon_type"], "likely"],
    paint: {
      "line-color": ["case", stateOn("selected"), "#ffffff", flashOutline("likely")],
      "line-width": ["case", stateOn("selected"), 3, 1.1],
      "line-opacity": 0.9, "line-dasharray": [2, 1.5],
    },
  });
  map.addLayer({
    id: "flash-fill-high", type: "fill", source: FLASH_SRC,
    filter: ["==", ["get", "polygon_type"], "highly_likely"],
    paint: {"fill-color": flashFill("highly_likely"), "fill-opacity": flashOpacityExpr("highly_likely")},
  });
  map.addLayer({
    id: "flash-line-high", type: "line", source: FLASH_SRC,
    filter: ["==", ["get", "polygon_type"], "highly_likely"],
    paint: {
      "line-color": ["case", stateOn("selected"), "#ffffff", flashOutline("highly_likely")],
      "line-width": ["case", stateOn("selected"), 3.5, 1.8],
      "line-opacity": 0.95,
    },
  });
  bindFlashInteractions();
}

// Repaint the flash overlay after its ramp changes (the layers may not exist
// yet if the overlay has never been switched on — they pick the ramp up when
// they are created).
// A tier's resting/selected fill opacity, from the Display Options slider. Outline
// mode drops the fill and leans on the (already distinct) dashed/solid borders.
function flashOpacityExpr(tier) {
  if (view.flashOutlineOnly) return 0;
  const t = FLASH_TIER[tier];
  const fill = Math.min(1, view.flashOpacity * t.fillMul);
  return ["case", stateOn("selected"), Math.min(1, fill * t.selMul), fill];
}

// Opacity, outline mode and which tiers are drawn. Kept apart from
// applyFlashColors so a ramp change and an appearance change stay independent.
export function applyFlashStyle() {
  for (const tier of FLASH_TIERS) {
    const [fillId, lineId] = FLASH_TIER_LAYERS[tier];
    if (map.getLayer(fillId)) map.setPaintProperty(fillId, "fill-opacity", flashOpacityExpr(tier));
    // A hidden tier stays hidden even while the overlay is on; when the overlay
    // is off, setFlashOn has already hidden everything and owns the visibility.
    if (!flashOn) continue;
    const vis = visibleTiers.has(tier) ? "visible" : "none";
    for (const id of [fillId, lineId]) {
      if (map.getLayer(id)) map.setLayoutProperty(id, "visibility", vis);
    }
  }
}

export function applyFlashColors() {
  const pairs = [["likely", "flash-fill-likely", "flash-line-likely"],
    ["highly_likely", "flash-fill-high", "flash-line-high"]];
  for (const [tier, fillId, lineId] of pairs) {
    if (map.getLayer(fillId)) map.setPaintProperty(fillId, "fill-color", flashFill(tier));
    if (map.getLayer(lineId)) {
      map.setPaintProperty(lineId, "line-color",
        ["case", stateOn("selected"), "#ffffff", flashOutline(tier)]);
    }
  }
}

// Hover a flash-flood polygon to see its legend attribute ("Likely" /
// "Highly likely"), colored to match the on-map fill. Reuses the shared cell
// tooltip; the cell handler yields to flash where the two overlap.
function flashTooltipHtml(p) {
  const label = flashChanceLabel(p.polygon_type);
  const fill = FLASH_TIER[p.polygon_type] ? flashFill(p.polygon_type) : "#7c3aed";
  return `<span style="display:inline-flex;align-items:center;gap:6px">` +
    `<span style="display:inline-block;width:10px;height:10px;border-radius:2px;background:${fill}"></span>` +
    `Flash flood · <b>${label}</b></span>`;
}

let flashInteractionsBound = false;
function bindFlashInteractions() {
  if (flashInteractionsBound) return;
  flashInteractionsBound = true;
  FLASH_FILL_LAYERS.forEach((id) => {
    map.on("mousemove", id, (e) => {
      const f = e.features[0];
      if (!f) return;
      map.getCanvas().style.cursor = "pointer";
      tooltip.setLngLat(e.lngLat).setHTML(flashTooltipHtml(f.properties)).addTo(map);
    });
    map.on("mouseleave", id, () => {
      map.getCanvas().style.cursor = "";
      tooltip.remove();
    });
    // Click a flash polygon to frame it on screen. Prefer the full source
    // geometry, since rendered features can be clipped at tile edges.
    map.on("click", id, (e) => {
      const hit = e.features && e.features[0];
      if (!hit) return;
      const src = flashData && flashData.features.find(
        (x) => x.properties.polygonId === hit.properties.polygonId);
      const b = boundsOf([src || hit]);
      if (!b.isEmpty()) map.fitBounds(b, {padding: 60, maxZoom: 12, duration: 600});
      selectedFlash = (src || hit).properties;   // fill the Flood Hub tile
      setFlashSelected(selectedFlash.polygonId);  // light up the polygon on the map
      setFlashProps(selectedFlash);               // hand it to the panel to render
      rerenderPanel();
      scrollPanelToSection("section-flash");      // reveal the flash tile just filled
    });
  });
}

// Carry the `selected` feature-state on the clicked flash polygon (white
// outline), clearing it from the previously selected one. Pass null to clear.
export function setFlashSelected(polygonId) {
  if (!map.getSource(FLASH_SRC)) { selectedFlashId = polygonId; return; }
  if (selectedFlashId != null)
    map.setFeatureState({source: FLASH_SRC, id: selectedFlashId}, {selected: false});
  selectedFlashId = polygonId;
  if (polygonId != null)
    map.setFeatureState({source: FLASH_SRC, id: polygonId}, {selected: true});
}

// Back to standby: clicking the basemap closes the flash tile as well as the cell
// tile, and layers/cells.js cannot assign this module's state from outside.
export function clearFlashSelection() {
  selectedFlash = null;
  setFlashSelected(null);
  setFlashProps(null);
}

// True when a visible flash polygon sits under the cursor (flash wins the tooltip).
export function flashUnderCursor(point) {
  if (!flashOn || !map.getLayer(FLASH_FILL_LAYERS[0])) return false;
  return map.queryRenderedFeatures(point, {layers: FLASH_FILL_LAYERS}).length > 0;
}

export function setFlashOn(on) {
  flashOn = on;
  if (!on) {
    FLASH_LAYERS.forEach((id) => { if (map.getLayer(id)) map.setLayoutProperty(id, "visibility", "none"); });
    return;
  }
  loadFlash((data) => {
    if (!data) {           // fetch failed: back out the flood_hub flash model
      flashOn = false;
      visibleFlashModels.delete("flood_hub");
      const cb = document.querySelector('.filter-dd[data-kind="flash"] input[data-model="flood_hub"]');
      if (cb) cb.checked = false;
      const tile = document.getElementById("tile-flash-flood_hub");
      if (tile) tile.classList.add("hidden");
      return;
    }
    ensureFlashLayers();
    FLASH_LAYERS.forEach((id) => { if (map.getLayer(id)) map.setLayoutProperty(id, "visibility", "visible"); });
    applyFlashStyle();   // re-hide any tier switched off in Display Options
    applyLayerOrder();   // keep flash below districts and above basins
  });
}
