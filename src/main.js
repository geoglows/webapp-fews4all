// Entry point. Vite bundles these imports — no CDN <script>/<link> tags needed.
import "./style.css" // Tailwind + custom theme + MapLibre overrides
import "maplibre-gl/dist/maplibre-gl.css" // MapLibre's own stylesheet (from npm)
import {AttributionControl, LngLatBounds, Map as MapLibreMap, NavigationControl, Popup, setWorkerUrl} from "maplibre-gl";
// MapLibre 6 runs its tiling in a worker it loads from a sibling file resolved
// off `import.meta.url`. Once Vite pre-bundles or hashes the library that path
// no longer exists, and the failure is silent and easy to misread: raster tiles
// are loaded on the main thread so the basemap looks fine, while every GeoJSON
// source stays stuck at `isSourceLoaded() === false` and paints nothing. Hand
// MapLibre a worker URL that Vite actually emits.
import maplibreWorkerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url";
import {icon} from "./icons.js"; // heroicons, inlined as SVG at build time
import {DATA} from "./sources.js"; // central data-source hub (public/ files)

(function () {
  "use strict";

  setWorkerUrl(maplibreWorkerUrl);

  const SEVERITY = {
    warning: {rank: 1, color: "#ffd21f", label: "Warning"},
    danger: {rank: 2, color: "#ff8c00", label: "Danger"},
    extreme: {rank: 3, color: "#e0201b", label: "Extreme"},
  };
  const DEFAULT_COLOR = "#4da3ff";
  const HASH_COLOR = "#0f172a";   // neutral dark for the multi-model legend swatch
                                  // (on the map the hash/outline are per-severity)

  // MapLibre measures zoom against a 512px world where Leaflet used 256px, so
  // every zoom number in this file sits one step below the Leaflet equivalent
  // for the same view on screen.
  const RES_START_ZOOM = 2;
  const RES_ZOOM_STEP = 2;

  function sevColor(s) {
    const k = (s || "").toLowerCase();
    return SEVERITY[k] ? SEVERITY[k].color : DEFAULT_COLOR;
  }

  const FIELD_LABELS = [
    ["severity", "Severity"],
    ["riverId", "River ID"],
    ["district", "Near"],
    ["returnPeriodYr", "Return period"],
    ["peakDischargeCms", "Mean discharge"], //this will need to be changed into peak, the info from geoglows is in mean discharge.
    ["issuedTime", "Issued"],
    ["startTime", "Start"],
    ["peakTime", "Peak"],
    ["endTime", "End"],
    ["historicalComparison", "Historical"],
  ];
  // The riverId row is labelled per model: GEOGLOWS forecasts a reach ("River
  // ID"), Flood Hub forecasts a gauge ("Gauge"). Falls back to the label above.
  const RIVER_ID_LABEL = {geoglows: "River ID", flood_hub: "Gauge"};

  // Every pipeline stamps the FeatureCollection with a `kind`, so all the
  // user-facing wording (readout, attribution, panel copy) comes from one place
  // instead of being hardcoded to H3.
  const DATASETS = {
    "basins-telescoping": {
      unit: "Basin",
      resLabel: "Basin level",
      attribution: "HydroBASINS",
      emptyTitle: "No basin selected",
      emptyBody: "Click a highlighted basin on the map to see every forecast inside it.",
    },
    "h3-telescoping": {
      unit: "Cell",
      resLabel: "H3 res",
      attribution: "Grid: H3 (Uber H3)",
      emptyTitle: "No cell selected",
      emptyBody: "Click a highlighted grid cell on the map to see every forecast inside it.",
    },
    "s2-telescoping": {
      unit: "Cell",
      resLabel: "S2 level",
      attribution: "Grid: S2 (Google S2)",
      emptyTitle: "No cell selected",
      emptyBody: "Click a highlighted grid cell on the map to see every forecast inside it.",
      // S2 cells are much coarser per level than H3, so the uniform start/step
      // (which would only reach level 8 at zoom ~14) leaves huge cells filling
      // the screen. Map each level to the zoom where its cells read well.
      zoomByRes: {2: 0, 3: 1, 4: 2, 5: 3, 6: 4, 7: 5, 8: 6},
    },
  };

  // Until the data lands we don't know which build it is; stay neutral.
  let dataset = {
    unit: "Area", resLabel: "Level", attribution: "",
    emptyTitle: "No area selected",
    emptyBody: "Click a highlighted area on the map to see every forecast inside it.",
  };

  // ---- Dataset switcher + basin-context state (logic further down) -----------
  // Data URLs come from the source hub in sources.js (see DATA import above).
  const DATASETS_MENU = [
    {key: "basins", label: "Basins", url: DATA.basins},
    {key: "h3", label: "H3 cells", url: DATA.h3},
    {key: "s2", label: "S2 cells", url: DATA.s2},
  ];
  let currentDatasetKey = "basins";
  let datasetControlEl = null;
  let contextControlEl = null;
  let interactionsBound = false;      // cell hover/click handlers bound once

  // Streams + districts context — only shown for the basins dataset, only for the
  // selected basin. `data` is cached once fetched; `on` is the toggle state.
  const ctx = {
    streams: {url: DATA.streams, data: null, on: false, loading: false},
    districts: {url: DATA.districts, data: null, on: false, loading: false},
  };
  let selectedBasinId = null;
  let zoomExtent = "basin";           // basin | river | district
  // MapLibre zoom sits ~1 below Leaflet, so the "show all tributaries" zoom is a
  // step lower than the local build's 10.
  const STREAM_ALL_ZOOM = 9;
  function streamMinOrder(z) {
    return z >= STREAM_ALL_ZOOM ? 1 : Math.max(1, STREAM_ALL_ZOOM - Math.floor(z) + 1);
  }

  // Flood Hub flash-flood polygons — a global overlay (available in every dataset
  // view) toggled from the "Flash Floods" section at the bottom of the side panel.
  // Two polygon types: "highly_likely" and "likely".
  let flashOn = false;
  let flashData = null;
  let flashLoading = false;
  const FLASH_SRC = "flash-src";
  // Fill is split by type so highly_likely can stack above likely. The fills
  // double as hover/click hit targets; FLASH_LAYERS is bottom->top within the group.
  const FLASH_FILL_LAYERS = ["flash-fill-likely", "flash-fill-high"];
  const FLASH_LAYERS = ["flash-fill-likely", "flash-line-likely", "flash-fill-high", "flash-line-high"];

  // Flash-flood models listed in the panel's "Flash Floods" dropdown. Only Flood
  // Hub exists today; adding a model here (with its own layer/toggle wiring)
  // extends the list. `legend` describes the polygon types the model draws, and
  // matches the on-map styling (fill + outline, dashed for the lower tier).
  const FLASH_MODELS = [
    {
      key: "flood_hub",
      label: "Flood Hub",
      legend: [
        {type: "likely", label: "Likely", fill: "#a78bfa", outline: "#7c3aed", dashed: true},
        {type: "highly_likely", label: "Highly likely", fill: "#6d28d9", outline: "#4c1d95", dashed: false},
      ],
    },
  ];
  // polygon_type -> its legend entry, so a hovered flash polygon can show the
  // matching legend attribute in a tooltip.
  const FLASH_LEGEND_BY_TYPE = {};
  FLASH_MODELS.forEach((fm) => fm.legend.forEach((L) => { FLASH_LEGEND_BY_TYPE[L.type] = L; }));

  // The flash polygon the user last clicked (null = standby). Its properties fill
  // the Flood Hub tile, just as a selected basin fills the river tiles. The id is
  // tracked separately to carry the `selected` feature-state on the map.
  let selectedFlash = null;
  let selectedFlashId = null;

  // Chance label from polygon_type ("likely" -> "Likely"), via the shared legend.
  const flashChanceLabel = (t) => (FLASH_LEGEND_BY_TYPE[t] || {}).label || t || "—";

  // Human "Near" value for a river forecast (a single reach/gauge point, so it sits
  // in exactly one district): the district, else the country, else a dash.
  function nearLabel(name, count, country) {
    name = (name || "").trim();
    const n = Number(count) || 0;
    country = (country || "").trim();
    if (name && n > 1) return `${name} + ${n - 1} district${n - 1 === 1 ? "" : "s"}`;
    return name || country || "—";
  }

  // A short comma list, truncated after three (e.g. "India, Nepal, China +2").
  function shortList(arr) {
    const a = (arr || []).filter(Boolean);
    return a.length <= 3 ? a.join(", ") : `${a.slice(0, 3).join(", ")} +${a.length - 3}`;
  }

  // Tiered "Near" value for a flash polygon, from the ADM1/ADM2 facts build_flash
  // stamps on it: the country names when it spans more than one country, the ADM1
  // region (with a count) when it spans several regions of one country, otherwise
  // the ADM2 district (with a count of the extra districts it touches).
  function flashNearLabel(p) {
    const countries = (p.countries || []).filter(Boolean);
    const district = (p.district || "").trim();
    const region = (p.region || "").trim();
    const dc = Number(p.district_count) || 0;
    const rc = Number(p.region_count) || 0;
    if (countries.length > 1) return shortList(countries);
    if (rc > 1 && region) return `${region} + ${rc - 1} region${rc - 1 === 1 ? "" : "s"}`;
    if (district) return dc > 1 ? `${district} + ${dc - 1} district${dc - 1 === 1 ? "" : "s"}` : district;
    if (region) return region;
    return countries[0] || "—";
  }

  // Format an ISO issue time in the viewer's local timezone, e.g. "Aug 20, 12:00 MDT".
  function fmtFlashIssued(iso) {
    if (!iso) return "—";
    const d = new Date(iso);
    if (isNaN(d.getTime())) return String(iso);
    return d.toLocaleString(undefined, {month: "short", day: "numeric",
      hour: "2-digit", minute: "2-digit", hour12: false, timeZoneName: "short"});
  }

  function unitLabel(props) {
    // Fall back to the per-feature tag if a file predates the `kind` member.
    if (props && props.basin_id && dataset.unit === "Area") return "Basin";
    return dataset.unit;
  }

  // Compact counts for the impact tiles: 8_181_280 -> "8.2M".
  function fmtCount(n) {
    if (n === null || n === undefined || !isFinite(n)) return "—";
    if (n >= 1e9) return (n / 1e9).toFixed(1).replace(/\.0$/, "") + "B";
    if (n >= 1e6) return (n / 1e6).toFixed(1).replace(/\.0$/, "") + "M";
    if (n >= 1e3) return (n / 1e3).toFixed(1).replace(/\.0$/, "") + "k";
    return Math.round(n).toLocaleString();
  }

  function fmtValue(key, val) {
    if (val === undefined || val === null || val === "") return "—";
    if (key === "returnPeriodYr") return val + "-year";
    if (key === "peakDischargeCms") return val + " m³/s";
    if (key.endsWith("Time")) {
      const d = new Date(val);
      if (!isNaN(d)) {
        return d.toLocaleString(undefined, {
          month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
          hour12: false, timeZoneName: "short",
        });
      }
    }
    return String(val);
  }

  // ---- Base maps ------------------------------------------------------------

  // Every base map here is openly licensed and free to use with attribution.
  // Tile sources carry only their own imagery credit; the grid/basin credit sits
  // on the GeoJSON source instead, so it survives base-layer switches and can be
  // written from whatever the data turns out to be.
  const OSM_ATTR = '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors';
  // MapLibre has no `{s}` placeholder; listing the shards as separate URLs is how
  // it spreads requests across hostnames.
  const shards = (subdomains, url) => subdomains.map((s) => url.replace("{s}", s));

  function lonLatToTile(lon, lat, z) {
    const n = 2 ** z;
    const latRad = (lat * Math.PI) / 180;
    return {
      z,
      x: Math.floor(((lon + 180) / 360) * n),
      y: Math.floor(((1 - Math.log(Math.tan(latRad) + 1 / Math.cos(latRad)) / Math.PI) / 2) * n),
    };
  }

  // Each base map gets a thumbnail preview, gallery-style. The thumbnail is just
  // one real tile of the same location from that layer's own source, so every
  // preview shows the area in that layer's actual style.
  // San Francisco Bay at z11 — a recognizable mix of city, water, and bridges.
  const THUMB = lonLatToTile(-122.40, 37.80, 11);

  // Basemap set mirrored from the River Forecast System companion app. All are free,
  // no-key raster tile services (Esri's "Environment" basemap is a vector style that
  // needs an API key, so it's left out for now). Esri/USGS serve tiles in {z}/{y}/{x}
  // order, so their thumbnails use z/y/x too.
  const ESRI_ATTR = '&copy; <a href="https://www.esri.com">Esri</a>';
  const ESRI = "https://server.arcgisonline.com/ArcGIS/rest/services";
  const esriThumb = (svc) => `${ESRI}/${svc}/MapServer/tile/${THUMB.z}/${THUMB.y}/${THUMB.x}`;

  const BASEMAPS = [
    {
      id: "esri-light",
      name: "Light grey (Esri)",
      thumb: esriThumb("Canvas/World_Light_Gray_Base"),
      source: {
        type: "raster",
        tiles: [`${ESRI}/Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}`],
        tileSize: 256, maxzoom: 16,
        attribution: `${ESRI_ATTR}, HERE, Garmin, &copy; ${OSM_ATTR}, and the GIS User Community`,
      },
    },
    {
      id: "esri-dark",
      name: "Dark grey (Esri)",
      thumb: esriThumb("Canvas/World_Dark_Gray_Base"),
      source: {
        type: "raster",
        tiles: [`${ESRI}/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}`],
        tileSize: 256, maxzoom: 16,
        attribution: `${ESRI_ATTR}, HERE, Garmin, &copy; ${OSM_ATTR}, and the GIS User Community`,
      },
    },
    {
      id: "esri-imagery",
      name: "Imagery (Esri)",
      thumb: esriThumb("World_Imagery"),
      source: {
        type: "raster",
        tiles: [`${ESRI}/World_Imagery/MapServer/tile/{z}/{y}/{x}`],
        tileSize: 256, maxzoom: 19,
        attribution: `${ESRI_ATTR}, Maxar, Earthstar Geographics, and the GIS User Community`,
      },
    },
    {
      id: "esri-imagery-labels",
      name: "Imagery + labels (Esri)",
      thumb: esriThumb("World_Imagery"),
      source: {
        type: "raster",
        tiles: [`${ESRI}/World_Imagery/MapServer/tile/{z}/{y}/{x}`],
        tileSize: 256, maxzoom: 19,
        attribution: `${ESRI_ATTR}, Maxar, Earthstar Geographics, and the GIS User Community`,
      },
      // Boundaries + place names, drawn directly on top of the imagery. This layer
      // is toggled together with the imagery beneath it (see basemapLayerIds).
      overlay: {
        type: "raster",
        tiles: [`${ESRI}/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}`],
        tileSize: 256, maxzoom: 13,
      },
    },
    {
      id: "esri-topo",
      name: "Topographic (Esri)",
      thumb: esriThumb("World_Topo_Map"),
      source: {
        type: "raster",
        tiles: [`${ESRI}/World_Topo_Map/MapServer/tile/{z}/{y}/{x}`],
        tileSize: 256, maxzoom: 19,
        attribution: `${ESRI_ATTR}, HERE, Garmin, FAO, NOAA, USGS, ${OSM_ATTR}, and the GIS User Community`,
      },
    },
    {
      id: "usgs-topo",
      name: "Topographic (USGS)",
      thumb: `https://basemap.nationalmap.gov/arcgis/rest/services/USGSTopo/MapServer/tile/${THUMB.z}/${THUMB.y}/${THUMB.x}`,
      source: {
        type: "raster",
        tiles: ["https://basemap.nationalmap.gov/arcgis/rest/services/USGSTopo/MapServer/tile/{z}/{y}/{x}"],
        tileSize: 256, maxzoom: 16,
        attribution: 'Tiles courtesy of the <a href="https://www.usgs.gov/">U.S. Geological Survey</a>',
      },
    },
    {
      id: "osm",
      name: "OpenStreetMap",
      thumb: `https://a.tile.openstreetmap.org/${THUMB.z}/${THUMB.x}/${THUMB.y}.png`,
      source: {
        type: "raster",
        tiles: shards(["a", "b", "c"], "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"),
        tileSize: 256, maxzoom: 19, attribution: OSM_ATTR,
      },
    },
    {
      id: "opentopomap",
      name: "OpenTopoMap",
      thumb: `https://a.tile.opentopomap.org/${THUMB.z}/${THUMB.x}/${THUMB.y}.png`,
      source: {
        type: "raster",
        tiles: shards(["a", "b", "c"], "https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png"),
        tileSize: 256, maxzoom: 17,
        attribution: `${OSM_ATTR}, SRTM | &copy; <a href="https://opentopomap.org">OpenTopoMap</a> (CC-BY-SA)`,
      },
    },
  ];
  const DEFAULT_BASEMAP = "esri-light";

  // A basemap is one raster layer, except Imagery + labels, which pairs the imagery
  // with a labels overlay drawn directly above it; both share one toggle.
  const basemapLayerIds = (b) => (b.overlay ? [b.id, `${b.id}-overlay`] : [b.id]);

  // ---- Map ------------------------------------------------------------------

  // A single copy of the world. The data only exists once, so `renderWorldCopies`
  // stays off; otherwise you can scroll into empty repeated tiles where no
  // cells/basins are drawn. That alone clamps panning to one world width, which
  // is what Leaflet needed `maxBounds` for — and just as well, because a
  // whole-world `maxBounds` crashes maplibre-gl 6.2.0 inside the constructor's
  // first resize (a small regional one is fine).
  const map = new MapLibreMap({
    container: "map",
    style: {
      version: 8,
      sources: Object.fromEntries(BASEMAPS.flatMap((b) =>
        b.overlay ? [[b.id, b.source], [`${b.id}-overlay`, b.overlay]] : [[b.id, b.source]])),
      // Every base map lives in the style at once; switching just flips which one
      // is visible, so no restyle and no re-fetch of tiles already cached. Only the
      // visible one's attribution shows.
      layers: BASEMAPS.flatMap((b) =>
        basemapLayerIds(b).map((lid) => ({
          id: lid,
          type: "raster",
          source: lid,
          layout: {visibility: b.id === DEFAULT_BASEMAP ? "visible" : "none"},
        }))),
    },
    center: [0, 20],
    zoom: 1,
    minZoom: 1,
    maxZoom: 19,
    renderWorldCopies: false,
    dragRotate: false,
    pitchWithRotate: false,
    attributionControl: false,
  });
  map.touchZoomRotate.disableRotation();
  map.addControl(new NavigationControl({showCompass: false}), "top-left");
  map.addControl(new AttributionControl({compact: false}), "bottom-right");

  // ---- Shared dropdown controls (top-right column) --------------------------
  // Each is an icon button that opens a panel to its left. Opening one closes the
  // others; the button's `title` shows the tool's name on hover.
  const openDropdowns = [];
  function closeOtherDropdowns(self) {
    openDropdowns.forEach((close) => { if (close !== self) close(); });
  }

  function dropdownControl(opts) {
    // opts: { iconName, title, side?, panelStyle?, render(panel), onReady?(container) }
    // side "left" = control sits in a left corner, so its panel/tip open to the
    // right; default (any other value) opens to the left for right-corner controls.
    const openRight = opts.side === "left";
    const panelAnchor = openRight ? "left:34px" : "right:34px";
    const tipAnchor = openRight ? "left:36px" : "right:36px";
    let container = null, panel = null, closeOnDoc = null;
    const close = () => { if (panel && !panel.hidden) panel.hidden = true; };
    function toggle() {
      if (panel.hidden) { closeOtherDropdowns(close); panel.hidden = false; }
      else close();
    }
    return {
      onAdd() {
        container = document.createElement("div");
        container.className = "maplibregl-ctrl";
        container.style.cssText = "position:relative;background:#fff;border-radius:4px;box-shadow:0 0 0 2px rgb(0 0 0 / .1)";
        const btn = document.createElement("button");
        btn.type = "button";
        btn.setAttribute("aria-label", opts.title);
        btn.style.cssText = "display:flex;align-items:center;justify-content:center;width:29px;height:29px;cursor:pointer;background:none;border:0";
        btn.innerHTML = icon(opts.iconName, "text-[19px] text-slate-700");
        panel = document.createElement("div");
        panel.hidden = true;
        panel.style.cssText = "position:absolute;top:0;" + panelAnchor + ";background:#fff;border-radius:6px;" +
          "box-shadow:0 1px 6px rgb(0 0 0 / .3);" + (opts.panelStyle || "padding:6px");
        // Name label that pops up alongside on hover (native title is too slow).
        const tip = document.createElement("div");
        tip.textContent = opts.title;
        tip.style.cssText = "position:absolute;" + tipAnchor + ";top:50%;transform:translateY(-50%);white-space:nowrap;" +
          "background:#0f172a;color:#fff;font:600 11px system-ui,sans-serif;padding:3px 7px;border-radius:5px;" +
          "pointer-events:none;opacity:0;transition:opacity .1s;box-shadow:0 1px 4px rgb(0 0 0 / .3)";
        btn.addEventListener("mouseenter", () => { if (panel.hidden) tip.style.opacity = "1"; });
        btn.addEventListener("mouseleave", () => { tip.style.opacity = "0"; });
        container.append(btn, panel, tip);
        opts.render(panel);
        btn.addEventListener("click", toggle);
        container.addEventListener("click", (e) => e.stopPropagation());
        closeOnDoc = () => close();
        document.addEventListener("click", closeOnDoc);
        openDropdowns.push(close);
        if (opts.onReady) opts.onReady(container, panel);
        return container;
      },
      onRemove() {
        document.removeEventListener("click", closeOnDoc);
        const i = openDropdowns.indexOf(close);
        if (i >= 0) openDropdowns.splice(i, 1);
        container.remove();
      },
    };
  }

  // Base-map switcher: a gallery of thumbnails.
  function basemapControl() {
    return dropdownControl({
      iconName: "basemap",
      title: "Base Map Layers",
      panelStyle: "padding:0 4px 4px",
      render(panel) {
        panel.innerHTML = BASEMAPS.map((b) =>
          `<button type="button" class="basemap-row" data-basemap="${b.id}">` +
          `<img src="${b.thumb}" alt="" loading="lazy"><span>${b.name}</span></button>`).join("");
        const rows = [...panel.querySelectorAll(".basemap-row")];
        const highlight = (id) => rows.forEach((r) => r.classList.toggle("basemap-selected", r.dataset.basemap === id));
        rows.forEach((r) => r.addEventListener("click", () => {
          BASEMAPS.forEach((b) => {
            const vis = b.id === r.dataset.basemap ? "visible" : "none";
            basemapLayerIds(b).forEach((lid) => map.setLayoutProperty(lid, "visibility", vis));
          });
          highlight(r.dataset.basemap);
        }));
        highlight(DEFAULT_BASEMAP);
      },
    });
  }

  // Current resolution readout, bottom-left.
  map.addControl({
    onAdd() {
      this._el = document.createElement("div");
      this._el.className = "maplibregl-ctrl res-readout";
      this._el.id = "res-readout";
      this._el.textContent = dataset.resLabel + " —";
      return this._el;
    },
    onRemove() {
      this._el.remove();
    },
  }, "bottom-left");

  function updateResReadout(res) {
    const el = document.getElementById("res-readout");
    if (el) el.textContent = dataset.resLabel + " " + res;
  }

  // Severity legend (Warning/Danger/Extreme + the Multi-model hash), shown under
  // the River Floods section title.
  function severityLegendHtml() {
    return `<div class="flex flex-wrap gap-x-3 gap-y-1 mb-3">` +
      Object.keys(SEVERITY).filter((k) => k !== "none").map((k) =>
        `<span class="flex items-center gap-1.5 text-[11px] font-semibold text-slate-700">` +
        `<span class="w-3 h-3 rounded-sm border border-black/30" style="background:${SEVERITY[k].color}"></span>${SEVERITY[k].label}</span>`
      ).join("") +
      `<span class="flex items-center gap-1.5 text-[11px] font-semibold text-slate-700">` +
      `<span class="inline-flex w-3 h-3 rounded-sm border border-slate-900 overflow-hidden">` +
      `<svg viewBox="0 0 14 14" width="12" height="12" aria-hidden="true"><g stroke="${HASH_COLOR}" stroke-width="1.3">` +
      `<line x1="0" y1="5" x2="5" y2="0"/><line x1="0" y1="10" x2="10" y2="0"/>` +
      `<line x1="4" y1="14" x2="14" y2="4"/><line x1="9" y1="14" x2="14" y2="9"/></g></svg></span>Multi-model</span></div>`;
  }

  // Flash-flood tier legend (Likely / Highly likely), shared by all flash models,
  // shown under the Flash Floods section title.
  function flashLegendHtml() {
    const tiers = (FLASH_MODELS[0] && FLASH_MODELS[0].legend) || [];
    return `<div class="flex flex-wrap gap-x-3 gap-y-1 mb-3">` +
      tiers.map((L) =>
        `<span class="flex items-center gap-1.5 text-[11px] font-semibold text-slate-700">` +
        `<span class="inline-block w-3 h-3 rounded-[3px] shrink-0" style="background:${L.fill};border:1.5px ${L.dashed ? "dashed" : "solid"} ${L.outline}"></span>${L.label}</span>`
      ).join("") + `</div>`;
  }

  const panelEmpty = document.getElementById("panel-empty");
  const panelContent = document.getElementById("panel-content");

  const MODEL_HOME = {
    geoglows: "https://hydroviewer.geoglows.org/",
    flood_hub: "https://sites.research.google/floods/",
  };
  const PANEL_MODELS = ["geoglows", "flood_hub"];

  // The panel always lists both models. A model shows a full card when the selected
  // feature carries its forecast, otherwise a shrunken "standby" card (name linked to
  // its own app). Flash-flood toggles live in their own section near the bottom.
  function renderPanel(props) {
    panelEmpty.hidden = true;
    panelContent.hidden = false;
    const selected = !!props;

    const badge = (sev, color) =>
      `<span class="inline-block px-2.5 py-0.5 rounded-full text-[11px] font-semibold capitalize text-[#10161d]" style="background:${color}">${sev || "—"}</span>`;

    // A chevron that collapses the section body with matching id (wired after render).
    const collapseBtn = (target, cls = "text-slate-400 hover:text-slate-600") =>
      `<button type="button" class="collapse-btn shrink-0 ${cls}" data-target="${target}" aria-label="Collapse section">` +
      `${icon("chevron-down", "text-[13px] transition-transform")}</button>`;

    function modelTitle(m, fc) {
      const label = modelLabel(m);
      const href = (fc && modelLink(fc)) || MODEL_HOME[m];
      return `<a href="${href}" target="_blank" rel="noopener noreferrer" title="Open ${label}"
         class="inline-flex items-center gap-1 text-sky-300 hover:text-sky-200 hover:underline">${label}${
        icon("arrow-top-right-on-square", "text-[11px] opacity-80")}</a>`;
    }
    function modelTile(m) {
      const fcs = selected ? props.forecasts.filter((f) => f.model === m) : [];
      const has = fcs.length > 0;
      const titleSpan =
        `<span class="flex items-center gap-1.5 font-semibold text-[13px] capitalize ${has ? "text-slate-100" : "text-slate-400"}">` +
        icon("chart-bar", has ? "text-sky-300" : "text-slate-500") + modelTitle(m, fcs[0]) + `</span>`;
      const hide = visibleModels.has(m) ? "" : " hidden";
      if (!has) {
        return `<div id="tile-river-${m}" class="bg-[#141e2a] border border-slate-700/60 rounded-[10px] px-3.5 py-2.5 mb-3${hide}">` +
          `<div class="flex items-center justify-between gap-2 mb-1.5">${titleSpan}</div>` +
          `<div class="text-slate-500 text-[12px]">On standby — ${selected ? "no forecast for this area" : "click a basin on the map"}.</div></div>`;
      }
      const bodyId = `sec-model-${m}`;
      const head =
        `<div class="flex items-center justify-between gap-2 mb-1.5">${titleSpan}` +
        collapseBtn(bodyId, "text-slate-400 hover:text-slate-200") + `</div>`;
      const body = fcs.map((fc, i) => {
        const rows = FIELD_LABELS.filter(([k]) => k !== "historicalComparison").map(([k, label]) => {
          const lbl = k === "riverId" ? (RIVER_ID_LABEL[m] || label) : label;
          const dt = `<dt class="text-slate-400">${lbl}</dt>`;
          if (k === "severity")
            return `${dt}<dd class="m-0">${badge(fc.severity, sevColor(fc.severity))}</dd>`;
          const val = k === "district"
            ? nearLabel(fc.district, fc.district_count, fc.country)
            : fmtValue(k, fc[k]);
          return `${dt}<dd class="m-0 text-slate-100 break-words">${val}</dd>`;
        }).join("");
        const note = fc.historicalComparison
          ? `<div class="flex items-start gap-1.5 text-xs text-slate-400 italic mt-2">${icon("clock", "text-sm mt-0.5")}<span>“${fc.historicalComparison}”</span></div>`
          : "";
        return `<dl class="grid grid-cols-[128px_1fr] gap-x-2.5 gap-y-1 text-[12.5px]${i ? " mt-2 pt-2 border-t border-slate-700/50" : ""}">${rows}</dl>${note}`;
      }).join("");
      return `<div id="tile-river-${m}" class="bg-[#1b2a3a] border border-slate-700 border-l-4 rounded-[10px] px-3.5 py-3 mb-3${hide}" style="border-left-color:${sevColor(worstSeverity(fcs))}">` +
        head + `<div id="${bodyId}">` + body + `</div></div>`;
    }

    // Forecast (river) tiles: always list PANEL_MODELS; the River Floods filter
    // hides the ones switched off (see the filter wiring below).
    const tiles = PANEL_MODELS.map(modelTile).join("");

    // Section heading (the largest text in the panel) with a collapse chevron.
    const sectionHeader = (title, target, iconName) =>
      `<h2 class="flex items-center justify-between text-slate-900 font-bold text-[19px] mb-2.5">` +
      `<span class="flex items-center gap-1.5">${iconName ? icon(iconName, "text-slate-500 text-sm") : ""}${title}</span>` +
      collapseBtn(target) + `</h2>`;

    // "All models"-style multi-select filter, shared by both sections.
    const filterLabel = (models, set) => {
      const n = models.filter((m) => set.has(m)).length;
      return n === models.length ? "All models" : n === 0 ? "No models" : `${n} of ${models.length} models`;
    };
    const filterDropdown = (kind, models, set) =>
      `<div class="filter-dd relative mb-3" data-kind="${kind}">` +
      `<button type="button" class="dd-btn w-full flex items-center justify-between gap-2 px-3 py-2 rounded-lg border border-slate-300 bg-white text-[13px] font-medium text-slate-700 hover:bg-slate-50">` +
      `<span class="flex items-center gap-1.5">${icon("funnel", "text-sky-500")}<span class="dd-label">${filterLabel(models, set)}</span></span>` +
      `<span class="dd-caret inline-flex transition-transform">${icon("chevron-down", "text-slate-400")}</span></button>` +
      `<div class="dd-menu hidden absolute z-[1000] left-0 right-0 mt-1 rounded-lg border border-slate-200 bg-white shadow-lg p-1.5">` +
      models.map((m) =>
        `<label class="flex items-center gap-2 px-2 py-1.5 rounded hover:bg-slate-50 text-[13px] text-slate-700 cursor-pointer select-none">` +
        `<input type="checkbox" data-model="${m}"${set.has(m) ? " checked" : ""} class="accent-sky-500 w-3.5 h-3.5">${modelLabel(m)}</label>`
      ).join("") + `</div></div>`;

    // A flash-flood model tile (dark card, violet accent): linked name, then the
    // attributes of the selected flash polygon (or a standby line). Interactive
    // like the river tiles — clicking a polygon fills its model's tile. Its
    // collapse body is `sec-flashmodel-<key>`.
    const flashModelTile = (fm) => {
      const bodyId = `sec-flashmodel-${fm.key}`;
      const link = MODEL_HOME[fm.key] || "#";
      const title =
        `<a href="${link}" target="_blank" rel="noopener noreferrer" title="Open ${fm.label}" ` +
        `class="inline-flex items-center gap-1 text-sky-300 hover:text-sky-200 hover:underline">${fm.label}` +
        icon("arrow-top-right-on-square", "text-[11px] opacity-80") + `</a>`;
      // Flood Hub is the only flash model today, so it carries the current selection.
      const p = fm.key === "flood_hub" ? selectedFlash : null;
      let body;
      if (p) {
        const rows = [
          ["Chance", flashChanceLabel(p.polygon_type)],
          ["Near", flashNearLabel(p)],
          ["Issued", fmtFlashIssued(p.forecastIssueTime)],
          ["Forecast window", p.forecastPeriodHours ? `Next ${p.forecastPeriodHours} hours` : "—"],
        ].map(([k, v]) =>
          `<dt class="text-slate-400">${k}</dt><dd class="m-0 text-slate-100 break-words">${v}</dd>`
        ).join("");
        body = `<dl class="grid grid-cols-[118px_1fr] gap-x-2.5 gap-y-1 text-[12.5px]">${rows}</dl>`;
      } else {
        body = `<div class="text-slate-500 text-[12px]">On standby — click a flash flood area on the map.</div>`;
      }
      // Collapse arrow and the violet left-accent only appear once a polygon is
      // selected; on standby the tile is a plain card.
      const head =
        `<div class="flex items-center justify-between gap-2 mb-1.5">` +
        `<span class="flex items-center gap-1.5 font-semibold text-[13px] text-slate-100">${icon("bolt", "text-violet-300")}${title}</span>` +
        (p ? collapseBtn(bodyId, "text-slate-400 hover:text-slate-200") : "") + `</div>`;
      const hide = visibleFlashModels.has(fm.key) ? "" : " hidden";
      const accent = p ? ` border-l-4` : "";
      const accentStyle = p ? ` style="border-left-color:#7c3aed"` : "";
      return `<div id="tile-flash-${fm.key}" class="bg-[#1b2a3a] border border-slate-700${accent} rounded-[10px] px-3.5 py-3 mb-3${hide}"${accentStyle}>` +
        head + `<div id="${bodyId}">` + body + `</div></div>`;
    };

    let head;
    if (selected) {
      const worst = (props.severity || "").toLowerCase();
      const worstColor = sevColor(worst);
      head =
        `<h2 class="flex items-center gap-2 text-slate-800 font-semibold text-[15px] mb-0.5">` +
        `<span class="inline-flex" style="color:${worstColor}">${icon("lasso")}</span> ${unitLabel(props)} ${props.cell_id} ${badge(worst, worstColor)}</h2>` +
        `<div class="mb-3.5"><span class="text-slate-500 text-xs">${props.model_count} forecast${props.model_count === 1 ? "" : "s"}</span></div>`;
    } else {
      // No basin selected: the idle river tiles already carry the instruction,
      // so the section needs no header prompt of its own.
      head = "";
    }

    const imp = selected ? props.impact : null;
    const impTile = (name, label, value, span) =>
      `<div class="${span ? "col-span-2 " : ""}rounded-lg border border-slate-200 bg-slate-50 px-2.5 py-2">` +
      `<div class="flex items-center gap-1 text-slate-400 text-[10px] font-semibold uppercase tracking-wide mb-1">${icon(name, "text-[12px]")}${label}</div>` +
      `<div class="text-slate-800 font-bold text-[15px] leading-none">${value}</div></div>`;
    const impactHtml = imp
      ? `<div class="mt-4">` +
        `<h3 class="flex items-center justify-between text-slate-800 font-semibold text-[11px] uppercase tracking-wider mb-2">` +
        `<span class="flex items-center gap-1.5">${icon("exclamation-triangle", "text-amber-500 text-sm")}Impact</span>${collapseBtn("sec-impact")}</h3>` +
        `<div id="sec-impact"><div class="grid grid-cols-2 gap-2">` +
        impTile("building-office-2", "Buildings", fmtCount(imp.buildings)) +
        impTile("area-hash-filled", "Farmland", fmtCount(imp.farmland_m2 / 1e6) + " km²") +
        impTile("car-travel-mode", "Roads", fmtCount(imp.highway_km) + " km") +
        impTile("train-travel-mode", "Railways", fmtCount(imp.railway_km) + " km") +
        impTile("users", "Population", fmtCount(imp.population), true) +
        `</div><p class="text-slate-400 text-[12px] mt-1.5">Totals across the whole basin.</p></div></div>`
      : "";

    // Section 1 — River Floods: the All-models filter, basin header, forecast
    // tiles and impact, all collapsible under one heading.
    const riverSection =
      `<div id="section-river" class="mb-4">` + sectionHeader("River Floods", "sec-river") +
      `<div id="sec-river">` + severityLegendHtml() + filterDropdown("river", PANEL_MODELS, visibleModels) +
      head + tiles + impactHtml + `</div></div>`;

    // Section 2 — Flash Floods: a global overlay. Its filter reveals a tile per
    // enabled model (and turns that model's polygons on the map).
    const flashKeys = FLASH_MODELS.map((fm) => fm.key);
    const flashSection =
      `<div id="section-flash" class="mt-12">` + sectionHeader("Flash Floods", "sec-flash") +
      `<div id="sec-flash">` + flashLegendHtml() + filterDropdown("flash", flashKeys, visibleFlashModels) +
      FLASH_MODELS.map(flashModelTile).join("") + `</div></div>`;

    panelContent.innerHTML = riverSection + flashSection;

    // Collapse arrows: toggle the target section body and rotate the chevron.
    panelContent.querySelectorAll(".collapse-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        const body = document.getElementById(btn.dataset.target);
        if (!body) return;
        const collapsed = body.classList.toggle("hidden");
        const svg = btn.querySelector("svg");
        if (svg) svg.classList.toggle("-rotate-90", collapsed);
      });
    });

    // Filter dropdowns: open/close, and per-model toggle of the matching tile
    // (and, for flash, its map polygons). Outside-click close is handled globally.
    const flashSetter = {flood_hub: setFlashOn};
    panelContent.querySelectorAll(".filter-dd").forEach((dd) => {
      const isFlash = dd.dataset.kind === "flash";
      const set = isFlash ? visibleFlashModels : visibleModels;
      const models = isFlash ? flashKeys : PANEL_MODELS;
      const menu = dd.querySelector(".dd-menu");
      const caret = dd.querySelector(".dd-caret");
      const label = dd.querySelector(".dd-label");
      dd.querySelector(".dd-btn").addEventListener("click", (e) => {
        e.stopPropagation();
        const open = menu.classList.toggle("hidden");
        caret.style.transform = open ? "" : "rotate(180deg)";
      });
      dd.querySelectorAll("input[type=checkbox]").forEach((cb) => {
        cb.addEventListener("change", () => {
          const m = cb.dataset.model;
          if (cb.checked) set.add(m); else set.delete(m);
          label.textContent = filterLabel(models, set);
          const card = document.getElementById(`tile-${dd.dataset.kind}-${m}`);
          if (card) card.classList.toggle("hidden", !cb.checked);
          if (isFlash) (flashSetter[m] || (() => {}))(cb.checked);
          else refreshFeatures();
        });
      });
    });
  }

  let resolutions = [];
  const byRes = {};

  // ---- Model filters (one per panel section) --------------------------------
  // River Floods = forecast models; Flash Floods = flash-flood models. Both
  // default to all-on; each section's dropdown toggles its tiles (and, for flash,
  // the map polygons).
  const visibleModels = new Set(PANEL_MODELS);
  const visibleFlashModels = new Set(FLASH_MODELS.map((fm) => fm.key));
  const MODEL_LABELS = {geoglows: "GEOGLOWS", flood_hub: "Flood Hub"};

  function modelLabel(m) {
    return MODEL_LABELS[m] || m.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
  }

  // Deep link a single forecast back into its source application, centered on the
  // reach/gauge coordinate the pipeline now carries. Returns null when there's no
  // usable coordinate (older data, or a model we don't have a URL scheme for), so
  // the caller can fall back to a plain, unlinked title. URL patterns live here in
  // one place — if a provider changes routing, this is the only thing to update.
  const LINK_ZOOM = {geoglows: 13, flood_hub: 10};

  function modelLink(fc) {
    const lat = Number(fc && fc.lat);
    const lon = Number(fc && fc.lon);
    if (!Number.isFinite(lat) || !Number.isFinite(lon)) return null;
    const m = String((fc && fc.model) || "").toLowerCase();
    if (m === "geoglows") {
      // Hydroviewer is location-only; centering on the reach shows the stream.
      return `https://hydroviewer.geoglows.org/#lon=${lon}&lat=${lat}&zoom=${LINK_ZOOM.geoglows}&definition=`;
    }
    if (m === "flood_hub") {
      // /l/{lat}/{lng}/{zoom}[/g/{gaugeId}] — the id pins the specific gauge.
      const base = `https://sites.research.google/floods/l/${lat}/${lon}/${LINK_ZOOM.flood_hub}`;
      return fc.riverId ? `${base}/g/${encodeURIComponent(fc.riverId)}` : base;
    }
    return null;
  }

  function worstSeverity(forecasts) {
    let best = "", bestRank = -1;
    for (const fc of forecasts) {
      const info = SEVERITY[(fc.severity || "").toLowerCase()];
      const r = info ? info.rank : -1;
      if (r > bestRank) {
        bestRank = r;
        best = (fc.severity || "").toLowerCase();
      }
    }
    return best;
  }

  // ---- Cell/basin layer -----------------------------------------------------

  const SRC = "cells";
  const FILL = "cells-fill";
  const LINE = "cells-line";
  const HASH = "cells-hash";   // hash-pattern overlay marking multi-model basins
  const EMPTY_FC = {type: "FeatureCollection", features: []};

  // MapLibre flattens feature properties through its tiling pipeline, so the
  // nested `forecasts`/`impact`/`models` members come back from a rendered
  // feature as JSON strings. Keep the real objects here, keyed by the id we
  // stamp on each feature, and read everything for the panel out of this map.
  const featureById = new Map();
  let nextFeatureId = 1;

  // Keep only forecasts from visible models; drop empty cells; recolour the rest.
  function visibleFeatures(features) {
    const out = [];
    for (const f of features) {
      const fcs = f.properties.forecasts.filter((x) => visibleModels.has(x.model));
      if (!fcs.length) continue;
      const modelNames = [...new Set(fcs.map((x) => x.model))];
      const feature = {
        type: "Feature",
        id: nextFeatureId++,
        geometry: f.geometry,
        properties: Object.assign({}, f.properties, {
          forecasts: fcs, model_count: fcs.length, severity: worstSeverity(fcs),
          models: modelNames, agree: modelNames.length >= 2,
        }),
      };
      featureById.set(feature.id, feature);
      out.push(feature);
    }
    return out;
  }

  const stateOn = (key) => ["boolean", ["feature-state", key], false];
  const severityColor = [
    "match", ["coalesce", ["get", "severity"], ""],
    ...Object.entries(SEVERITY).flatMap(([k, v]) => [k, v.color]),
    DEFAULT_COLOR,
  ];
  // A darker shade of a hex colour (multiply each channel).
  function darken(hex, f = 0.55) {
    const n = parseInt(hex.slice(1), 16);
    const c = (sh) => Math.round(((n >> sh) & 255) * f).toString(16).padStart(2, "0");
    return "#" + c(16) + c(8) + c(0);
  }
  // Like severityColor, but a darker shade of each level — used for the outline
  // and hash of multi-model basins so they read as that polygon's own colour.
  const darkSeverityColor = [
    "match", ["coalesce", ["get", "severity"], ""],
    ...Object.entries(SEVERITY).flatMap(([k, v]) => [k, darken(v.color)]),
    darken(DEFAULT_COLOR),
  ];

  // Hash tile: fine diagonal lines in the given colour, marking agreement basins.
  // One image per severity (its darker shade). Rendered at pixelRatio 2 so it
  // stays crisp on retina screens, then used as a data-driven fill-pattern.
  function makeHashImage(color, size = 16, w = 2) {
    const cv = document.createElement("canvas");
    cv.width = cv.height = size;
    const g = cv.getContext("2d");
    g.lineWidth = w;
    g.lineCap = "square";
    g.strokeStyle = color;
    for (const o of [-size, 0, size]) {   // wraps keep the tile seamless
      g.beginPath();
      g.moveTo(o, size);
      g.lineTo(size + o, 0);
      g.stroke();
    }
    return g.getImageData(0, 0, size, size);
  }
  const hashImageId = (sev) => "mm-hash-" + sev;
  function ensureHashImages() {
    for (const [k, v] of Object.entries(SEVERITY)) {
      const id = hashImageId(k);
      if (!map.hasImage(id)) map.addImage(id, makeHashImage(darken(v.color)), {pixelRatio: 2});
    }
  }

  function addCellLayers() {
    const source = {type: "geojson", data: EMPTY_FC};
    if (dataset.attribution) source.attribution = dataset.attribution;
    map.addSource(SRC, source);
    map.addLayer({
      id: FILL,
      type: "fill",
      source: SRC,
      paint: {
        "fill-color": severityColor,
        "fill-opacity": [
          "case",
          stateOn("selected"), 0.2,
          stateOn("hover"), 0.6,
          0.3,
        ],
      },
    });
    // Multi-model (agreement) basins get a subtle diagonal hash overlay in a
    // darker shade of the basin's own severity colour, matching its outline.
    ensureHashImages();
    map.addLayer({
      id: HASH,
      type: "fill",
      source: SRC,
      filter: ["==", ["get", "agree"], true],
      paint: {
        "fill-pattern": ["match", ["coalesce", ["get", "severity"], ""],
          ...Object.keys(SEVERITY).flatMap((k) => [k, hashImageId(k)]),
          hashImageId("danger")],
        "fill-opacity": 0.55,
      },
    });
    map.addLayer({
      id: LINE,
      type: "line",
      source: SRC,
      paint: {
        "line-color": [
          "case",
          stateOn("selected"), "#ffffff",
          ["get", "agree"], darkSeverityColor,
          severityColor,
        ],
        "line-width": [
          "case",
          stateOn("selected"), 4,
          stateOn("hover"), 3,
          ["get", "agree"], 2.5,
          1,
        ],
        "line-opacity": ["case", ["get", "agree"], 1, 0.85],
      },
    });
  }

  // Leaflet handed us `layer.getBounds()`; with MapLibre we walk the coordinates.
  function boundsOf(features) {
    const b = new LngLatBounds();
    const walk = (c) => (Array.isArray(c[0]) ? c.forEach(walk) : b.extend(c));
    for (const f of features) if (f.geometry) walk(f.geometry.coordinates);
    return b;
  }

  const tooltip = new Popup({
    closeButton: false, closeOnClick: false, className: "cell-tooltip", offset: 12, maxWidth: "340px",
  });

  // Hover label: just the identifier (basin number) and its severity.
  function tooltipHtml(p) {
    return `${unitLabel(p)} ${p.cell_id} · <b>${p.severity || "?"}</b>`;
  }

  let hoveredId = null;
  let selectedId = null;

  // Re-render the panel keeping the current basin selection. Used when only the
  // flash selection changed, so the Flood Hub tile updates without disturbing
  // the river section.
  function rerenderPanel() {
    const f = selectedId != null ? featureById.get(selectedId) : null;
    renderPanel(f ? f.properties : null);
  }

  // Bring a panel section into view after a selection (the panel just re-rendered,
  // so wait a frame for layout). `sectionId` is section-river / section-flash.
  function scrollPanelToSection(sectionId) {
    requestAnimationFrame(() => {
      const el = document.getElementById(sectionId);
      if (el) el.scrollIntoView({behavior: "smooth", block: "start"});
    });
  }

  function setFeatureState(id, patch) {
    if (id != null) map.setFeatureState({source: SRC, id}, patch);
  }

  function setHover(id) {
    if (hoveredId === id) return;
    setFeatureState(hoveredId, {hover: false});
    hoveredId = id;
    setFeatureState(hoveredId, {hover: true});
  }

  function clearSelection() {
    setFeatureState(selectedId, {selected: false});
    selectedId = null;
    selectedBasinId = null;
    refreshContext();                 // hide any per-basin context
    renderPanel(null);                // standby tiles (both models still listed)
  }

  function selectFeature(id) {
    const feature = featureById.get(id);
    if (!feature) return;
    setFeatureState(selectedId, {selected: false});
    selectedId = id;
    setFeatureState(selectedId, {selected: true});
    renderPanel(feature.properties);
    scrollPanelToSection("section-river");   // reveal the river tiles just filled
    selectedBasinId = String(feature.properties.cell_id);
    refreshContext();                 // reveal this basin's streams/districts (if armed)
    zoomToSelection(feature);          // frame per the "Zoom to" choice
  }

  function bindCellInteractions() {
    map.on("mousemove", FILL, (e) => {
      const f = e.features[0];
      if (!f) return;
      map.getCanvas().style.cursor = "pointer";
      setHover(f.id);
      // Only a flash polygon on top outranks the basin tooltip; the basin beats
      // districts (which sit behind it).
      if (flashUnderCursor(e.point)) return;
      tooltip.setLngLat(e.lngLat).setHTML(tooltipHtml(featureById.get(f.id).properties)).addTo(map);
    });
    map.on("mouseleave", FILL, () => {
      map.getCanvas().style.cursor = "";
      setHover(null);
      tooltip.remove();
    });
    // Only the finest telescoping level carries statistics. Clicking a finer
    // cell opens its panel; clicking a coarser cell drills in (zooms to its
    // extent to reveal the next level up); clicking the basemap deselects.
    map.on("click", (e) => {
      // A flash polygon under the cursor handles its own click (fit-to-screen);
      // don't drill cells or deselect underneath it.
      if (flashUnderCursor(e.point)) return;
      const hits = map.queryRenderedFeatures(e.point, {layers: [FILL]});
      const f = hits.length ? featureById.get(hits[0].id) : null;
      // Clicking off every polygon (no basin here, and no flash — that returns
      // above) closes all tiles: clear the flash selection too, then the basin.
      if (!f) { selectedFlash = null; setFlashSelected(null); clearSelection(); return; }
      if (Number(f.properties.res) === Number(finestRes())) selectFeature(hits[0].id);
      else drillInto(f);
    });
  }

  // ---- Resolution switching -------------------------------------------------

  const fcByRes = {};

  function fcFor(res) {
    if (!fcByRes[res]) {
      fcByRes[res] = {
        type: "FeatureCollection",
        features: visibleFeatures(byRes[String(res)].features),
      };
    }
    return fcByRes[res];
  }

  // resolutions is sorted ascending, so the last entry is the finest level.
  const finestRes = () => (resolutions.length ? resolutions[resolutions.length - 1] : null);

  // The minimum zoom at which a resolution appears. A dataset can override the
  // per-level thresholds (see DATASETS.zoomByRes); otherwise every level steps
  // up uniformly from RES_START_ZOOM.
  function resZoomThreshold(res, index) {
    return dataset.zoomByRes && dataset.zoomByRes[res] != null
      ? dataset.zoomByRes[res]
      : RES_START_ZOOM + index * RES_ZOOM_STEP;
  }

  function zoomToRes(zoom) {
    let chosen = resolutions[0];
    resolutions.forEach((r, i) => {
      if (zoom >= resZoomThreshold(r, i)) chosen = r;
    });
    return chosen;
  }

  // Clicking a coarser (non-finest) cell drills in: zoom to that cell's extent,
  // and at least to the next level's threshold, so the finer cells inside it
  // come into view. The normal zoom-driven telescoping then takes over.
  function drillInto(feature) {
    const b = boundsOf([feature]);
    if (b.isEmpty()) return;
    const i = resolutions.indexOf(Number(feature.properties.res));
    const next = (i >= 0 && i < resolutions.length - 1) ? resolutions[i + 1] : null;
    const cam = map.cameraForBounds(b, {padding: 40});
    let zoom = cam ? cam.zoom : map.getZoom();
    if (next != null) zoom = Math.max(zoom, resZoomThreshold(next, i + 1) + 0.05);
    map.easeTo({center: cam ? cam.center : b.getCenter(), zoom, duration: 600});
  }

  let currentRes = null;

  function showRes(res) {
    if (res === currentRes) return;
    clearSelection();
    setHover(null);
    tooltip.remove();
    map.getSource(SRC).setData(fcFor(res));
    currentRes = res;
    updateResReadout(res);
  }

  // Rebuild the current data after a model toggle (cached collections are stale).
  function refreshFeatures() {
    for (const k in fcByRes) delete fcByRes[k];
    featureById.clear();
    const res = currentRes;
    currentRes = null;
    if (res != null) showRes(res);
  }

  // One document-level handler closes any open filter-dropdown menu on an
  // outside click (the per-menu wiring in renderPanel only handles open/toggle).
  document.addEventListener("click", (e) => {
    document.querySelectorAll(".filter-dd").forEach((dd) => {
      if (dd.contains(e.target)) return;
      const menu = dd.querySelector(".dd-menu");
      const caret = dd.querySelector(".dd-caret");
      if (menu) menu.classList.add("hidden");
      if (caret) caret.style.transform = "";
    });
  });

  function buildFromGeojson(geo, fit) {
    // Resolve the wording before anything renders — the attribution is baked
    // into the GeoJSON source, so it has to be known before we add it.
    if (geo && DATASETS[geo.kind]) dataset = DATASETS[geo.kind];

    const feats = (geo && geo.features) || [];
    const grouped = {};
    for (const f of feats) {
      const r = (f.properties && f.properties.res != null) ? f.properties.res : 0;
      (grouped[r] = grouped[r] || []).push(f);
    }
    resolutions = (Array.isArray(geo.resolutions) && geo.resolutions.length
      ? geo.resolutions.map(Number)
      : Object.keys(grouped).map(Number)).sort((a, b) => a - b);
    for (const r of resolutions) {
      byRes[String(r)] = {type: "FeatureCollection", features: grouped[r] || []};
    }

    if (!resolutions.some((r) => byRes[String(r)].features.length)) {
      panelContent.hidden = true;
      panelEmpty.hidden = false;
      document.getElementById("panel-empty").innerHTML =
        `<h2 class="text-slate-800 font-semibold text-[15px] mb-1">No ${dataset.unit.toLowerCase()} data</h2>` +
        `<p class="text-sm leading-relaxed max-w-[240px]">This dataset's file loaded but has no features yet.</p>`;
      return;
    }

    const emptyH = document.querySelector("#panel-empty h2");
    const emptyP = document.querySelector("#panel-empty p");
    if (emptyH) emptyH.textContent = dataset.emptyTitle;
    if (emptyP) emptyP.textContent = dataset.emptyBody;

    addCellLayers();
    // Bind hover/click once, now that the fill layer first exists; the layer id is
    // reused on every dataset switch so the delegated handlers keep working.
    if (!interactionsBound) { bindCellInteractions(); interactionsBound = true; }
    applyLayerOrder();   // restore rivers/flash/basins/districts stacking after rebuild

    showRes(resolutions[0]);
    if (fit) {
      const extent = boundsOf(fcFor(resolutions[0]).features);
      if (!extent.isEmpty()) map.fitBounds(extent, {padding: 40, maxZoom: 5, duration: 0});
    }
    showRes(zoomToRes(map.getZoom()));
  }

  function panelError(err) {
    panelContent.hidden = true;
    panelEmpty.hidden = false;
    document.getElementById("panel-empty").innerHTML =
      "<h2 class=\"text-slate-800 font-semibold text-[15px] mb-1\">Couldn't load forecast data</h2>" +
      "<p class=\"text-sm leading-relaxed max-w-[240px]\">" +
      String(err && err.message ? err.message : err) +
      "</p>";
  }

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
      paint: {"fill-color": "#a78bfa", "fill-opacity": ["case", stateOn("selected"), 0.55, 0.32]},
    });
    map.addLayer({
      id: "flash-line-likely", type: "line", source: FLASH_SRC,
      filter: ["==", ["get", "polygon_type"], "likely"],
      paint: {
        "line-color": ["case", stateOn("selected"), "#ffffff", "#7c3aed"],
        "line-width": ["case", stateOn("selected"), 3, 1.1],
        "line-opacity": 0.9, "line-dasharray": [2, 1.5],
      },
    });
    map.addLayer({
      id: "flash-fill-high", type: "fill", source: FLASH_SRC,
      filter: ["==", ["get", "polygon_type"], "highly_likely"],
      paint: {"fill-color": "#6d28d9", "fill-opacity": ["case", stateOn("selected"), 0.72, 0.5]},
    });
    map.addLayer({
      id: "flash-line-high", type: "line", source: FLASH_SRC,
      filter: ["==", ["get", "polygon_type"], "highly_likely"],
      paint: {
        "line-color": ["case", stateOn("selected"), "#ffffff", "#4c1d95"],
        "line-width": ["case", stateOn("selected"), 3.5, 1.8],
        "line-opacity": 0.95,
      },
    });
    bindFlashInteractions();
  }

  // Hover a flash-flood polygon to see its legend attribute ("Likely" /
  // "Highly likely"), colored to match the on-map fill. Reuses the shared cell
  // tooltip; the cell handler yields to flash where the two overlap.
  function flashTooltipHtml(p) {
    const L = FLASH_LEGEND_BY_TYPE[p.polygon_type] ||
      {label: p.polygon_type || "Flash flood", fill: "#7c3aed"};
    return `<span style="display:inline-flex;align-items:center;gap:6px">` +
      `<span style="display:inline-block;width:10px;height:10px;border-radius:2px;background:${L.fill}"></span>` +
      `Flash flood · <b>${L.label}</b></span>`;
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
        rerenderPanel();
        scrollPanelToSection("section-flash");      // reveal the flash tile just filled
      });
    });
  }

  // Carry the `selected` feature-state on the clicked flash polygon (white
  // outline), clearing it from the previously selected one. Pass null to clear.
  function setFlashSelected(polygonId) {
    if (!map.getSource(FLASH_SRC)) { selectedFlashId = polygonId; return; }
    if (selectedFlashId != null)
      map.setFeatureState({source: FLASH_SRC, id: selectedFlashId}, {selected: false});
    selectedFlashId = polygonId;
    if (polygonId != null)
      map.setFeatureState({source: FLASH_SRC, id: polygonId}, {selected: true});
  }

  // True when a visible flash polygon sits under the cursor (flash wins the tooltip).
  function flashUnderCursor(point) {
    if (!flashOn || !map.getLayer(FLASH_FILL_LAYERS[0])) return false;
    return map.queryRenderedFeatures(point, {layers: FLASH_FILL_LAYERS}).length > 0;
  }

  function setFlashOn(on) {
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
      applyLayerOrder();   // keep flash below districts and above basins
    });
  }

  // Desired stacking, top -> bottom: rivers, districts, flash (highly_likely over
  // likely), basins. Re-applied whenever any of these layers (re)appears, since
  // they're added at different times (dataset build, flash/context toggles).
  const LAYER_ORDER = [
    "ctx-streams-line", "ctx-streams-casing",
    "ctx-districts-line", "ctx-districts-casing", "ctx-districts-fill",
    "flash-line-high", "flash-fill-high", "flash-line-likely", "flash-fill-likely",
    LINE, HASH, FILL,
  ];
  function applyLayerOrder() {
    // Move bottom-most first so each moveLayer(id)-to-top leaves LAYER_ORDER[0] on top.
    for (let i = LAYER_ORDER.length - 1; i >= 0; i--) {
      if (map.getLayer(LAYER_ORDER[i])) map.moveLayer(LAYER_ORDER[i]);
    }
  }

  // ---- Dataset switching (basins / h3 / s2) ---------------------------------

  function teardown() {
    clearSelection();
    setHover(null);
    tooltip.remove();
    removeContext();
    for (const id of [LINE, HASH, FILL]) if (map.getLayer(id)) map.removeLayer(id);
    if (map.getSource(SRC)) map.removeSource(SRC);
    for (const k in fcByRes) delete fcByRes[k];
    for (const k in byRes) delete byRes[k];
    featureById.clear();
    resolutions = [];
    currentRes = null;
  }

  function loadDataset(key, fit) {
    const ds = DATASETS_MENU.find((d) => d.key === key);
    if (!ds) return;
    currentDatasetKey = key;
    highlightDataset();
    updateContextControlsVisibility();   // context is basins-only
    teardown();
    fetch(ds.url)
      .then((r) => { if (!r.ok) throw new Error("HTTP " + r.status + " for " + ds.url); return r.json(); })
      .then((geo) => buildFromGeojson(geo, fit))
      .catch(panelError);
  }

  function onZoomEnd() {
    showRes(zoomToRes(map.getZoom()));
    updateStreamsLOD();                  // stream LOD depends on zoom
  }

  // ---- Basin context: streams + districts (MapLibre sources/layers) ---------

  const ctxSrc = (which) => "ctx-" + which + "-src";
  const ctxLayers = (which) => which === "streams"
    ? ["ctx-streams-casing", "ctx-streams-line"]
    : ["ctx-districts-fill", "ctx-districts-casing", "ctx-districts-line"];

  function loadCtx(which, cb) {
    const c = ctx[which];
    if (c.data) return cb(c.data);
    if (c.loading) return;
    c.loading = true;
    fetch(c.url)
      .then((r) => { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); })
      .then((data) => { c.data = data; c.loading = false; cb(data); })
      .catch((err) => { c.loading = false; console.warn(which + ":", err.message); cb(null); });
  }

  // Add the source + layers for a context type once its data is available. Streams
  // get a dark casing + a bright cyan line (in-basin solid-ish, downstream lighter);
  // districts get a dark casing + a dashed magenta outline.
  function ensureCtxLayers(which) {
    if (currentDatasetKey !== "basins" || map.getSource(ctxSrc(which)) || !ctx[which].data) return;
    map.addSource(ctxSrc(which), {type: "geojson", data: ctx[which].data});
    if (which === "streams") {
      map.addLayer({
        id: "ctx-streams-casing", type: "line", source: ctxSrc("streams"),
        layout: {"line-cap": "round"},
        paint: {"line-color": "#0b1220", "line-width": 5, "line-opacity": 0.85},
      });
      map.addLayer({
        id: "ctx-streams-line", type: "line", source: ctxSrc("streams"),
        layout: {"line-cap": "round"},
        // Defaults; updateCtx sets per-basin colour/width from in_basins so the
        // in-basin channel is emphasised over the downstream trace.
        paint: {"line-color": "#22d3ee", "line-width": 2.8, "line-opacity": 0.95},
      });
    } else {
      // A near-invisible fill under the outlines gives the whole district area a
      // hit target for the hover label (the outlines alone are too thin to hover).
      map.addLayer({
        id: "ctx-districts-fill", type: "fill", source: ctxSrc("districts"),
        paint: {"fill-color": "#e879f9", "fill-opacity": 0.001},
      });
      map.addLayer({
        id: "ctx-districts-casing", type: "line", source: ctxSrc("districts"),
        layout: {"line-cap": "round"},
        paint: {"line-color": "#0b1220", "line-width": 5, "line-opacity": 0.8},
      });
      map.addLayer({
        id: "ctx-districts-line", type: "line", source: ctxSrc("districts"),
        paint: {"line-color": "#e879f9", "line-width": 2.5, "line-opacity": 1, "line-dasharray": [2, 1.5]},
      });
      bindDistrictHover();
    }
  }

  // Hover a district to see its name (name only). Reuses the shared tooltip.
  // Districts sit behind basins and flash, so the label only wins where neither
  // a basin nor a flash polygon is on top; it still pops where a district is the
  // topmost thing under the cursor.
  let districtHoverBound = false;
  function bindDistrictHover() {
    if (districtHoverBound) return;
    districtHoverBound = true;
    map.on("mousemove", "ctx-districts-fill", (e) => {
      const f = e.features && e.features[0];
      if (!f || flashUnderCursor(e.point) || basinUnderCursor(e.point)) return;
      map.getCanvas().style.cursor = "pointer";
      tooltip.setLngLat(e.lngLat).setHTML(`<b>${f.properties.name || "District"}</b>`).addTo(map);
    });
    map.on("mouseleave", "ctx-districts-fill", () => {
      map.getCanvas().style.cursor = "";
      tooltip.remove();
    });
  }

  function basinUnderCursor(point) {
    return map.getLayer(FILL) &&
      map.queryRenderedFeatures(point, {layers: [FILL]}).length > 0;
  }

  // Show a context type's layers filtered to the selected basin (+ stream LOD),
  // or hide them when it's off / not a basin view / nothing selected.
  function updateCtx(which) {
    const active = ctx[which].on && currentDatasetKey === "basins" && selectedBasinId;
    if (!active) {
      ctxLayers(which).forEach((id) => {
        if (map.getLayer(id)) map.setLayoutProperty(id, "visibility", "none");
      });
      return;
    }
    ensureCtxLayers(which);
    const base = ["in", selectedBasinId, ["get", "basins"]];
    let filter = base;
    if (which === "streams") {
      // Is this reach physically inside the selected basin? Prefer build_context's
      // in_basins (correct per basin); fall back to the global `reach` label for
      // older data that lacks it, so streams still work before the pipeline rerun.
      const inBasin = ["case",
        ["has", "in_basins"],
        ["in", selectedBasinId, ["coalesce", ["get", "in_basins"], ["literal", []]]],
        ["==", ["get", "reach"], "in_basin"]];
      // Always draw the downstream trace (keeps the main channel anchored through
      // the basin at any zoom); telescope only the in-basin reaches by zoom.
      filter = ["all", base, ["any", ["!", inBasin], [">=", ["get", "ord"], streamMinOrder(map.getZoom())]]];
      if (map.getLayer("ctx-streams-line")) {
        // Emphasize the in-basin channel (bright, thicker) over the paler trace.
        map.setPaintProperty("ctx-streams-line", "line-color", ["case", inBasin, "#22d3ee", "#7dd3fc"]);
        map.setPaintProperty("ctx-streams-line", "line-width", ["case", inBasin, 2.8, 2]);
      }
    }
    ctxLayers(which).forEach((id) => {
      if (map.getLayer(id)) {
        map.setFilter(id, filter);
        map.setLayoutProperty(id, "visibility", "visible");
      }
    });
    applyLayerOrder();   // rivers on top, districts above flash/basins
  }

  function setCtxOn(which, on) {
    ctx[which].on = on;
    if (on) loadCtx(which, (data) => { if (!data) { ctx[which].on = false; return; } updateCtx(which); });
    else updateCtx(which);
  }

  function refreshContext() {
    updateCtx("streams");
    updateCtx("districts");
  }

  function updateStreamsLOD() {
    if (ctx.streams.on) updateCtx("streams");
  }

  function removeContext() {
    ["streams", "districts"].forEach((which) => {
      ctxLayers(which).forEach((id) => { if (map.getLayer(id)) map.removeLayer(id); });
      if (map.getSource(ctxSrc(which))) map.removeSource(ctxSrc(which));
    });
  }

  // ---- Zoom extent: basin / river / district --------------------------------

  function zoomToSelection(feature) {
    const basinFit = () => map.fitBounds(boundsOf([feature]), {maxZoom: 11, padding: 40});
    if (zoomExtent === "river") return fitContext("streams", feature, basinFit);
    if (zoomExtent === "district") return fitContext("districts", feature, basinFit);
    basinFit();
  }

  function fitContext(which, feature, fallback) {
    loadCtx(which, (data) => {
      if (!data) return fallback();
      const id = String(feature.properties.cell_id);
      const feats = data.features.filter((f) => ((f.properties && f.properties.basins) || []).includes(id));
      if (!feats.length) return fallback();
      const b = boundsOf(feats);
      if (b.isEmpty()) return fallback();
      map.fitBounds(b, {maxZoom: 12, padding: 40});
    });
  }

  // ---- Controls: dataset toggle + basin-context panel -----------------------

  function highlightDataset() {
    if (!datasetControlEl) return;
    datasetControlEl.querySelectorAll(".ds-row").forEach((b) => {
      const on = b.dataset.ds === currentDatasetKey;
      b.style.background = on ? "#0284c7" : "transparent";
      b.style.color = on ? "#fff" : "#334155";
    });
  }

  // Data-layer picker: Basins / H3 / S2.
  function datasetControl() {
    return dropdownControl({
      iconName: "h3-hexagon",
      title: "Flagged Area Type",
      panelStyle: "padding:4px;min-width:118px;font:600 12px system-ui,sans-serif",
      render(panel) {
        panel.innerHTML = DATASETS_MENU.map((d) =>
          `<button type="button" data-ds="${d.key}" class="ds-row" style="display:block;width:100%;text-align:left;` +
          `border:0;background:transparent;padding:6px 9px;border-radius:5px;cursor:pointer;color:#334155;white-space:nowrap">${d.label}</button>`).join("");
        panel.querySelectorAll(".ds-row").forEach((b) => b.addEventListener("click", () => {
          panel.hidden = true;
          if (b.dataset.ds !== currentDatasetKey) loadDataset(b.dataset.ds, false);
        }));
      },
      onReady(container) { datasetControlEl = container; highlightDataset(); },
    });
  }

  function updateContextControlsVisibility() {
    if (contextControlEl) contextControlEl.style.display = currentDatasetKey === "basins" ? "" : "none";
  }

  // Selected-basin context: streams/districts overlays + zoom-to extent.
  function contextControl() {
    return dropdownControl({
      iconName: "layers",
      title: "Context Layers",
      panelStyle: "padding:6px 9px;font:600 12px system-ui,sans-serif;color:#0f172a;min-width:118px",
      render(panel) {
        const head = (t) => `<div style="font-size:10px;color:#64748b;text-transform:uppercase;` +
          `letter-spacing:.04em;margin-bottom:4px">${t}</div>`;
        panel.innerHTML =
          head("Selected basin") +
          '<label style="display:flex;align-items:center;gap:6px;cursor:pointer;margin-bottom:2px">' +
          '<input type="checkbox" id="ctx-streams" style="accent-color:#22d3ee">Streams</label>' +
          '<label style="display:flex;align-items:center;gap:6px;cursor:pointer">' +
          '<input type="checkbox" id="ctx-districts" style="accent-color:#e879f9">Districts</label>';
        panel.querySelector("#ctx-streams").addEventListener("change", (e) => setCtxOn("streams", e.target.checked));
        panel.querySelector("#ctx-districts").addEventListener("change", (e) => setCtxOn("districts", e.target.checked));
      },
      onReady(container) { contextControlEl = container; updateContextControlsVisibility(); },
    });
  }

  // Zoom-to extent picker (top-left, by the zoom in/out buttons): frame the
  // selected basin, its stream network, or its districts. Value "river" fits the
  // streams extent; it's labelled "Stream" to match the Streams context layer.
  function zoomToControl() {
    const OPTS = [["basin", "Basin"], ["river", "Stream"], ["district", "District"]];
    return dropdownControl({
      iconName: "magnifying-glass",
      title: "Zoom To",
      side: "left",
      panelStyle: "padding:6px 9px;font:600 12px system-ui,sans-serif;color:#0f172a;min-width:110px",
      render(panel) {
        panel.innerHTML =
          '<div style="font-size:10px;color:#64748b;text-transform:uppercase;letter-spacing:.04em;margin-bottom:4px">Zoom to</div>' +
          OPTS.map(([v, label], i) =>
            `<label style="display:flex;align-items:center;gap:6px;cursor:pointer${i < OPTS.length - 1 ? ";margin-bottom:2px" : ""}">` +
            `<input type="radio" name="zoomext" value="${v}"${v === "basin" ? " checked" : ""} ` +
            `style="accent-color:#0284c7">${label}</label>`).join("");
        panel.querySelectorAll('input[name="zoomext"]').forEach((rb) => rb.addEventListener("change", () => {
          if (!rb.checked) return;
          zoomExtent = rb.value;
          const f = featureById.get(selectedId);
          if (f) zoomToSelection(f);
        }));
      },
    });
  }

  // ---- Boot -----------------------------------------------------------------

  map.once("load", () => {
    map.on("zoomend", onZoomEnd);
    // Zoom-to dropdown joins the zoom in/out buttons in the top-left.
    map.addControl(zoomToControl(), "top-left");
    // Three stacked dropdowns in the top-right column.
    map.addControl(basemapControl(), "top-right");
    map.addControl(datasetControl(), "top-right");
    map.addControl(contextControl(), "top-right");
    loadDataset("basins", true);           // interactions bind on first dataset build
    // Flash models default to on, so draw their polygons at startup.
    if (visibleFlashModels.has("flood_hub")) setFlashOn(true);
  });
})();
