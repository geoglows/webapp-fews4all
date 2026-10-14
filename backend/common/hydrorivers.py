"""Flood Hub's own network — HydroRIVERS v10 — and the rule for spreading a gauge.

Flood Hub publishes gauges, not reaches. On the map that is a scatter of single
hexagons: the Brahmaputra warned at four places reads as four unrelated dots, the
river between them unflagged even though the same flood is in it. GEOGLOWS does
not have this problem because it forecasts a reach, and a reach joins through the
crosswalk to every cell it crosses.

Flood Hub is built on HydroSHEDS, so HydroRIVERS v10 IS its network — and its
gauge ids say so literally. A Flood Hub `gaugeId` is `hybas_<HYBAS_L12>`, and
HYBAS_L12 is a column of the HydroRIVERS shapefile. So the join is a string
match, not a spatial guess, for the gauges that carry such an id (67 of 99 in the
release this was built against; the other 32 snap to the nearest reach).

THE RULE: a warned gauge flags its own reach, and nothing beyond it.

A HydroRIVERS reach is the stretch of river between two confluences — the unit
the network is actually drawn in — so this is the largest claim the data makes on
the gauge's behalf without inferring anything. The gauge observed a flood; the
reach is the piece of river it observed it ON; the cells that piece crosses carry
it. Past the next confluence the river is a different reach with a different
catchment, and nothing has been observed there.

What that costs, measured against the release this was written for: 99 gauges land
on 99 distinct reaches, which cross 181 cells in all. 99 of those are the gauge
cells themselves, so the rule adds about 82. A reach is a short thing — across all
8.5M of them the median crosses 2 res-6 cells, the mean 1.76 — so this spreads a
gauge by roughly one hexagon, not along a river.

That is deliberate, and it replaced a rule that reached much further: walk
downstream from a warned gauge and keep the span if it met a SECOND warned gauge,
which filled 1,402 cells. Every cell it filled sat between two observations, so
nothing was inferred past one — but the span itself was inferred, and the stretch
of river it claimed was never observed in the middle. The rule here claims only
what a gauge sits on. The walk is gone, and with it `next_down`: this module no
longer reads that column at all.

The other direction was considered and rejected. HydroRIVERS' MAIN_RIV column
names the whole river SYSTEM a reach belongs to, and flagging that would put a
single Ganges gauge on the entire Ganges: our 99 gauges sit in 29 systems holding
656,606 reaches and 276,688 cells.

Running the same rule on the GEOGLOWS v3 network instead was tried and reverted.
It works — all 99 gauges snap inside their own hexagon, and it flags 127 cells
rather than 83 — but it gives up the exact HYBAS id join that resolves two thirds
of the gauges here, and the extra reach was not worth that. If it is ever picked
up again, the one thing to know is that the GEOGLOWS crosswalk's `report_id` is
NOT the reach: it is a per-cell answer to which reach speaks for a hexagon, and
the same river carries different report_ids in different cells. `river_id` is the
line segment.

  attribution   The gauge owns its reach's cells. They carry its severity, its
                times and its values, and they name it rather than pretending to
                be a gauge of their own: `from <gaugeId>`. The filled cell is an
                inference about where the flood is, and a reader clicking one is
                owed the difference between that and a reading.

Where two claims land on one cell — two warned reaches crossing the same hexagon —
the claim with the WORSE severity wins, then the larger drainage area. Never the
milder one: a cell two models would call extreme and warning is extreme, and the
same has to hold for one model's own geometry.

Reads two tables from the static tier (static/build_hydrorivers.py builds both):

    hydrorivers_topology.csv    hyriv_id, next_down, hybas_l12, upland_skm, main_riv
    h3_hydrorivers_r6.csv       h3_id, hyriv_id

Only three of the topology's columns are read — `next_down` and `main_riv` are
built but unused — and it is 8.5M rows, so it is held as sorted numpy columns and
looked up by binary search rather than as dicts: the dict form is about 2 GB and
has OOMed this machine before. The crosswalk is 14.9M rows and is never held at
all: it is streamed and tested against the few dozen reaches a day's gauges touch.
"""

import csv
import os
import re
import sys

from . import config as C

GAUGE_HYBAS = re.compile(r"^hybas[_-]?(\d+)$", re.I)


