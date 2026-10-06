// Central source hub for FEWS4All data files.
//
// Every GeoJSON the map loads is named here, in one place, so there is a single
// spot to repoint the app if the data ever moves. The files currently live in
// the `public/` folder, which Vite serves from the site root; using
// import.meta.env.BASE_URL resolves them correctly in dev and in production,
// even if the site is later served from a subpath (e.g. /fews4all/).
//
// To point the app at a different origin - for example back to a cloud CDN -
// change BASE to that URL (keep the trailing slash) and every entry below
// follows automatically. To move a single file, edit just its line.

const BASE = import.meta.env.BASE_URL; // public/ -> served from the site root

// One forecast release's delivered tables. The backend writes these per run; the
// date is a constant until the date picker exists, and the path is the dev-server
// one — Vite serves the project root, so backend/output resolves while `npm run
// dev` is running. Production needs these served from somewhere real.
export const RELEASE = "2026-09-23";
export const RELEASE_BASE = `/backend/output/${RELEASE}/`;

export const DATA = {
  basins: BASE + "data_basins.geojson",
  h3: BASE + "data_h3cells.geojson",
  globalStreams: BASE + "data_streams.geojson",
  // boundaries now come from backend/static/tiles/adm.pmtiles (see config.js
  // ADM_TILES_URL); public/data_boundaries.geojson is no longer read.
  flash: BASE + "data_flash_floods.geojson",
};
