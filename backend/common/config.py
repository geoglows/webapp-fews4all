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
import re

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
    # The 2-year grid. The other two levels sit beside it under the same stamp,
    # and the reporting points (RPG_U*.shp) ride along as an attribute overlay.
    "glofas": ["sumAL_medium_AL*.nc"],
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
# GloFAS issues its forecast as three global 0.05-degree fields, one per return
# period, whose value is how many of the 51 ensemble members exceeded that
# threshold. A pixel is flagged at the highest level reaching 30% of the ensemble
# — 16 members — which is exactly what the reporting points encode as ThresGroup:
# sampling these grids at all 4,122 point coordinates reproduces ThresGroup with
# no exceptions, and the probabilities to within rounding.
GLOFAS_ENSEMBLE = 51
GLOFAS_ALERT_MEMBERS = 16
GLOFAS_GRID_VAR = "sumAL"
GLOFAS_GRID_LEVELS = {1: "medium", 2: "high", 3: "extreme"}
GLOFAS_POINTS_GLOB = "RPG_U*.shp"      # the overlay, not a second source
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
    "return_period_yr", "flow_cms",
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
# Color is owned here rather than in the front end, so a release carries the
# palette it was drawn with. The front end still switches between palettes live —
# they are paint-only changes — but it chooses among the ones defined here rather
# than inventing its own.
PALETTES = [
    # Two families, deliberately kept apart.
    #
    # The matplotlib perceptually uniform maps, sampled at 0.75 / 0.45 / 0.15 —
    # note the descent: these maps run dark to light as their value rises, so
    # reading them downwards is what puts the DARK end on `extreme`, matching the
    # ColorBrewer schemes below.
    #
    # The span was widened at the same time. At the old 0.40 / 0.50 / 0.60 the
    # closest pair of rungs was 2.7 dE apart on the map (cividis), which is at the
    # edge of being distinguishable at all on a res-6 hexagon at world zoom; it is
    # now 10.1 at worst and 29.6 at best. The ends stop short of the extremes on
    # purpose: below about 0.15 inferno and magma go near-black (L* 7) and lose
    # the hue that tells the ramps apart, and above about 0.75 the light end is
    # pale enough to start washing out against the basemap at low fill.
    {"id": "viridis", "label": "Viridis",
     "warning": "#5ec962", "danger": "#25848e", "extreme": "#463480"},
    {"id": "plasma", "label": "Plasma",
     "warning": "#f89540", "danger": "#bf3984", "extreme": "#5601a4"},
    {"id": "inferno", "label": "Inferno",
     "warning": "#f98e09", "danger": "#a82e5f", "extreme": "#2b0b57"},
    {"id": "magma", "label": "Magma",
     "warning": "#fc8961", "danger": "#a1307e", "extreme": "#251255"},
    {"id": "cividis", "label": "Cividis",
     "warning": "#bcae6c", "danger": "#727274", "extreme": "#243c6e"},

    # ColorBrewer sequential (colorbrewer2.org), sampled from the NINE-class
    # sequences at positions 2 / 4 / 6 rather than taken as the published 3-class
    # schemes.
    #
    # The 3-class schemes were the obvious thing to use and they were too pale at
    # the light end to see. Measured: composited at the default 30% fill over the
    # light basemap, their lightest swatch sat 2.1 to 4.0 dE from the bare paper,
    # which is at or below the threshold where a color registers at all — so a
    # `warning` cell was, in practice, an unpainted one.
    #
    # Shifting all three rungs one notch up the 9-class ladder puts that swatch at
    # 4.3 to 8.8 dE and leaves every neighbouring pair 7.0 to 11.8 dE apart, where
    # before the span was 5.4 to 12.7 — so the low end roughly doubled in presence
    # without any rung crowding its neighbour. All three got deeper, not just the
    # first: lifting `warning` alone squeezed the gap to `danger` down to 3.2 dE
    # on blues and purples, which is its own kind of unreadable.
    #
    # They still run dark-is-worst, which is ColorBrewer's own convention and the
    # readable one here — the palest rung belongs on `warning`, where being quiet
    # costs nothing, not on `extreme`.
    {"id": "blues", "label": "Blues",
     "warning": "#c6dbef", "danger": "#6baed6", "extreme": "#2171b5"},
    {"id": "greens", "label": "Greens",
     "warning": "#c7e9c0", "danger": "#74c476", "extreme": "#238b45"},
    {"id": "oranges", "label": "Oranges",
     "warning": "#fdd0a2", "danger": "#fd8d3c", "extreme": "#d94801"},
    {"id": "purples", "label": "Purples",
     "warning": "#dadaeb", "danger": "#9e9ac8", "extreme": "#6a51a3"},
    {"id": "reds", "label": "Reds",
     "warning": "#fcbba1", "danger": "#fb6a4a", "extreme": "#cb181d"},
]
DEFAULT_PALETTE = "oranges"
# Every model opens on the same palette. Models are told apart by the split-cell
# bands and the panel, not by color — and a cell whose models share a palette is
# no longer split at all, it simply takes the worst severity's color.
#
# Oranges, matching the heat map the app opens on, so switching between the two
# views changes what is drawn rather than what color everything is. Under the heat
# map the cells take the heat's ramp anyway (see rampFor in src/settings.js); this
# is what they fall back to with it off.
MODEL_PALETTE = {
    "geoglows": "oranges",
    "flood_hub": "oranges",
    "glofas": "oranges",
    "flash": "reds",
}
FLASH_PALETTE = "reds"
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
# ---- HydroRIVERS (Flood Hub's network) -----------------------------------
# Flood Hub is built on HydroSHEDS, so HydroRIVERS v10 is the stream set that
# matches its gauges — and its gauge ids ARE HydroBASINS level-12 codes, which
# HydroRIVERS carries as HYBAS_L12. Used to spread a warned gauge onto the reach it
# sits on, which Flood Hub itself reports only as an isolated point.
#
# Running the same rule on the GEOGLOWS network was tried and reverted: it reaches
# a little further (127 cells against 83) but gives up this id join, which resolves
# two thirds of the gauges exactly rather than by a spatial guess.
HYDRORIVERS_SHP = os.path.join(INPUT_DIR, "HydroRIVERS_v10", "HydroRIVERS_v10.shp")
HYDRORIVERS_TOPOLOGY_CSV = os.path.join(BACKEND, "static", "hydrorivers_topology.csv")
HYDRORIVERS_CROSSWALK_CSV = os.path.join(BACKEND, "static", "h3_hydrorivers_r6.csv")
# A gauge whose id is not a `hybas_` code (today: CWC, ANA, BWDB, MOI — 32 of 99)
# has no join key, so it is snapped to the nearest reach instead: the res-6 cell it
# sits in, then rings outward to this radius. Two rings is about 12 km, which is
# further than a gauge should ever be from its own river and close enough that the
# answer is still that river rather than the next catchment.
HYDRORIVERS_SNAP_RINGS = 2
# What a filled cell calls itself. The reach belongs to the gauge standing on it,
# so the cell names that gauge rather than claiming to be one.
FLOOD_HUB_FILL_PREFIX = "from "

