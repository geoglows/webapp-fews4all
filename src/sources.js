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

export const DATA = {
  basins: BASE + "data_basins.geojson",
  h3: BASE + "data_h3cells.geojson",
  globalStreams: BASE + "data_streams.geojson",
  boundaries: BASE + "data_boundaries.geojson",
  flash: BASE + "data_flash_floods.geojson",
};
