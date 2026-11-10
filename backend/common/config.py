"""Shared constants and paths for the FEWS4All backend.

The backend runs in two tiers, and the folder layout says which is which:

    backend/static/   built once, rerun only when the reference data changes
                      (H3 and basin geometry -> PMTiles, the cell impact table,
                      the id -> Near table)
    backend/daily/    run per forecast release: intersect -> compare -> summary
                      csv -> map rules

The daily tier joins the static tables rather than recomputing them, which is
what keeps a run short: the ADM cascade is the slowest thing left in the pipeline
and its answer does not change between releases. Impact is not computed here at
all any more -- the Flood Impact Model project publishes a table keyed by H3 cell
and this joins it, cell for cell, with no apportionment anywhere in the path.

Everything user-facing about severity lives here, so the ladder is stated once
and every step (and eventually the front end's rules file) reads the same values.
"""

import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))     # backend/common
BACKEND = os.path.dirname(HERE)                       # backend
ROOT = os.path.dirname(BACKEND)                       # <repo>

# ---- where everything lives ------------------------------------------------
# One tree, dated, holding both what was downloaded and what was published:
#
#   fews4all_data/
#     current_release.txt          the day the map opens on
#     releases.json                every release, summarised
#     YYYY/MM/DD/
#       raw_downloads/
#         rfs/        mapstyletable_<vpu>_<cycle>.parquet
#         glofas/     sumAL_*.nc, summary_alerts_*.nc, RPG_U<cycle>.{shp,...}
#         floodhub/   world_flood_status_<date>.json, urban_flash_floods_<date>.geojson
#         fetch.json  what arrived, when, and how big
#       processed/
#         release.json        the run record, the day manifest and the style tables
#         cell_index.json     which forecast is in which cell, at every resolution
#         day_NN.json         that forecast day's cells and forecasts
#         flash_areas.geojson
#         summary_<date>.csv  every forecast in the release, for a person
#
# WHY THE DATE IS IN THE PATH AND NOT THE FILENAME. The tree is built to be
# copied to a bucket as-is, and a bucket is a flat namespace pretending to be a
# tree -- YYYY/MM/DD sorts, lists and lifecycle-rules cleanly, where a thousand
# files called forecasts.csv in one prefix do not. The Flood Hub files keep their
# date in the NAME as well, because they are the two a person is most likely to
# download on their own, and a file that leaves the tree loses the only copy of
# its date.
#
# THE APP READS THIS DIRECTLY. There is no second published layout, so a path
# here is a URL there: see OUTPUT_BASE in src/sources.js, which is a relative
# path today and a bucket URL the day it moves.
DATA_DIR = os.environ.get("FEWS_DATA") or os.path.join(ROOT, "fews4all_data")

# Kept because a few tools still take an explicit folder, and because a run can
# be pointed at one release without moving anything. Both now resolve INSIDE the
# dated tree rather than naming trees of their own.
INPUT_DIR = os.environ.get("FEWS_INPUT") or DATA_DIR
OUTPUT_DIR = os.environ.get("FEWS_OUTPUT") or DATA_DIR

# The three model folders under raw_downloads. `rfs` rather than `geoglows`
# because that is the product's own name -- the parquets say River Forecast
# System v3 -- and the key this pipeline uses internally is a separate thing.
RAW_DIRS = {"geoglows": "rfs", "glofas": "glofas",
            "flood_hub": "floodhub", "flash": "floodhub"}

# Which models' raw downloads the archive KEEPS once a run has published.
#
# GEOGLOWS does not. Its map-tables are 1.9 GB a morning -- about 90% of
# everything this pipeline downloads -- and it is the one feed that is genuinely
# re-fetchable: the bucket holds roughly 110 cycles, so any day inside three
# months can be pulled again and any day outside it was never going to be.
# Keeping them would cost some 700 GB a year to store a second copy of what
# someone else is already storing, for the days where it is not needed.
#
# The other two stay. GloFAS is 190 MB and the archive this pipeline can reach
# does not go back far. Flood Hub is one megabyte and has no archive AT ALL --
# searchLatestFloodStatus answers for right now and keeps nothing -- so the file
# under floodhub/ is the only copy of that morning that will ever exist. It is
# the smallest thing here and the only irreplaceable one.
#
# The cost of dropping GEOGLOWS: re-running an old day needs its parquets
# fetched again. That is a few minutes inside the bucket's window, and impossible
# outside it, which is the trade being made.
RAW_KEEP = {"geoglows": False, "glofas": True, "flood_hub": True, "flash": True}


def prune_raw(release, root=None):
    """Delete the raw downloads of every model RAW_KEEP says not to archive.

    Called after a run has published, never before: the files are the input that
    run just read, and a failure partway through should leave them where step 2
    can be pointed at them again.
    """
    import shutil
    freed = []
    for model, keep in RAW_KEEP.items():
        if keep:
            continue
        d = raw_dir(release, model, root)
        if not os.path.isdir(d):
            continue
        size = sum(os.path.getsize(os.path.join(d, f)) for f in os.listdir(d)
                   if os.path.isfile(os.path.join(d, f)))
        shutil.rmtree(d, ignore_errors=True)
        if not os.path.isdir(d):
            freed.append((RAW_DIRS[model], size))
    return freed


def release_dir(release, root=None):
    """`2026-11-05` -> <data>/2026/11/05. The one place a date becomes a path."""
    y, m, d = str(release).split("-")[:3]
    return os.path.join(root or DATA_DIR, y, m, d)


def raw_dir(release, model=None, root=None):
    """<release>/raw_downloads[/<model folder>]."""
    base = os.path.join(release_dir(release, root), "raw_downloads")
    return os.path.join(base, RAW_DIRS[model]) if model else base


def raw_dir_under(release_dir_path, model):
    """<some release folder>/raw_downloads/<model folder>.

    Takes the FOLDER rather than the date, because the readers are handed a
    folder and a release may be pointed at by hand. Returns None for a model with
    no raw folder of its own, so callers can skip it without a special case.
    """
    name = RAW_DIRS.get(model)
    return os.path.join(release_dir_path, "raw_downloads", name) if name else None


