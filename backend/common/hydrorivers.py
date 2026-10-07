"""Flood Hub's own network — HydroRIVERS v10 — and the rule for filling between gauges.

Flood Hub publishes gauges, not reaches. On the map that is a scatter of single
hexagons: the Brahmaputra warned at four places reads as four unrelated dots, the
river between them unflagged even though the same flood is in it. GEOGLOWS does
not have this problem because it forecasts a reach, and a reach joins through the
crosswalk to every cell it crosses.

Flood Hub is built on HydroSHEDS, so HydroRIVERS v10 IS its network — and its
gauge ids say so literally. A Flood Hub `gaugeId` is `hybas_<HYBAS_L12>`, and
HYBAS_L12 is a column of the HydroRIVERS shapefile. So the join is a string
match, not a spatial guess, for the gauges that carry such an id (67 of 99 in the
release this was built against).

THE RULE, stated as the three choices it rests on:

  direction     Downstream only. A warning is about water that has arrived, and
                the water goes one way. Walking upstream from a warned gauge would
                flag catchment that may well be dry.

  extent        From one flagged gauge to the NEXT flagged gauge, and no further.
                A walk that runs to the sea without meeting another warning is
                thrown away. Two warnings at the top of the Mississippi would
                otherwise flag the river to the Gulf on the strength of nothing
                observed in between.

  attribution   The UPSTREAM gauge owns the span. It is the one that observed the
                flood the filled cells are inferred from, so those cells carry its
                severity and its times, and they name it rather than pretending to
                be a gauge of their own: `from <gaugeId>`.

Downstream needs no branch rule — `next_down` is a single edge, so following it IS
staying on the main stem. Branching only exists going upstream, which is the
direction this never goes.

Where two spans overlap — two warned tributaries above one warned confluence share
the river below the junction — the claim with the WORSE severity wins, then the
nearer source, then the larger drainage area. Never the milder one: a cell two
models would call extreme and warning is extreme, and the same has to hold for one
model's own geometry.

Reads two tables from the static tier (static/build_hydrorivers.py builds both):

    hydrorivers_topology.csv    hyriv_id, next_down, hybas_l12, upland_skm, main_riv
    h3_hydrorivers_r6.csv       h3_id, hyriv_id

The topology is 8.5M rows, so it is held as sorted numpy columns and looked up by
binary search rather than as dicts — the dict form is about 2 GB and has OOMed
this machine before. The crosswalk is 14.9M rows and is never held at all: it is
streamed and tested against the few thousand reaches a day's walks touch.
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
        self.next = None         # next_down, 0 = outlet
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
        df = pd.read_csv(self.path,
                         usecols=["hyriv_id", "next_down", "hybas_l12", "upland_skm"],
                         dtype={"hyriv_id": "int64", "next_down": "int64",
                                "hybas_l12": "int64", "upland_skm": "float32"})
        df.sort_values("hyriv_id", inplace=True)
        self.ids = df["hyriv_id"].to_numpy()
        self.next = df["next_down"].to_numpy()
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

    def next_of(self, rid):
        i = self._at(rid)
        if i < 0:
            return None
        nxt = int(self.next[i])
        return None if nxt == 0 else nxt      # 0 is HydroRIVERS' outlet marker

    def area_of(self, rid):
        i = self._at(rid)
        return float(self.area[i]) if i >= 0 else 0.0

    def has(self, rid):
        return self._at(rid) >= 0

    def downstream(self, rid, hops):
        """The reaches below `rid`, nearest first, stopping at an outlet or `hops`."""
        out, seen, cur = [], {rid}, rid
        for _ in range(hops):
            nxt = self.next_of(cur)
            if nxt is None or nxt in seen:     # outlet, unknown, or a loop in the data
                break
            out.append(nxt)
            seen.add(nxt)
            cur = nxt
        return out

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

def fill_spans(flagged, net, max_hops=None):
    """Which reaches lie BETWEEN two warned gauges, and which gauge owns each.

    `flagged` maps a reach id to the source record on it, carrying at least
    `rank` (severity rank, higher is worse) and `forecast_uid`. A reach holding
    more than one warned gauge keeps the worse of them — that choice is made by
    the caller, before this is reached.

    Returns (claims, stats). `claims` maps a filler reach id to
    (sort key, source reach id); the key is what the cell-level collapse sorts on,
    so the two stages cannot disagree about which source wins.
    """
    hops = max_hops or C.HYDRORIVERS_MAX_HOPS
    claims, spans, open_ended = {}, 0, 0
    for src in flagged:
        path = []
        met = False
        for rid in net.downstream(src, hops):
            if rid in flagged:
                met = True
                break
            path.append(rid)
        if not met:
            # Ran to the sea, to the hop limit, or off the end of the table with no
            # second warning to close the span. Left empty on purpose.
            open_ended += 1
            continue
        spans += 1
        key_src = (-flagged[src]["rank"], -net.area_of(src))
        for i, rid in enumerate(path):
            key = (key_src[0], i, key_src[1])
            cur = claims.get(rid)
            if cur is None or key < cur[0]:
                claims[rid] = (key, src)
    return claims, {"spans": spans, "open_ended": open_ended,
                    "filler_reaches": len(claims)}


def filler_cells(claims, crosswalk_path, skip=()):
    """{cell -> source reach} for every cell the filled reaches pass through.

    One source per cell, not one per reach: a cell holding filler from two spans is
    the same overlap `fill_spans` already ruled on, so it is settled the same way
    rather than listing the cell twice. Cells in `skip` — the ones already holding a
    real gauge — are left to that gauge.
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
    """Flood Hub's between-gauge fill: new forecast records and their cell pairs.

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
    # takes a basin's trunk, and a basin can hold two of them — and the span below
    # must then be the worse of the two, not whichever was read first.
    by_uid = {r["forecast_uid"]: r for r in records}
    flagged = {}
    for uid, rid in on_reach.items():
        r = by_uid[uid]
        rank = C.SEVERITY_RANK.get(str(r.get("severity", "")).lower(), 0)
        cur = flagged.get(rid)
        if cur is None or rank > cur["rank"]:
            flagged[rid] = {"rank": rank, "forecast_uid": uid, "record": r}

    claims, walk_stats = fill_spans(flagged, net)
    cells = filler_cells(claims, crosswalk_path, skip=gauge_cells) if claims else {}

    # One synthetic forecast per SOURCE gauge, not per cell: the span is one
    # statement about one flood, and a record per cell would repeat every attribute
    # a few hundred times and inflate every forecast count built on the table.
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
    stats.update(walk_stats)
    stats["filled_cells"] = len(pairs)
    stats["filled_forecasts"] = len(out_records)
    return out_records, pairs, stats
