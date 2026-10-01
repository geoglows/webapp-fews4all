"""Shared constants and paths for the FEWS4All backend.

The backend runs in two tiers, and the folder layout says which is which:

    backend/static/   built once, rerun only when the reference data changes
                      (H3 and basin geometry -> PMTiles, the cell impact table,
                      the id -> Near table)
    backend/daily/    run per forecast release: intersect -> compare -> summary
                      csv -> map rules

The daily tier joins the static tables rather than recomputing them, which is
what keeps a run short: the HUC12 impact apportionment and the ADM cascade are
the slowest things in the whole pipeline and neither answer changes between
releases.

Everything user-facing about severity lives here, so the ladder is stated once
and every step (and eventually the front end's rules file) reads the same values.
"""

import os

HERE = os.path.dirname(os.path.abspath(__file__))     # backend/common
BACKEND = os.path.dirname(HERE)                       # backend
ROOT = os.path.dirname(BACKEND)                       # <repo>

# Where the operator drops a release's native model files. Overridable so a run
# can be pointed at an archived release without moving anything.
INPUT_DIR = os.environ.get("FEWS_INPUT") or os.path.join(BACKEND, "input")
OUTPUT_DIR = os.environ.get("FEWS_OUTPUT") or os.path.join(BACKEND, "output")

FILES = os.path.join(ROOT, "Files")                   # static reference data
# h3_id (res 6) -> river_id, report_id. The reach crosswalk: every cell a
# GEOGLOWS reach passes through, which is how a line becomes a set of cells.
CROSSWALK_CSV = os.path.join(FILES, "h3_streams_global_r6.csv")
CROSSWALK_ID_COL = "report_id"    # the reach a forecast is issued against

BASE_RES = 6                      # the resolution everything is decided at
RESOLUTIONS = [3, 4, 5, 6]        # ...then rolled up to, in step 3

# Input files are discovered by shape, not by exact name, because the names
# carry the release date and change every run.
INPUT_GLOBS = {
    "geoglows": ["mapstyletable*.csv"],
    "flood_hub": ["world_flood_status*.csv", "Flood_Hub_Global*.csv"],
    "glofas": ["RPG_U*.shp"],
    "flash": ["urban_flash_floods*.geojson"],
}

# One ladder for every model. The rank orders them; the three GEOGLOWS return
# periods, the three Flood Hub labels and the three GloFAS threshold groups all
# land on the same three rungs, so "danger" means the same thing on every model.
SEVERITY_RANK = {"warning": 1, "danger": 2, "extreme": 3}

# GEOGLOWS publishes a return period, not a label; below the 2-year it is not
# flagged. GloFAS's three thresholds are the same 2/5/20-year ladder, which is
# why its ThresGroup maps straight across.
GEOGLOWS_THRESHOLDS = [(20, "extreme"), (5, "danger"), (2, "warning")]
GEOGLOWS_MIN_MEAN_FLOW = 5        # drop trickles that clear a return period on noise
FLOOD_HUB_SEVERITY = {"ABOVE_NORMAL": "warning", "SEVERE": "danger", "EXTREME": "extreme"}
GLOFAS_SEVERITY = {1: "warning", 2: "danger", 3: "extreme"}
GLOFAS_RETURN_PERIOD = {1: 2, 2: 5, 3: 20}
# The name columns are literal placeholders on the ~92% of GloFAS points that are
# dynamic grid cells rather than gauged stations.
GLOFAS_PLACEHOLDERS = {"not a station", "not found", "na", "n/a", "none", "-", "--"}

# Flash floods publish two confidence tiers rather than a hazard magnitude, so they
# sit on the outer rungs of the shared ladder: "likely" reads as a warning, "highly
# likely" as the top rung. This is what lets a flash cell concur with a river model
# in step 3 instead of living in a parallel world of its own.
FLASH_SEVERITY = {"likely": "warning", "highly_likely": "extreme"}

# The columns of the two tables step 2 writes. Stated here because step 3 reads
# them and a contract that lives in one file cannot drift between two.
FORECAST_COLUMNS = [
    "forecast_uid", "model", "severity", "native_id",
    "return_period_yr", "peak_discharge_cms",
    "issued_time", "start_time", "peak_time", "end_time",
    "lat", "lon",
    # Identity a model happens to publish about the place it forecasts for.
    # Blank for models that do not; never invented.
    "country", "region", "basin", "sub_basin", "station", "river", "lead_time_days",
]
# One table for which forecasts are in which cell, at every resolution. Step 2
# writes it with the base resolution only; step 3 expands it in place. Same name
# and same columns throughout, so there is never a second copy of it on disk.
MATCH_COLUMNS = ["res", "h3_id", "forecast_uid"]

# A stable order for every list of models the backend writes, so a cell's `models`
# column reads the same way whatever order the readers happened to run in, and a
# diff between two releases shows real change rather than reshuffling.
MODEL_ORDER = ["geoglows", "flood_hub", "glofas", "flash"]

# Step 3's outputs. `co_models` holds the model sets that genuinely share one
# res-6 cell somewhere inside this feature — pipe-separated sets, plus-joined
# members ("geoglows+glofas|flood_hub+glofas"). Empty means NOT agreeing, which is
# a stronger statement than it looks: a coarse cell holds every forecast beneath
# it, so counting its own models would report agreement between models that never
# meet. That distinction is the whole reason this column exists.
CELL_COLUMNS = ["h3_id", "res", "severity", "model_count", "models",
                "co_models", "forecast_count"]