def processed_dir(release, root=None):
    """<release>/processed -- what the map fetches."""
    return os.path.join(release_dir(release, root), "processed")


def list_releases(root=None):
    """Every published release in the dated tree, oldest first, as `YYYY-MM-DD`.

    A release counts when it has a processed/ folder. The raw_downloads for a day
    can exist without it -- a run that fetched and then failed leaves exactly
    that -- and a half-published day must not appear in the index as if the map
    could draw it.

    The walk is three shallow listdirs rather than os.walk, because the tree is
    also the raw archive: walking it would descend into a hundred and sixty
    parquets per day for no reason.
    """
    root = root or DATA_DIR
    if not os.path.isdir(root):
        return []
    out = []
    for y in os.listdir(root):
        if not re.fullmatch(r"\d{4}", y):
            continue
        for m in os.listdir(os.path.join(root, y)):
            if not re.fullmatch(r"\d{2}", m):
                continue
            for d in os.listdir(os.path.join(root, y, m)):
                if not re.fullmatch(r"\d{2}", d):
                    continue
                # The folder is not the release. An interrupted copy leaves an
                # empty processed/ behind, and a day indexed on the folder alone
                # points the map at a release that has nothing in it. The
                # manifest is written last, so its presence is what makes a day
                # real.
                if os.path.isfile(os.path.join(root, y, m, d, "processed",
                                               PROCESSED["release"])):
                    out.append(f"{y}-{m}-{d}")
    return sorted(out)


def release_of_path(path):
    """<data>/2026/11/05/... -> `2026-11-05`, or None. The inverse of release_dir,
    so a folder handed to a step still knows which day it is."""
    parts = os.path.normpath(os.path.abspath(path)).split(os.sep)
    for i in range(len(parts) - 3, -1, -1):
        y, m, d = parts[i:i + 3]
        if re.fullmatch(r"\d{4}", y) and re.fullmatch(r"\d{2}", m) and re.fullmatch(r"\d{2}", d):
            return f"{y}-{m}-{d}"
    return None

FILES = os.path.join(ROOT, "Files")                   # static reference data

# backend/static holds the builders (code) and what they built. The two are kept
# apart so the directory says which is which:
#
#   tables/  big derived tables, parquet. Rebuildable by the build_*.py beside
#            them, which is what makes them safe to keep out of git.
#   ref/     small files a person reads or edits by hand, and caches small enough
#            to be worth keeping: country codes, the patch list of cells the
#            automated sources miss, the record of what the blackout box hides.
#   tiles/   the PMTiles archives the map draws from.
STATIC_TABLES = os.path.join(BACKEND, "static", "tables")
STATIC_REF = os.path.join(BACKEND, "static", "ref")
# h3_id (res 6) -> river_id, report_id. The reach crosswalk: every cell a
# GEOGLOWS reach passes through, which is how a line becomes a set of cells.
CROSSWALK_TABLE = os.path.join(FILES, "h3_streams_global_r6.parquet")
# `river_id`, NOT `report_id`. The two columns answer different questions and this
# was joined on the wrong one, silently and with a bias.
#
#   river_id   WHICH REACH is this. One row per (cell, reach) crossing. The id a
#              GEOGLOWS forecast carries as its comid.
#   report_id  WHICH REACH SPEAKS FOR THIS CELL. A per-(cell, reach) answer, not a
#              property of the reach: streams.py's resolution rules precomputed, so
#              the same river carries different report_ids in different cells.
#
# A reach only ever appears in the report_id column if it is the representative of
# some cell somewhere, which trunks are and tributaries largely are not. Joining
# the day's comids against it therefore dropped every reach that is nobody's
# representative: 10,594 of 19,490 flagged reaches in the release this was found
# on — 54%, and biased toward small streams rather than spread evenly.
#
# The deduplication report_id was standing in for still happens, further down, in
# step 2's resolve_reaches via the real network rules in common/streams.py. That is
# where it belongs: it needs to know which reaches are flagged TOGETHER in a cell,
# which a precomputed column cannot.
CROSSWALK_ID_COL = "river_id"

# Where each GEOGLOWS v2 reach is. Built by static/build_reach_points.py.
# The daily map-tables carry no coordinates, and admin.assign places a forecast by
# coordinate alone, so without this every GEOGLOWS forecast loses its district.
REACH_POINTS = os.path.join(STATIC_TABLES, "reach_points_v2.parquet")

# ---- the clock -------------------------------------------------------------
# EVERYTHING IN THIS PIPELINE IS UTC. Not a preference — the models leave no
# choice. Flood Hub stamps its times with a Z, and GEOGLOWS and GloFAS name their
# cycles 00Z in a filename with no timezone recorded anywhere else, so the only
# clock all four agree on is UTC.
#
# That means time.gmtime(), never time.localtime(), and a Z on anything written
# with an hour in it. The rule exists because mixing them is invisible: a run.json
# once carried `generated: ...T16:52:27Z` beside `file_mtime: ...T10:45:40`, six
# hours apart with nothing to say which clock either used.
#
# It also means a date derived from a file's mtime is a UTC date. In US Mountain
# time those differ for any file written between 00:00 and 06:00 UTC — which is
# precisely when the GEOGLOWS cycle lands.
#
# The script's log files are named with `date -u` for the same reason, so an
# evening run lands in the next day's log. That is correct, not a bug.

# ---- running daily against live data ---------------------------------------
# A live feed fails differently from an archive. An archive is absent or complete;
# a feed is late, truncated, empty, or yesterday's file left in place. The rules
# below are the ones that decide whether a model is trusted into a release.
#
# The governing choice, and it is a forecasting-operations choice rather than a
# code one: a release is written WITHOUT a model we cannot vouch for, and says so.
# A thin map that carries today's date beats a complete map nobody can date.