class Network:
    """HydroRIVERS as a graph. Loaded once per run; absent reaches answer None."""

    def __init__(self, path=None):
        self.path = path or C.HYDRORIVERS_TOPOLOGY_CSV
        self.ids = None          # sorted hyriv_id
        self.hybas = None        # HYBAS_L12
        self.area = None         # UPLAND_SKM
        self.loaded = False

    def load(self):
        if self.loaded:
            return self
        self.loaded = True
        if not os.path.exists(self.path):
            return self
        try:
            import numpy as np
            import pandas as pd
        except ImportError:
            print("  note: numpy/pandas not available; HydroRIVERS fill skipped",
                  file=sys.stderr)
            return self
        # `next_down` is not read. The fill no longer walks downstream, and a
        # column loaded for nobody is 68 MB of int64 held for the whole run.
        df = pd.read_csv(self.path,
                         usecols=["hyriv_id", "hybas_l12", "upland_skm"],
                         dtype={"hyriv_id": "int64",
                                "hybas_l12": "int64", "upland_skm": "float32"})
        df.sort_values("hyriv_id", inplace=True)
        self.ids = df["hyriv_id"].to_numpy()
        self.hybas = df["hybas_l12"].to_numpy()
        self.area = df["upland_skm"].to_numpy()
        del df
        self._np = np
        return self

    @property
    def ready(self):
        return self.ids is not None and len(self.ids) > 0

    def _at(self, rid):
        """Row index of a reach, or -1. Binary search over the sorted id column."""
        i = int(self._np.searchsorted(self.ids, rid))
        return i if i < len(self.ids) and int(self.ids[i]) == rid else -1

    def area_of(self, rid):
        i = self._at(rid)
        return float(self.area[i]) if i >= 0 else 0.0

    def has(self, rid):
        return self._at(rid) >= 0

    def reaches_for_hybas(self, codes):
        """{HYBAS_L12 -> the largest reach in that basin}, for the codes asked about.

        Largest by drainage area, which is the basin's own trunk: a level-12 basin
        holds a handful of reaches and the gauge is on the main one, not on a
        headwater stub that happens to share the code.
        """
        np = self._np
        want = np.array(sorted({int(c) for c in codes}), dtype="int64")
        if not len(want):
            return {}
        pos = np.searchsorted(want, self.hybas)
        pos[pos >= len(want)] = 0
        rows = np.flatnonzero(want[pos] == self.hybas)
        best = {}
        for i in rows:
            code, a = int(self.hybas[i]), float(self.area[i])
            if code not in best or a > best[code][1]:
                best[code] = (int(self.ids[i]), a)
        return {c: r for c, (r, _a) in best.items()}


# ---- gauge -> reach --------------------------------------------------------

def resolve_gauges(records, net, crosswalk_path=None):
    """The reach each flagged gauge sits on: {forecast_uid -> reach id}.

    Two routes, tried in that order. The id join is exact and free; the snap is a
    fallback for the agency-coded gauges (CWC, ANA, BWDB, MOI) whose ids are not
    HydroBASINS codes at all and so have nothing to join on.
    """
    by_code, loose = {}, []
    for r in records:
        gid = str(r.get("native_id") or "").strip()
        m = GAUGE_HYBAS.match(gid)
        if m:
            by_code.setdefault(int(m.group(1)), []).append(r)
        else:
            loose.append(r)

    out = {}
    if by_code:
        for code, rid in net.reaches_for_hybas(by_code).items():
            for r in by_code[code]:
                out[r["forecast_uid"]] = rid
    unjoined = [r for rs in by_code.values() for r in rs
                if r["forecast_uid"] not in out]

    snapped = {}
    todo = loose + unjoined
    if todo and crosswalk_path:
        snapped = snap_to_reach(todo, net, crosswalk_path)
        out.update(snapped)
    return out, {"by_id": len(out) - len(snapped), "snapped": len(snapped),
                 "unresolved": len(records) - len(out)}


def snap_to_reach(records, net, crosswalk_path, rings=None):
    """Nearest reach to each gauge, by H3 ring, read off the crosswalk.

    The crosswalk already says which cells every reach passes through, so the
    nearest reach to a point is found by widening rings around the point's own cell
    until one of them holds a reach — no geometry, no spatial index, one streamed
    pass. Ties inside a ring go to the larger river, same reasoning as the id join.
    """
    try:
        import h3
    except ImportError:
        return {}
    rings = C.HYDRORIVERS_SNAP_RINGS if rings is None else rings

    ring_of = {}          # cell -> [(uid, ring index)]
    for r in records:
        try:
            home = h3.latlng_to_cell(float(r["lat"]), float(r["lon"]), C.BASE_RES)
        except (TypeError, ValueError, KeyError):
            continue
        for cell in h3.grid_disk(home, rings):
            ring_of.setdefault(cell, []).append(
                (r["forecast_uid"], h3.grid_distance(home, cell)))
    if not ring_of:
        return {}

    best = {}
    with open(crosswalk_path, newline="", encoding="utf-8-sig") as f:
        rd = csv.reader(f)
        next(rd, None)
        for row in rd:
            hits = ring_of.get(row[0])
            if not hits:
                continue
            try:
                rid = int(float(row[1]))
            except (ValueError, IndexError):
                continue
            area = net.area_of(rid)
            for uid, k in hits:
                cand = (k, -area)
                if uid not in best or cand < best[uid][0]:
                    best[uid] = (cand, rid)
    return {uid: rid for uid, (_c, rid) in best.items()}


# ---- the walk --------------------------------------------------------------