# Step 4's enrichments. Impact is joined onto cells; Near onto forecasts. Both
# are written back over the same files rather than into a second delivered copy:
# every added column is new, and each step reads only columns it wrote or found,
# so re-running one is safe and leaves one set of files per release.
IMPACT_FIELDS = ["population", "buildings", "farmland_m2", "highway_km", "railway_km"]
CELL_DELIVERY_COLUMNS = CELL_COLUMNS + IMPACT_FIELDS
FORECAST_DELIVERY_COLUMNS = FORECAST_COLUMNS + ["district", "district_level"]

CELL_IMPACT_TABLE = os.path.join(BACKEND, "static", "cell_impact_r6.csv")

# geoBoundaries CGAZ, tried finest first. ADM2 does not tile every country —
# Uruguay's units cover 37% of its area, Norway's 70% — so an ADM2-only lookup
# leaves points in those gaps with no location at all. Each level is asked only
# about the points the finer one could not place.
BOUNDARIES_DIR = os.path.join(FILES, "International_boundaries")
ADMIN_LEVELS = [(2, "geoBoundariesCGAZ_ADM2.gpkg"),
                (1, "geoBoundariesCGAZ_ADM1.gpkg"),
                (0, "geoBoundariesCGAZ_ADM0.gpkg")]
ADMIN_READ_BATCH = 4000    # polygons per streamed read, to keep memory flat

# ---- Styling (step 5) ------------------------------------------------------
# Colour is owned here rather than in the front end, so a release carries the
# palette it was drawn with. The front end still switches between palettes live —
# they are paint-only changes — but it chooses among the ones defined here rather
# than inventing its own.
PALETTES = [
    # Matplotlib colormaps, sampled at 0.40 / 0.50 / 0.60 — a narrow slice through
    # the middle of each map, so the three rungs share a hue family and differ
    # mainly in tone. Lightness still rises with severity.
    #
    # Worth knowing what this costs: across the nine maps the closest pair of rungs
    # is 10 ΔE apart (cividis) against 39 at the wider sampling this replaced. Below
    # about 20 ΔE two colours are hard to separate at a glance, and a res-6 hexagon
    # at world zoom is a small target. The sampling is a constant here, so widening
    # it later is one edit and a rerun of step 5.
    {"id": "viridis", "label": "Viridis",
     "warning": "#2a788e", "danger": "#21918c", "extreme": "#22a884"},
    {"id": "plasma", "label": "Plasma",
     "warning": "#b12a90", "danger": "#cc4778", "extreme": "#e16462"},
    {"id": "inferno", "label": "Inferno",
     "warning": "#932667", "danger": "#bc3754", "extreme": "#dd513a"},
    {"id": "magma", "label": "Magma",
     "warning": "#8c2981", "danger": "#b73779", "extreme": "#de4968"},
    {"id": "cividis", "label": "Cividis",
     "warning": "#666970", "danger": "#7d7c78", "extreme": "#948e77"},
    {"id": "winter", "label": "Winter",
     "warning": "#0066cc", "danger": "#0080bf", "extreme": "#0099b2"},
    {"id": "autumn", "label": "Autumn",
     "warning": "#ff6600", "danger": "#ff8000", "extreme": "#ff9900"},
    {"id": "spring", "label": "Spring",
     "warning": "#ff6699", "danger": "#ff807f", "extreme": "#ff9966"},
    {"id": "gist_heat", "label": "Heat",
     "warning": "#990000", "danger": "#c00100", "extreme": "#e53300"},
]
DEFAULT_PALETTE = "inferno"
# Every model opens on the same palette. Models are told apart by the split-cell
# bands and the panel, not by colour — and a cell whose models share a palette is
# no longer split at all, it simply takes the worst severity's colour.
MODEL_PALETTE = {
    "geoglows": "inferno",
    "flood_hub": "inferno",
    "glofas": "inferno",
    "flash": "inferno",
}
FLASH_PALETTE = "inferno"
MODEL_LABELS = {"geoglows": "GEOGLOWS", "flood_hub": "Flood Hub",
                "glofas": "GloFAS", "flash": "Flash Floods"}

# Which resolution is drawn at which zoom. MapLibre measures zoom against a 512px
# world where Leaflet used 256px, so every number here sits one step below the
# Leaflet equivalent for the same view on screen.
# Stated per resolution rather than derived from a start and a step, because the
# coarsest one is not on the same ladder: the map's own minimum zoom is 1 and it
# opens at 1.3, so res 3 has to be drawing from 0 or the world view is blank. The
# rest step by 2 as before.
#
# This is read by BOTH step 5 (the layer bands) and the static tile build (the
# archive bands). They have to agree — an archive that starts at z2 cannot be
# drawn at z1 however the layer is configured, because MapLibre does not request
# tiles below a source's minzoom.
RES_ZOOM = {3: 0, 4: 4, 5: 6, 6: 8}
RES_START_ZOOM = RES_ZOOM[3]      # kept for anything still reading the old names
RES_ZOOM_STEP = 2

# The cell geometry archives the rules point at — one per resolution, because
# each resolution owns its own zoom band (see static/build_cell_tiles.py). `{res}`
# is substituted per resolution. Repoint this when the archives move; nothing else
# in the backend refers to them.
# Dev path: Vite serves the project root, so the archives resolve where the static
# build wrote them. PMTiles is read with HTTP range requests, which the dev server
# honours. For production, serve backend/static/tiles/ from somewhere real and
# repoint this — it is the only place the archives are named.
CELLS_TILES_URL = "pmtiles:///backend/static/tiles/cells.pmtiles"
# One archive holds every resolution, each as its OWN named layer. The names have
# to differ: the archives overlap at z4, z6 and z8, and layers sharing a name would
# be merged into one by the join, putting two resolutions of hexagon in the same
# tile with no way to tell them apart.
CELLS_SOURCE_LAYER = "cells_r{res}"
CELLS_PROMOTE_ID = "h3_id"        # feature-state (hover/selection) needs a stable id