# The name of the per-release run record. Holds, per model, the file used and its
# timestamp, the issue date, the counts, and the status with its reasons. Every
# question about "what did we actually publish that morning" is answered here, and
# it is also the baseline the volume check reads from.
MANIFEST_NAME = "run.json"
MANIFEST_BASELINE_N = 10          # how many past releases the volume check averages

# The release steps 3 to 5 are meant to operate on, written by step 2 when it
# finishes. Before this, each later step independently picked "the newest folder",
# which is a different question and answers it wrongly in the two cases that
# matter: a rerun of an older release (the later steps would wander off onto a
# newer one) and a step 2 that died midway (the later steps would happily decorate
# a half-written folder). A step that finds no pointer still falls back to newest,
# because refusing to run at all is the worse failure.
# ---- what processed/ is called ---------------------------------------------
# Named here so the backend writes them and the front end fetches them from one
# list, rather than two lists that agree until someone renames a file.
PROCESSED = {
    # The run record, the per-day manifest and the three style tables, together.
    # Four small files that were always read together and never apart.
    "release": "release.json",
    # Which forecast sits in which cell, at every resolution. Day-independent --
    # a forecast's cell never moves -- so it is fetched once per release.
    "cell_index": "cell_index.json",
    # Every forecast in the release, once, with the day span it speaks for. NOT
    # split per day: a GloFAS row spanning days 0-3 would be written into four
    # files and an undated Flood Hub gauge into all sixteen, which measured at
    # 5.9 MB of pure duplication per release. Cells genuinely differ by day and
    # stay split; forecasts do not and no longer are.
    "forecasts": "forecasts.json",
    # One per forecast day: that day's cells. The client picks the forecasts for
    # the day out of the table above, which it has already fetched.
    "day": "day_{day:02d}.json",
    "flash": "flash_areas.geojson",
    # For a person, not for the map. Every forecast in the release, one row each.
    "summary": "summary_{release}.csv",
}

RELEASE_POINTER = "current_release.txt"

# Every release the output directory holds, summarised for the time slider. The
# pointer says which release is current; this says which ones EXIST, which is a
# different question and the only one a browser cannot answer for itself — there
# is no directory listing over HTTP, and probing dates backwards would be both
# slow and wrong on a day the pipeline skipped.
#
# It carries each release's per-model status as well as its date, because a day
# missing a model is not the same kind of day as a complete one: 2026-10-21 and
# -22 were GEOGLOWS-only, and an animation that did not mark them would show
# warnings halving and recovering as though that were the weather.
RELEASE_INDEX = "releases.json"

# The input folder step 1 last filled, read by step 2 when it is not told which
# folder to use. The same bargain as the release pointer: written only after a
# fetch lands cleanly, so it never names a folder that is still filling, and
# falling back to the flat input directory when there is no pointer at all — which
# is what an archived release laid out by hand looks like.
INPUT_POINTER = "current_input.txt"

# Where a run is assembled before anyone can see it. The published release is only
# complete once step 5 has finished — step 3 rewrites cell_forecasts.csv, step 4
# rewrites cells.csv with its impact columns — so a run that dies at step 4 used
# to leave the live folder holding a cells.csv the app cannot read. Building in
# here and moving the finished folder into place at the end means the live release
# is either last night's, whole, or this morning's, whole.
STAGING_DIR = ".staging"

# How far a model's issue date may trail the newest model in the same run before
# it is dropped as stale. Models publish on their own cycles, so same-day is too
# strict; a day and a half absorbs a late cycle without absorbing yesterday's file.
MODEL_DATE_SPREAD_DAYS = 1.5
# ...and how far behind TODAY the whole release may be before the run says so.
RELEASE_MAX_AGE_DAYS = 2

# A model delivering far fewer forecasts than it usually does is the signature of a
# truncated download or an empty upstream response. It is only ever a WARNING: a
# genuinely quiet day looks the same from here, and discarding real forecasts on a
# heuristic is worse than publishing a thin one with a note attached. Zero
# forecasts from a file that exists is the exception — that is not a quiet day,
# that is a file with no data in it.
MODEL_VOLUME_FLOOR = 0.25         # fraction of the recent median that reads as thin

# Statuses a model can carry into a release.
#   ok        read, dated, and trusted
#   thin      read and trusted, but far below its usual volume
#   skipped   no input file at all
#   dropped   read but not trustworthy — stale, undateable, or unreadable
MODEL_OK, MODEL_THIN, MODEL_SKIPPED, MODEL_DROPPED = "ok", "thin", "skipped", "dropped"


BASE_RES = 6                      # the resolution everything is decided at
RESOLUTIONS = [3, 4, 5, 6]        # ...then rolled up to, in step 3

