# FEWS4All

A web map that flags flood-forecast locations (H3 grid cells or HydroBASINS basins)
and shows each model's forecast on click. Data comes from GEOGLOWS and Google Flood Hub.

## Stack

Built with **Vite** — it bundles the app, serves it with hot reload during development, and produces the static `dist/` build. Dependencies come from npm (Leaflet, Iconify, Tailwind CSS v4 via
`@tailwindcss/vite`), so there are no CDN
`<script>` tags. Vite also replaces VS Code Go Live — `npm run dev` is the dev server.

## Run the app

```
npm install
npm run dev        # dev server at http://localhost:5173
npm run build      # static build -> dist/
npm run preview    # preview the built site
```

## Layout

- `index.html` — page shell, loads `/src/main.js`
- `src/` — the app, one module per concern (below)
- `public/` — the GeoJSON the app fetches, plus static assets
- `scripts/` — the Python pipeline that builds that GeoJSON
- `test/` — `npm test` (node --test); pure modules only
- `Other/parked/` — code removed from the app but kept, each file noting how to restore it
- `Files/` — input CSVs, basin parquets, impact stats (gitignored)

### src/

`src/layers/` mirrors the pipeline: one module per file the pipeline emits, so
`build_flash.py` → `data_flash_floods.geojson` → `layers/flash.js` reads straight
across in either direction.

```
main.js        wiring + boot, and nothing else
config.js      constants: severity, ramps, wording, every source/layer id
settings.js    live view state (`view`) + the colours derived from it
format.js      pure formatting, labels, deep links, severity ranking
geometry.js    bounds + the clipping that splits a cell between models
basemaps.js    base-map definitions and thumbnails
map.js         the MapLibre map, shared tooltip, layer stacking, readout
panel.js       the side panel — renders only
sources.js     where every GeoJSON lives
icons.js       SVG icons inlined at build time
ui/dropdown.js the icon-button dropdown every control is built on
layers/        cells.js (H3 + basins), flash.js, streams.js, boundaries.js
controls/      display.js, context.js, basemap.js
```

Dependencies run one way: `controls/` → `layers/` → `map.js` → the pure modules.
Where that would loop — the panel's filters act on the layers, and the layers
render into the panel — the callee takes an `init({...})` of callbacks that
`main.js` supplies, so nothing imports its own caller. `main.js` is the only place
those connections exist, and the only place to look for them.

## Display Options

The top-right **Display Options** control (below the base-map picker) holds everything
about how the map is drawn, in two tabs so the panel stays short and most of the map
stays visible.

**Cells & Basins**

- **Flagged area** — H3 cells or basins.
- **Appearance** — a colour ramp per model (three shades, light warning to dark
  extreme), fill opacity, and an outline-only mode for dense areas or imagery. A cell
  holding forecasts from two models is drawn as one vertical slice per model, each in
  its own ramp; the outline and hash stay undivided so the split never reads as a
  border. All ramps default to Amber–Red, the app's historic palette.
- **Highlight** — the diagonal hash marking cells where the models concur.
- **Severity shown** — Warning / Danger / Extreme, independent of the panel's model filter.

**Flash Floods**

- **Appearance** — the overlay's ramp (its two tiers take the light and dark ends),
  fill opacity and outline-only. At the default opacity the tiers reproduce exactly
  the fixed values the overlay shipped with.
- **Tiers shown** — Likely / Highly likely.

Switching the overlay on and off stays in the side panel's Flash Floods filter:
Display Options governs how things look, the panel's filters govern what is shown.

Each model tile in the side panel carries its own legend, directly under the model
name and present whether or not the tile holds a forecast — a model's key shows the
colours that model paints, which is what a ramp per model is for. The tiles list only
the severities (or flash tiers) actually being drawn. "Models concur" appears in both
model tiles — it describes the pair, so a reader looking at one tile shouldn't have to
find the other to learn what the hash means — and disappears when the hatch highlight
is switched off, or when only one model is on.

## Context Layers

The **Context Layers** control (the layers icon, below Display Options) holds overlays
that sit alongside the hazard rather than describing it:

- **Global → TDX-Hydro Streams** — the network GEOGLOWS forecasts on, telescoping by
  zoom: order 7 and up at world view, order 6 from zoom 3, everything from zoom 5. Drawn beneath
  the flagged cells so it reads as a backdrop. Colour, width ramp and opacity are taken
  from River Forecast System v3's own `streams.js` (`STREAM_*` in `config.js`): width
  runs 4px to 10px across Strahler order 2-10, scaled x0.25/x0.5/x1/x2.2 at zoom
  3/7/12/16, so an order-6 reach is drawn at the same weight in both apps. The opening
  view (`center`/`zoom` in `map.js`) and `ORDER_BY_ZOOM` in `layers/streams.js` were
  matched by measuring pixel coverage against screenshots of that app.

  RFS v3 serves this network as PMTiles vector tiles, which is how it can carry order 2
  and up at every zoom. Ours is one GeoJSON, so `MIN_ORDER` is the ceiling on detail.