def claim_reaches(flagged, net):
    """Which reaches a warned gauge flags, and which gauge owns each: its own.

    `flagged` maps a reach id to the source record on it, carrying at least
    `rank` (severity rank, higher is worse) and `forecast_uid`. A reach holding
    more than one warned gauge keeps the worse of them — that choice is made by
    the caller, before this is reached, which is why the claim here is one line.

    Returns (claims, stats). `claims` maps a claimed reach id to
    (sort key, source reach id) — the source being the reach itself. The key is
    what the cell-level collapse sorts on, so the two stages cannot disagree about
    which gauge wins a hexagon two reaches share: worse severity first, then the
    larger drainage area.

    This is a pass-through now, and kept as its own function anyway. It is where
    the extent rule lives, and the rule has already changed once — a walk sat here
    before, and whatever replaces it needs the same two contracts: the key shape
    that filler_cells sorts on, and one entry per claimed reach.
    """
    claims = {rid: ((-flagged[rid]["rank"], -net.area_of(rid)), rid) for rid in flagged}
    return claims, {"claimed_reaches": len(claims)}


def filler_cells(claims, crosswalk_path, skip=()):
    """{cell -> source reach} for every cell the filled reaches pass through.

    One source per cell, not one per reach: a hexagon crossed by two claimed
    reaches is the same overlap `claim_reaches` already ruled on, so it is settled
    by the same key rather than listing the cell twice. Cells in `skip` — the ones
    already holding a real gauge — are left to that gauge.
    """
    skip = set(skip)
    best = {}
    with open(crosswalk_path, newline="", encoding="utf-8-sig") as f:
        rd = csv.reader(f)
        next(rd, None)
        for row in rd:
            try:
                rid = int(float(row[1]))
            except (ValueError, IndexError):
                continue
            claim = claims.get(rid)
            if claim is None:
                continue
            cell = row[0].strip()
            if not cell or cell in skip:
                continue
            key, src = claim
            cur = best.get(cell)
            if cur is None or key < cur[0]:
                best[cell] = (key, src)
    return {cell: src for cell, (_k, src) in best.items()}


# ---- the whole thing, as step 2 uses it ------------------------------------

def fill(records, gauge_cells, net=None, crosswalk_path=None, verbose=True):
    """Flood Hub's reach fill: new forecast records and their cell pairs.

    `records` are the day's flagged Flood Hub forecasts; `gauge_cells` the cells
    they already occupy as points. Returns (records, pairs, stats) and is a no-op
    returning empty lists whenever a table is missing — a fill that cannot run must
    leave the gauges exactly as they were, never drop them.
    """
    net = (net or Network()).load()
    crosswalk_path = crosswalk_path or C.HYDRORIVERS_CROSSWALK_CSV
    blank = ([], [], {})
    if not net.ready:
        if verbose:
            print(f"  note: no HydroRIVERS topology at {net.path}; "
                  "Flood Hub gauges left unfilled "
                  "(build it with static/build_hydrorivers.py --topology)",
                  file=sys.stderr)
        return blank
    if not os.path.exists(crosswalk_path):
        if verbose:
            print(f"  note: no HydroRIVERS crosswalk at {crosswalk_path}; "
                  "Flood Hub gauges left unfilled "
                  "(build it with static/build_hydrorivers.py --crosswalk)",
                  file=sys.stderr)
        return blank

    on_reach, res_stats = resolve_gauges(records, net, crosswalk_path)
    if not on_reach:
        return [], [], res_stats

    # One source per reach. Two gauges can resolve to the same reach — the id join
    # takes a basin's trunk, and a basin can hold two of them — and the claim below
    # must then be the worse of the two, not whichever was read first.
    by_uid = {r["forecast_uid"]: r for r in records}
    flagged = {}
    for uid, rid in on_reach.items():
        r = by_uid[uid]
        rank = C.SEVERITY_RANK.get(str(r.get("severity", "")).lower(), 0)
        cur = flagged.get(rid)
        if cur is None or rank > cur["rank"]:
            flagged[rid] = {"rank": rank, "forecast_uid": uid, "record": r}

    claims, claim_stats = claim_reaches(flagged, net)
    cells = filler_cells(claims, crosswalk_path, skip=gauge_cells) if claims else {}

    # One synthetic forecast per SOURCE gauge, not per cell: the reach is one
    # statement about one flood, and a record per cell would repeat every attribute
    # and inflate every forecast count built on the table.
    out_records, pairs, used = [], [], {}
    for cell, src in cells.items():
        uid = flagged[src]["forecast_uid"]
        fill_uid = used.get(uid)
        if fill_uid is None:
            src_rec = flagged[src]["record"]
            gid = str(src_rec.get("native_id") or "").strip()
            new = dict(src_rec)
            fill_uid = f"flood_hub:{C.FLOOD_HUB_FILL_PREFIX.strip()}:{gid}"
            new["forecast_uid"] = fill_uid
            new["native_id"] = f"{C.FLOOD_HUB_FILL_PREFIX}{gid}"
            out_records.append(new)
            used[uid] = fill_uid
        pairs.append((cell, fill_uid))

    stats = dict(res_stats)
    stats.update(claim_stats)
    stats["filled_cells"] = len(pairs)
    stats["filled_forecasts"] = len(out_records)
    return out_records, pairs, stats