# Input files are discovered by shape, not by exact name, because the names
# carry the release date and change every run.
INPUT_GLOBS = {
    # Parquet first: a cycle publishes ~161 per-VPU parquets carrying the whole
    # 10-day horizon, against ~81 global CSVs of one timestep each. The CSV
    # pattern is kept so an archived release can still be rerun in place.
    "geoglows": ["mapstyletable*.parquet", "mapstyletable*.csv"],
    # JSON first: it is what step 1 writes now. The CSVs stay matched so an
    # archived release from before the change still loads.
    "flood_hub": ["world_flood_status*.json", "world_flood_status*.csv",
                  "Flood_Hub_Global*.csv"],
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

# Which timestep of the forecast a release reports, as an hour UTC on the cycle's
# own date. The parquets carry all 81 three-hourly steps out to ten days, so this
# is a reporting policy rather than a download decision — changing it costs one
# edit and no extra bytes.
#
# A single step matches what the map showed before, and it is a KNOWN COMPROMISE
# rather than a neutral default. Two things are wrong with it, both deliberate for
# now and both worth re-reading before anyone assumes this is settled.
#
# It does not mean what the other models mean. GloFAS publishes the maximum across
# its four lead-time windows, so a GloFAS flag says "worst case within the
# horizon" while this says "conditions at 06Z". Concurrence between them is
# therefore not quite comparing like with like.
#
# And a 3-hourly snapshot can miss the flood entirely. Measured on VPU 201 (an
# alpine region, so near the worst case) for the 2026-08-22 00Z cycle:
#
#     06Z snapshot          0 reaches
#     max over that day    39 reaches   11 extreme, 14 danger, 14 warning
#     max over 10 days     47 reaches   20 extreme,  6 danger, 21 warning
#
#     caught by hour:  00Z 4/39   03Z 1/39   06Z 0/39   09Z 0/39
#                      12Z 11/39  15Z 31/39  18Z 21/39  21Z 6/39
#
# Nothing flagged at 06Z on a day when eleven reaches reached extreme nine hours
# later, and the best single hour still missed 8 of 39. Small steep catchments
# peak in hours; a large-river VPU would be far less sensitive, and globally the
# map does not go empty. But the mechanism is general.
#
# The fix is a max over a window, which costs no extra bytes — the parquets carry
# every step. _publish_step and the filter in read_geoglows_parquet are written to
# make that a contained change.
GEOGLOWS_FORECAST_HOUR = 6

# ---- where the GEOGLOWS forecast comes from --------------------------------
# A public bucket; no credentials. One folder per cycle, named YYYYMMDDHH, always
# at hour 00. Objects land roughly five hours after the cycle time (the 2026-10-21
# 00Z cycle was written at 04:49 UTC), and the bucket keeps about 110 cycles, so
# there is a three-month window to rerun against.
GEOGLOWS_BUCKET = "geoglows-v2-forecast-products"
GEOGLOWS_REGION = "us-west-2"
GEOGLOWS_PREFIX = "map-tables/"

# Which of the two products in each cycle folder to download.
#
#   "csv"      one global file per 3-hourly step, ~119 MB each, no coordinates
#   "parquet"  ~161 per-VPU files, ~1.7 GB, carrying every step of the horizon
#
# PARQUET since the map grew a forecast timeline. The CSVs are one file per step,
# so the horizon would be fourteen separate 119 MB downloads -- about the same
# bytes as the parquets, for a fourteenth of the data. The parquets carry every
# step of the horizon, which means the morning rule is now a decision the READER
# makes (see _publish_steps) rather than one the download forecloses, and moving
# to a max-over-each-day later costs nothing extra to fetch.
#
# IT IS A REAL INCREASE: ~124 MB a morning becomes ~1.7 GB, and step 1 pulls ~161
# files instead of one. If a morning run starts timing out or the link cannot take
# it, putting "csv" back is the whole rollback -- the reader handles both, and the
# map falls back to a day-0-only GEOGLOWS with the rest of the timeline intact.
GEOGLOWS_PRODUCT = "parquet"

# ---- where the Flood Hub forecast comes from -------------------------------
# An authenticated API rather than a bucket, which changes two things.
#
# IT IS NOT REPRODUCIBLE. searchLatestFloodStatus returns current state; there is
# no cycle, no archive, and no way to ask what it said yesterday. GEOGLOWS keeps
# ~110 cycles in its bucket, so a missed morning there is an inconvenience. Here
# a missed morning is gone. The files this writes are the only copy that will
# ever exist, which is why input/<date>/ is an archive for this model even though
# it is scratch space for GEOGLOWS.
#
# AND IT CAN FAIL BY HALVES. A bucket download arrives or does not, and the byte
# count catches the middle. A sweep over 248 countries can return 218 of them and
# exit cleanly, leaving a well-formed CSV that is quietly missing a continent.
# So the regions that error are counted and gate the result (below) instead of
# being printed and forgotten.
FLOOD_HUB_BASE = "https://floodforecasting.googleapis.com/v1"
FLOOD_HUB_KEY_ENV = "FLOODS_API_KEY" # RILEY'S API KEY
COUNTRY_CODES = os.path.join(STATIC_REF, "country_codes.csv")

# Severities that count as "this gauge has a warning", and so earn the extra
# round trips that fetch discharge, peak timing and return period.
FLOOD_HUB_WARNING_SEVERITIES = ("ABOVE_NORMAL", "SEVERE", "EXTREME")
FLOOD_HUB_DISCHARGE_UNIT = "CUBIC_METERS_PER_SECOND"   # metre-stage gauges are skipped
FLOOD_HUB_MODEL_BUCKET = 50        # gaugeModels:batchGet ids per call
FLOOD_HUB_FORECAST_BUCKET = 500    # queryGaugeForecasts ids per call

# A gauge's discharge thresholds ARE its return periods. Checked highest first.
FLOOD_HUB_THRESHOLD_YEARS = [("extremeDangerLevel", 20), ("dangerLevel", 5),
                             ("warningLevel", 2)]

# How much of the world may fail before the sweep is not to be trusted. Marked
# thin rather than dropped: the countries that answered returned real gauges, and
# throwing those away over a partial outage is worse than publishing with a note.
FLOOD_HUB_MAX_REGION_FAILURE_RATE = 0.05

# Which flash-flood polygons to fetch, and what the pipeline calls them. The
# names on the right are what read_flash and FLASH_SEVERITY key on.
FLASH_POLYGON_TYPES = [("likely", "likelyAffectedPolygonId"),
                       ("highly_likely", "highlyLikelyAffectedPolygonId")]

# Caches for the two things that are static but arrive with the daily feed.
# A gauge's thresholds are a property of the gauge; a flash polygon is a fixed
# ~20km urban cell. Re-downloading either every morning is the same mistake as
# re-downloading a reach's coordinates was.
FLOOD_HUB_MODEL_CACHE = os.path.join(STATIC_REF, "flood_hub_gauge_models.json")
FLASH_POLYGON_CACHE = os.path.join(STATIC_REF, "flash_polygons.json")

# ---- where the GloFAS forecast comes from -----------------------------------
# ECMWF's Auxiliary Data Store, over HTTPS rather than the FTP the CEMS wiki
# leads with — which means this fetch is shaped like the GEOGLOWS one (list a
# day, take some files) rather than like the Flood Hub one.
#
# Credentials are sent as HTTP Basic, so they travel in a header and NOT in the
# URL. That is the opposite of Flood Hub and it is why this module may safely
# print the URL it tried: a logged ECPDS URL gives nothing away, and saying
# exactly what was attempted is the difference between a fixable 404 and a
# mystery.
#
# Retention is 15 days, so like Flood Hub this is a feed whose past cannot be
# re-fetched. What lands in input/<date>/ is the only copy.
#
# The cycle folder is dated and the filenames inside it are fully determined by
# the stamp, so the newest cycle is found by PROBING for one file rather than by
# parsing the listing's HTML. Fewer moving parts, and it survives a redesign of
# their web interface.
ECPDS_BASE = "https://aux.ecmwf.int/ecpds/data"
ECPDS_PRODGEN = "prodgen"
ECPDS_USER_ENV = "ECPDS_USER"
ECPDS_PASSWORD_ENV = "ECPDS_PASSWORD"
ECPDS_LOOKBACK_DAYS = 4        # how far back to probe before giving up

# The eleven objects a cycle owes us. The three sumAL grids are the alert levels
# models.py reads together — a missing one drops GloFAS rather than understating
# it — the three summary_alerts grids are the same alerts split by lead time, and
# the RPG_U set is one shapefile in five parts.
GLOFAS_FILES = [
    "sumAL_medium_AL_{stamp}.nc",
    "sumAL_high_AL_{stamp}.nc",
    "sumAL_extreme_AL_{stamp}.nc",
    "summary_alerts_1_3_{stamp}.nc",
    "summary_alerts_4_10_{stamp}.nc",
    "summary_alerts_11_30_{stamp}.nc",
    "RPG_U{stamp}.shp",
    "RPG_U{stamp}.shx",
    "RPG_U{stamp}.dbf",
    "RPG_U{stamp}.prj",
    "RPG_U{stamp}.qix",
]
GLOFAS_PROBE_FILE = "sumAL_medium_AL_{stamp}.nc"   # the one that decides a cycle exists
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

# ---- the lead-time bands ---------------------------------------------------
# sumAL answers "does this pixel flood" and nothing about WHEN: it is a maximum
# over the whole horizon with no time axis to recover. summary_alerts answers the
# same question three times, once per lead band, on the same 0.05-degree grid.
#
# Each file holds one code per pixel. ECMWF documents the code as four return
# periods x three probability classes, ascending, 5..16:
#
#     5-7  = 1.5-year    8-10 = 2-year    11-13 = 5-year    14-16 = 20-year
#
# Our files carry no 1.5-year class at all, so the published range is 8..16 and
# maps exactly onto the three severities already in GLOFAS_SEVERITY.
#
# VERIFIED, not assumed. Decoding the three bands and taking the worst class per
# pixel reproduces sumAL's own classification on the 2026-09-23 cycle across all
# 26,185 alerting pixels: 100% exact, nothing alerting in sumAL without a band
# code and nothing carrying a band code that sumAL is silent on. The bands ARE
# the sumAL alerts, told apart by lead time — which is the only reason it is safe
# to publish a severity from them.
#
#     python3 - <<'EOF'   # the check, if it ever needs repeating
#     ... decode each band, take the max class per pixel, compare with sumAL
#     EOF
GLOFAS_SUMMARY_FILES = {
    "1-3":   "summary_alerts_1_3_{stamp}.nc",
    "4-10":  "summary_alerts_4_10_{stamp}.nc",
    "11-15": "summary_alerts_11_30_{stamp}.nc",   # named _30, carries 11-15
}
GLOFAS_SUMMARY_VAR = "max_prob_summary_{band}"
GLOFAS_CODE_LEVELS = [(8, 10, 1), (11, 13, 2), (14, 16, 3)]   # lo, hi, severity group

# Which forecast days each band covers, as day_from..day_to with day 0 = release.
#
# THE FIRST BAND STARTS AT 0, NOT 1. GloFAS numbers its steps from 1 and step 1 is
# the 00Z cycle plus 24 hours, so read literally the "1-3" band says nothing about
# the day the forecast was issued — and GloFAS would vanish from the day the map
# opens on, which is the day most people will ever look at. Step 1 covers the
# period from issue to +24h, so the first band is published as 0-3: it includes
# today because its own window does.
#
# The later bands are taken literally. Stretching those would be inventing lead
# time rather than reading it.
GLOFAS_BANDS = {"1-3": (0, 3), "4-10": (4, 10), "11-15": (11, 15)}
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
# ---- the forecast day axis -------------------------------------------------
# A release reports a horizon, not a moment. Day 0 is the release date; day N is
# N days later. Every forecast row carries the span of days it speaks for, as
# day_from..day_to inclusive, and the map draws a row on day D when
# day_from <= D <= day_to.
#
# One span covers all four models, which is the point — they mean very different
# things by "forecast" and a single pair of columns is what lets them share a
# timeline without pretending otherwise:
#
#   GEOGLOWS   [N, N]              one morning step; asserts THAT day
#   Flood Hub  [start, end]        the published window, 1-2 days wide
#   Flood Hub  [0, HORIZON-1]      ...when the gauge publishes no window at all
#   GloFAS     [0, HORIZON-1]      the grid is a max over the horizon with no
#                                  time axis to recover, so it spans everything
#   flash      [0, 1]              24 hours, which straddles two days
#
# The WIDTH of a span is therefore a confidence signal and not just bookkeeping.
# A width of 1 means the model named that day. A width of HORIZON means it did
# not, and the row is on the map because it has to go somewhere. Agreement uses
# this: see CONCUR_MAX_SPAN below.
#
# 16 because GloFAS's last lead band runs to day 15 and a ceiling that clipped it
# would silently shorten a band the source published. It is a CEILING, not the
# length: step 6 ends the timeline at the last day some forecast actually names,
# which with today's products is day 9 (GEOGLOWS) or day 15 (GloFAS bands). An
# over-generous ceiling costs nothing; a tight one loses data.
HORIZON_DAYS = 16

# How wide a span may be and still count toward model agreement. A GloFAS cell
# spanning the whole horizon would otherwise agree with every GEOGLOWS day it
# touches, by construction, and concurrence that cannot fail is not a measurement.
# Set to HORIZON_DAYS to count everything, 1 to demand that both models named the
# same day.
#
# IT IS DELIBERATELY OPEN. At HORIZON_DAYS the day-0 map is bit-for-bit what it
# was before the timeline existed, which is the only setting that can be turned on
# without re-reading every agreement figure anyone has already looked at. Tighten
# it once there is a reason to, not as part of a change about something else:
# at 3 GloFAS stops contributing to agreement entirely, and that is a large claim
# to make silently.
CONCUR_MAX_SPAN = HORIZON_DAYS


def day_dir(release_dir, day):
    """<release>/days/dNN — where one forecast day's delivered tables live."""
    return os.path.join(release_dir, "days", f"d{int(day):02d}")


FORECAST_COLUMNS = [
    "forecast_uid", "model", "severity", "native_id",
    "return_period_yr", "flow_cms",
    "issued_time", "start_time", "peak_time", "end_time",
    "lat", "lon",
    # Identity a model happens to publish about the place it forecasts for.
    # Blank for models that do not; never invented.
    "country", "region", "basin", "sub_basin", "station", "river", "lead_time_days",
    # The forecast day span this row speaks for; see HORIZON_DAYS above.
    "day_from", "day_to",
    # The day in this release when this forecast is at its worst, derived in
    # step 6 from the row's own siblings. Blank where the model does not resolve
    # a peak: a GloFAS pixel or a Flood Hub window has ONE severity across its
    # whole span, so there is no worst day to name and inventing one would be the
    # same mistake as Flood Hub's own peakTime field.
    "peak_day",
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
CELL_COLUMNS = ["h3_id", "res", "day", "severity", "model_count", "models",
                "co_models", "forecast_count"]

# Step 4's enrichments. Impact is joined onto cells; Near onto forecasts. Both
# are written back over the same files rather than into a second delivered copy:
# every added column is new, and each step reads only columns it wrote or found,
# so re-running one is safe and leaves one set of files per release.
# Built by the Flood Impact Model project, not by this one: it writes an H3 r6
# table and this reads it. The columns and their units are its contract, recorded
# beside the data in cell_impact_r6_metadata.csv -- read that before assuming what
# a number means, because two of these have already changed shape once.
#
#   population         people          WorldPop 2024 constrained 1 km (R2025A)
#   buildings          count           OSM footprint centres
#   farmland_km2       km2             ESA WorldCover 2021 cropland
#   transportation_km  km              OSM roads AND railways, together
#   bridges            count           OSM bridge=* segment centres
#   dams               count           OSM waterway=dam
#   weirs              count           OSM waterway=weir, check_dam
#
# TWO THINGS CHANGED when this replaced the table this pipeline used to build for
# itself (by apportioning HUC12 basin statistics across the cells each basin
# covered; that script is gone, and nothing here distributes anything any more).
# Both changes are the silent kind:
#
#   farmland is km2 now, not m2. The panel used to divide by 1e6 and no longer
#   does. Getting this wrong is a factor of a million, in the direction that makes
#   a field look like a continent.
#
#   roads and railways are ONE column. They cannot be shown apart any more, which
#   is a real loss and not a rename -- the upstream table does not carry them
#   separately, so no amount of work here brings the distinction back.
IMPACT_FIELDS = ["population", "buildings", "farmland_km2", "transportation_km",
                 "bridges", "dams", "weirs"]
# Which of those are whole things rather than measurements, for step 4's rounding.
IMPACT_COUNT_FIELDS = {"population", "buildings", "bridges", "dams", "weirs"}
CELL_DELIVERY_COLUMNS = CELL_COLUMNS + IMPACT_FIELDS
FORECAST_DELIVERY_COLUMNS = FORECAST_COLUMNS + ["district", "district_level"]

CELL_IMPACT_TABLE = os.path.join(STATIC_TABLES, "cell_impact_r6.parquet")


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
    # Shifted TWICE, for the same reason both times. Positions 3/5/7 were still too
    # pale at the light end to read on the PANEL, where the legend sits on white
    # paper rather than over the basemap: purples' `warning` was 15.3 dE from bare
    # paper and blues' 18.4, and composited at the default 30% fill over the map
    # they were 3.5 and 4.6 dE from the bare basemap — at or below the threshold
    # where a color registers at all.
    #
    # At 4/6/8 the worst light rung is 28.0 dE on paper and 7.1 dE on the map, so
    # the low end roughly doubled in presence again, while the closest neighbouring
    # pair is still 23.6 dE apart. Going one further, to 5/7/9, would buy another
    # 15 dE at the light end but push `extreme` to near-black (#08306b, #00441b),
    # where the hue that tells the ramps apart is gone.
    #
    # All three rungs move together, never just the first: lifting `warning` alone
    # squeezed its gap to `danger` down to 3.2 dE on blues and purples, which is
    # its own kind of unreadable.
    #
    # The matplotlib family above was measured at the same time and left alone --
    # its weakest light rung (cividis) is already 46.2 dE on paper and 12.7 on the
    # map, and every sampling that darkened it made the rung gaps worse.
    #
    # They still run dark-is-worst, which is ColorBrewer's own convention and the
    # readable one here — the palest rung belongs on `warning`, where being quiet
    # costs nothing, not on `extreme`.
    # Blues joins purples one rung lower (9-class 5/7/9). It was the weakest light
    # rung left after purples moved -- 20.2 dE against the basemap at the default
    # 60% heat opacity -- and it is the ramp the agreement views give the models,
    # so it is read beside that purple rather than instead of it.
    #
    # Its `extreme` is now #08306b, which IS close to black (L* 19). Accepted here
    # and not for greens or oranges: blues is only ever the quiet end of an
    # agreement view, where the eye is meant to go to the purple and the red, and a
    # near-black `extreme` on a model ramp reads as emphasis rather than as a lost
    # hue. If the three-rung blues bar ever has to be told apart on its own, this is
    # the first thing to put back.
    {"id": "blues", "label": "Blues",
     "warning": "#6baed6", "danger": "#2171b5", "extreme": "#08306b"},
    {"id": "greens", "label": "Greens",
     "warning": "#a1d99b", "danger": "#41ab5d", "extreme": "#006d2c"},
    {"id": "oranges", "label": "Oranges",
     "warning": "#fdae6b", "danger": "#f16913", "extreme": "#a63603"},
    # Purples alone sits one rung LOWER on the ladder than its four siblings
    # (9-class 5/7/9 rather than 4/6/8). It carries agreement rather than a model,
    # so it is the one ramp a reader goes looking for on purpose, and at 4/6/8 it
    # had the weakest light rung of the family: 14.7 dE against the basemap at the
    # default 60% heat opacity, where the next worst was blues at 20.2. At 5/7/9 it
    # is 23.4, and its rung gaps (35.5 / 32.9) are the healthiest of the set. Its
    # 9th class is a deep violet rather than the near-black the other schemes reach,
    # which is why this step works here and not for blues or greens.
    {"id": "purples", "label": "Purples",
     "warning": "#9e9ac8", "danger": "#6a51a3", "extreme": "#3f007d"},
    {"id": "reds", "label": "Reds",
     "warning": "#fc9272", "danger": "#ef3b2c", "extreme": "#a50f15"},
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
HYDRORIVERS_TOPOLOGY_TABLE = os.path.join(STATIC_TABLES, "hydrorivers_topology.parquet")
HYDRORIVERS_CROSSWALK_TABLE = os.path.join(STATIC_TABLES, "h3_hydrorivers_r6.parquet")
# ---- the cell universe ----------------------------------------------------
# Which cells the geometry archive covers. This used to BE the impact table, and
# that coupling was a bug: a cell needs geometry because something can flag it,
# which is a different question from whether we have exposure numbers for it.
# Where the two disagreed the cell was flagged in the tables and had no hexagon
# on the map at all — invisible, unhoverable, unclickable. Hawaii is the case
# that found it: 36 GloFAS cells, 31 of them extreme, absent from the map.
#
# So the universe is a UNION of every source that can put a cell on the map, and
# the impact table is one contributor among them rather than the definition.
# Each entry is (path, column holding the h3 id); a source that is not present is
# skipped with a note rather than failing the build.
#
# The extra file is the patch hatch. The three bulk sources all descend from the
# same hydrography and share its blind spots — none of them has ever heard of
# Hawaii — so there has to be somewhere to put cells that only one model knows
# about, without re-plumbing anything. build_cell_universe.py writes it.
# The canonical registry: the one file that says which res-6 cells exist.
# Written by static/build_cell_universe.py as a union of land, the crosswalks
# and the GloFAS river mask. Read by the tile build as its universe, and by
# nothing else — in particular it is never used to filter a forecast.
CELL_UNIVERSE = os.path.join(STATIC_TABLES, "cell_universe_r6.parquet")
# Superseded by CELL_UNIVERSE; kept as a source so an existing patch file is
# still honoured rather than silently dropped.
CELL_UNIVERSE_EXTRA = os.path.join(STATIC_REF, "cell_universe_extra.csv")
CELL_UNIVERSE_SOURCES = [
    (CELL_UNIVERSE, 0),               # the registry, once built
    (CELL_IMPACT_TABLE, 0),
    (CROSSWALK_TABLE, 0),                    # GEOGLOWS reaches
    (HYDRORIVERS_CROSSWALK_TABLE, 0),        # Flood Hub's network
    (CELL_UNIVERSE_EXTRA, 0),              # whatever the above still miss
]
# A gauge whose id is not a `hybas_` code (today: CWC, ANA, BWDB, MOI — 32 of 99)
# has no join key, so it is snapped to the nearest reach instead: the res-6 cell it
# sits in, then rings outward to this radius. Two rings is about 12 km, which is
# further than a gauge should ever be from its own river and close enough that the
# answer is still that river rather than the next catchment.
HYDRORIVERS_SNAP_RINGS = 2
# What a filled cell calls itself. The reach belongs to the gauge standing on it,
# so the cell names that gauge rather than claiming to be one.
FLOOD_HUB_FILL_PREFIX = "from "

# ============================================================================
# ##  TEMPORARY  ##  GEOGLOWS BLACKOUT BOX  ##  NOT A FIX  ##
# ============================================================================
#
#   EVERY GEOGLOWS forecast inside the box below is DELETED from every release
#   before anything else sees it. Not drawn, not counted, not available to
#   agreement, not in the panel. Finland, Karelia and their neighbours are a HOLE
#   in the GEOGLOWS layer. The other models still report there normally.
#
#   THIS HIDES REAL FLOODS. If a river in this box genuinely floods, FEWS4All will
#   not say so. That was accepted knowingly on 2026-10-29 and it stays wrong until
#   the cause below is fixed.
#
# WHY
#   GEOGLOWS v2 has a gap between its retrospective record and its forecast
#   climatology. Where the two do not meet, the return period a flow is measured
#   against comes out far too low, so ordinary flow clears its "2-year" threshold
#   permanently. This region is the worst instance found: the whole of Finland lit
#   amber, every day, since the first release we kept.
#
# THE EVIDENCE (10 releases, 2026-09-23 .. 2026-10-29, 36 days)
#   5,577 reaches in the box were flagged at least once
#   1,379 of them were flagged on EVERY ONE of the 10 days
#   1,054 of those (76%) reported the SAME severity on all 10
#   1,009 of those (73%) sat permanently on the 2-year rung
#   Floods end. These do not.
#
# WHY A BOX AND NOT A LIST OF REACHES
#   A list built from the days we happen to have cannot make a hole: a reach that
#   lights up for the first time tomorrow is not on it, so the blob grows back one
#   reach at a time and the list has to be rebuilt forever. A box is a standing
#   statement -- "we do not trust GEOGLOWS here" -- which is the true one, and it
#   needs no maintenance. It is also honest about its own cost: it is obvious at a
#   glance that everything inside is gone, where a list of 5,577 ids is not.
#
# WHAT THIS IS NOT
#   Not a fix, not a filter the user can turn off, and not a statement about those
#   rivers. It is a piece of tape over a known upstream defect.
#
# BEFORE THIS CAN BE REMOVED
#   - Raise the climatology gap with the GEOGLOWS team; it is their data, and the
#     same defect is presumably hurting every other consumer of v2 silently.
#   - static/build_suppression.py reports what the box is currently swallowing --
#     run it before deciding, so the decision is made against numbers.
#   - When v2's return periods are rebuilt, set the box to None, DELETE this block,
#     and rerun a release to confirm the region goes quiet on its own.
#   - Until then it says so where it cannot be missed: the run log prints a line
#     every single run, and run.json records the count.
#
# The box is west, south, east, north in degrees -- the extent of the screenshot
# the artifact was reported from, which is Finland plus Russian Karelia and their
# margins. Set it to None to switch the blackout off entirely.
# ============================================================================
GEOGLOWS_BLACKOUT_BOX = (17.0, 57.0, 45.0, 71.0)
# A record of what was being flagged in there when the box went in, written by
# static/build_suppression.py. NOTHING IN THE DAILY RUN READS THIS -- the box is
# the rule. It exists so "what are we hiding?" has an answer that does not depend
# on anyone still having the releases from October 2026.
GEOGLOWS_SUPPRESSED_CSV = os.path.join(STATIC_REF, "geoglows_suppressed.csv")

# ---- river network -------------------------------------------------------
# Topology read out of the GEOGLOWS v2 model table by static/build_stream_attrs.py,
# so the daily run can tell a river reported twice from two rivers meeting, and both
# from two rivers that never meet. The daily GEOGLOWS file carries none of this.
#
# It has to be v2, because the forecasts are v2. This table was first built from the
# v3 stream TILES, which are a different network with a different id space: only
# 68.5% of v2 reach ids appear in v3 at all, so on a measured release 47.4% of
# flagged reaches were simply unknown to the table and 3,572 of 3,591 multi-reach
# cells could not be resolved. A reach the table does not know is never merged -- by
# design, in streams.py -- so an unknown reach is not a wrong answer, it is a cell
# that keeps reporting four rivers when it holds one.
#
# The model table is the same file the h3 crosswalk was built from (its DSLINKNO is
# what produced `report_id`), so the topology here and the topology behind the
# crosswalk are now one source. 6,838,900 reaches, exactly the crosswalk's id set.
#   LINKNO -> river_id   DSLINKNO -> next_id (-1 = terminal, written blank)
#   TerminalLink -> outlet_id   DSContArea -> ds_cont_area   strmOrder -> strahler
STREAMS_V2_MODEL_TABLE = os.path.join(
    ROOT, "Other", "matching", "files", "v2-model-table.parquet")
# The v3 tiles stay, for map CONTEXT only -- the stream lines drawn under the
# hexagons. Nothing in the daily run reads topology from them any more.
STREAM_TILES_PATH = os.path.join(BACKEND, "static", "tiles", "streams.pmtiles")
STREAM_ATTRS_TABLE = os.path.join(STATIC_TABLES, "stream_attrs.parquet")
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


def current_input(input_dir=None):
    """The release folder step 2 should read.

    What step 1 last fetched, by the pointer it leaves; failing that, the newest
    day in the tree that actually has raw downloads. The fallback matters more
    than it used to: the pointer names a date and the date is now a PATH, so a
    missing or stale pointer used to leave step 2 reading the root of the archive
    and finding nothing, which looks exactly like a morning when no model
    delivered.
    """
    root = input_dir or INPUT_DIR
    try:
        with open(os.path.join(root, INPUT_POINTER), encoding="utf-8") as f:
            name = f.read().strip()
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", name):
            path = release_dir(name, root)
            if os.path.isdir(os.path.join(path, "raw_downloads")):
                return path
            print(f"  note: {INPUT_POINTER} names {name}, which has no raw "
                  f"downloads — looking for the newest day instead")
    except OSError:
        pass

    days = []
    for y in sorted(os.listdir(root)) if os.path.isdir(root) else []:
        if not re.fullmatch(r"\d{4}", y):
            continue
        for m in sorted(os.listdir(os.path.join(root, y))):
            for d in sorted(os.listdir(os.path.join(root, y, m))):
                if os.path.isdir(os.path.join(root, y, m, d, "raw_downloads")):
                    days.append((f"{y}-{m}-{d}", os.path.join(root, y, m, d)))
    return days[-1][1] if days else root


def release_pointer(output_dir=None):
    return os.path.join(output_dir or OUTPUT_DIR, RELEASE_POINTER)


def write_release_pointer(release, output_dir=None):
    """Record which release the later steps should work on. Never fatal."""
    try:
        p = release_pointer(output_dir)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(release.strip() + "\n")
        os.replace(tmp, p)
        return p
    except OSError as e:
        print(f"  note: could not write {RELEASE_POINTER} ({e})")
        return None


def current_release(output_dir=None):
    """The release step 2 last published, or the newest folder if there is no
    pointer. Returns a path, or None when the output directory holds no release.
    """
    root = output_dir or OUTPUT_DIR
    try:
        with open(release_pointer(output_dir), encoding="utf-8") as f:
            name = f.read().strip()
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", name):
            path = os.path.join(root, name)
            if os.path.isdir(path):
                return path
            print(f"  note: {RELEASE_POINTER} names {name}, which does not exist "
                  f"— falling back to the newest release")
    except OSError:
        pass
    return latest_release(output_dir)


def latest_release(output_dir=None):
    """The newest release folder under the output directory, or None.

    Releases are named YYYY-MM-DD, so newest is last in sort order. Anything that
    is not a release folder is ignored: steps 3 to 5 used to take whichever entry
    the filesystem handed back first, and a stray .DS_Store sorts ahead of every
    real release.
    """
    root = output_dir or OUTPUT_DIR
    names = list_releases(root)
    return release_dir(names[-1], root) if names else None