- **Global → ADM Boundaries** — geoBoundaries CGAZ units, one level per zoom band:
  countries below zoom 4, regions from 4, districts from 7. Hovering a unit
  names it. Only one level is drawn at a time, which costs nothing visually — ADM1
  tiles its country and ADM2 tiles its ADM1, so the national border is still on screen
  as the outer edge of whichever level is active.
Context is global: both layers stand on their own, independent of which cell or basin
is selected. The per-basin Streams / Districts overlays and the Zoom To control they
shared have been retired — `Other/parked/context.js`, `Other/parked/zoom-to.js` and
`Other/build_context.py`, each with a restore note at the top.

### ADM boundaries pipeline

```
cd scripts
python build_boundaries.py    # ADM0/1/2 gpkg -> public/data_boundaries.geojson
```

Each level is simplified to the resolution of the zooms it is shown at (tolerance
0.01 / 0.005 / 0.002 degrees, set in `LEVELS`), which is what makes one file
affordable: full-resolution CGAZ is ~1.3 GB across the three, nearly all of it
coastline vertices invisible at the zoom where that level appears. Output is 115 MB
raw / ~36 MB compressed — ADM0 6.5 MB, ADM1 20 MB, ADM2 88 MB. 33 sub-hectare slivers
are dropped by `MIN_AREA`.

### TDX-Hydro streams pipeline

```
cd scripts
python build_streams.py    # TDX-Hydro gpkg + model table -> public/data_streams.geojson
```

The geometry file carries nothing but `LINKNO`, so `strmOrder` is joined from
`Other/matching/files/v2-model-table.parquet` (same 6.84M keys, 1:1). Only order 5 and
up is written — 842,581 reaches, 192 MB raw and ~39 MB over the wire compressed. The
whole network would be ~1.4 GB, which is what `MIN_ORDER` at the top of the script
guards against.
Coordinates are rounded to 5 decimal places (~1 m).

### Locating a forecast

Both cell and basin builds tag every forecast with the place it sits in, by
point-in-polygon against geoBoundaries, finest first: the ADM2 district where there
is one, else the ADM1 region, else the ADM0 country. The cascade exists because CGAZ
ADM2 does not tile every country — Uruguay'"'"'s units cover 37% of its area, Norway'"'"'s 70%
— so an ADM2-only lookup left forecasts in those gaps with no location at all. The
level that answered is carried as `districtLevel`, and the panel qualifies anything
coarser than a district ("Tacuarembó (region)") rather than passing a country off as
one.

## Data

The map fetches its GeoJSON from CloudFront:

```
https://d3hbj0z0f67zhd.cloudfront.net/fews4all/data_basins.geojson
```

Regenerate it with the pipeline below, then upload the result.

### Grid (H3) pipeline — the default view

```
cd scripts
python build_cells_h3.py        # + h3_r6_global_matches.csv -> public/data_h3cells.geojson
```

H3 is what the map opens on (`DEFAULT_DATASET` in `src/main.js`); the basin build
stays available from the Flagged Area Type control.

GEOGLOWS reaches join res-6 cells through `h3_streams_global_r6.csv`
(`h3_id, river_id, report_id` — one row per cell a stream segment traverses, from
`Other/matching/h3_match/assign_streams_to_h3.py`), so a flooding river lights up its
whole path rather than the one hexagon holding its centroid. The join runs on
`report_id`, the reporting reach a forecast is issued against (`MATCH_ID_COL` at the
top of the script switches it back to `river_id`); repeat crossings of one reach
through one cell are collapsed, so a cell lists a reach once. Flood Hub gauges have
no crosswalk entry and are binned by their own coordinate. Res 6 rolls up to 5/4/3
through the H3 parent hierarchy. Each forecast gets its ADM2 district, and each cell
gets HUC12 impact statistics split by overlapping area (a hexagon ignores basin
boundaries, so whole-HUC12 sums would double-count).

### Basin (HydroBASINS) pipeline

```
cd scripts
python build_basins.py          # + HUC08.parquet -> public/data_basins.geojson
```

GEOGLOWS rivers join to level-8 basins via `global_matches.csv`, Flood Hub gauges by
point-in-polygon. This and the H3 build write the same FeatureCollection shape, so the
app reads either one from the Flagged area control.

## Severity

Four tiers — none / warning / danger / extreme. Flood Hub uses its own labels; GEOGLOWS is derived from return period (>=20yr extreme, >=5yr danger, >=2yr warning). Thresholds and the mean-flow floor
are set at the top of each build script (`build_cells_h3.py`, `build_basins.py`).

Colour is *not* fixed to severity: severity picks the shade, and the model's ramp
(chosen in the Display menu) picks the hue.
