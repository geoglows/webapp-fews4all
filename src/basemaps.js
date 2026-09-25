// Base-map definitions and their gallery thumbnails.

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

export const BASEMAPS = [
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
export const DEFAULT_BASEMAP = "esri-light";

// A basemap is one raster layer, except Imagery + labels, which pairs the imagery
// with a labels overlay drawn directly above it; both share one toggle.
export const basemapLayerIds = (b) => (b.overlay ? [b.id, `${b.id}-overlay`] : [b.id]);