# ---- river network -------------------------------------------------------
# Topology lifted out of the v3 stream tiles by static/build_stream_attrs.py, so
# the daily run can tell a river reported twice from two rivers meeting, and both
# from two rivers that never meet. The daily GEOGLOWS file carries none of this.
STREAM_TILES_PATH = os.path.join(BACKEND, "static", "tiles", "streams.pmtiles")
STREAM_ATTRS_CSV = os.path.join(BACKEND, "static", "stream_attrs.csv")
# How far downstream to look when deciding whether two flagged reaches are the
# same channel. Measured: same-channel pairs sat 1 to 4 reaches apart, so this has
# headroom without being a licence to chain half a basin together.
STREAM_CHAIN_HOPS = 8

# The ADM boundary layer that used to be tiled here is retired — every base map
# already draws national and regional borders. geoBoundaries itself is still read,
# by common/admin.py, for each forecast's "Near" district: see BOUNDARIES_DIR and
# ADMIN_LEVELS above. The tile build is in Other/retired/build_adm_tiles.py.

CELLS_TILES_URL = "pmtiles:///backend/static/tiles/cells.pmtiles"
# One archive holds every resolution, each as its OWN named layer. The names have
# to differ: the archives overlap at z4, z6 and z8, and layers sharing a name would
# be merged into one by the join, putting two resolutions of hexagon in the same
# tile with no way to tell them apart.
CELLS_SOURCE_LAYER = "cells_r{res}"
CELLS_PROMOTE_ID = "h3_id"        # feature-state (hover/selection) needs a stable id


def latest_release(output_dir=None):
    """The newest release folder under the output directory, or None.

    Releases are named YYYY-MM-DD, so newest is last in sort order. Anything that
    is not a release folder is ignored: steps 3 to 5 used to take whichever entry
    the filesystem handed back first, and a stray .DS_Store sorts ahead of every
    real release.
    """
    root = output_dir or OUTPUT_DIR
    if not os.path.isdir(root):
        return None
    names = [n for n in os.listdir(root)
             if re.fullmatch(r"\d{4}-\d{2}-\d{2}", n)
             and os.path.isdir(os.path.join(root, n))]
    return os.path.join(root, max(names)) if names else None
