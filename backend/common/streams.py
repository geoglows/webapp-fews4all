"""River network topology, and the rules for a cell holding several reaches.

A res-6 hexagon is about 36 km across the diagonal and a GEOGLOWS reach has a
median footprint of ONE cell, so a hexagon routinely holds more than one flagged
reach. Measured over a release, that is 575 of 10,282 cells, and the reasons are
not the same reason:

    separate channels sharing a hexagon   36%
    confluence, two rivers joining        22%
    same channel, a few reaches apart     10%
    same channel, consecutive              3%
    (the rest could not be resolved from the tiles)

So there is no single "deduplicate" that is right. Three rules, one per case:

  1. Reaches that MEET — at a junction inside the cell, or anywhere downstream —
     collapse to the one with the greater drainage area. That is the trunk; the
     other is a tributary joining it. Drainage area beats the alternatives: it
     decides every pair, where Strahler order ties on 18% of confluences, and it
     agrees with the larger forecast flow on 91% of pairs while staying the same
     from day to day.

  2. Reaches that are the SAME channel, one downstream of the other, collapse to
     the most upstream of them.

  3. Reaches that NEVER meet — different outlets, so different basins — are left
     alone. No statement about the hexagon can be true of both: there is no shared
     junction and no combined flow. Rare (1.5%) and concentrated on coastlines,
     where neighbouring rivers reach the sea a few kilometres apart.

Rule 2 is applied first, within each chain, then rule 1 across what survives. The
order only matters where a cell holds a chain AND a separate tributary, which is a
handful of cells; it is stated here because it is a choice, not a consequence.
"""

import csv
import os

from . import config as C


class Network:
    """Reach topology, loaded once per run. Absent reaches answer None."""

    def __init__(self, path=None):
        self.next = {}
        self.outlet = {}
        self.area = {}
        self.loaded = False
        self.path = path or C.STREAM_ATTRS_CSV

    def load(self):
        if self.loaded:
            return self
        self.loaded = True
        if not os.path.exists(self.path):
            return self
        with open(self.path, newline="", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                rid = _int(row.get("river_id"))
                if rid is None:
                    continue
                nxt = _int(row.get("next_id"))
                if nxt is not None:
                    self.next[rid] = nxt          # blank means terminal; stays absent
                out = _int(row.get("outlet_id"))
                if out is not None:
                    self.outlet[rid] = out
                a = _float(row.get("ds_cont_area"))
                if a is not None:
                    self.area[rid] = a
        return self

    @property
    def ready(self):
        return bool(self.area)

    def downstream(self, rid, hops=None):
        """The reaches below `rid`, nearest first, to a bounded depth."""
        out, cur = [], rid
        for _ in range(hops or C.STREAM_CHAIN_HOPS):
            cur = self.next.get(cur)
            if cur is None or cur in out:         # terminal, or a loop in the data
                break
            out.append(cur)
        return out

    def same_channel(self, a, b):
        """True when one reach lies on the other's own downstream path."""
        return b in self.downstream(a) or a in self.downstream(b)

    def converge(self, a, b):
        """True when both eventually reach the same outlet.

        Outlet equality, not `next_id` equality. A terminal reach has no
        downstream at all, so comparing next ids would make every pair of river
        MOUTHS look like a junction — two false confluences in 127 when measured.
        """
        oa, ob = self.outlet.get(a), self.outlet.get(b)
        return oa is not None and oa == ob


def _int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def resolve_cell(reach_ids, net):
    """The reaches a cell should report, from the ones flagged inside it.

    Returns (kept, dropped, reason). `reason` names the rule that fired, for the
    run's own accounting — a silent reduction is the kind that goes wrong unnoticed.
    """
    ids = list(dict.fromkeys(reach_ids))
    if len(ids) < 2 or not net.ready:
        return ids, [], "single" if len(ids) < 2 else "no topology"

    # Rule 3 first, as a partition: reaches that never meet are different stories
    # and are resolved independently of each other.
    groups, ungrouped = [], []
    for rid in ids:
        if net.outlet.get(rid) is None:
            ungrouped.append(rid)                 # unknown to the table; never merged
            continue
        for g in groups:
            if net.converge(rid, g[0]):
                g.append(rid)
                break
        else:
            groups.append([rid])

    kept, dropped, rules = [], [], set()
    for g in groups:
        if len(g) > 1:
            rules.add("basin")
        survivors = _collapse_chains(g, net, dropped, rules)
        # Rule 1: whatever still stands in one basin meets downstream, so the
        # greater drainage area is the trunk and carries the cell.
        if len(survivors) > 1:
            survivors.sort(key=lambda r: net.area.get(r, 0), reverse=True)
            dropped.extend(survivors[1:])
            rules.add("confluence")
            survivors = survivors[:1]
        kept.extend(survivors)

    kept.extend(ungrouped)
    if len(groups) > 1:
        rules.add("separate basins")
    return kept, dropped, "+".join(sorted(rules)) or "kept"


def _collapse_chains(group, net, dropped, rules):
    """Rule 2: one channel reported twice keeps only its most upstream reach."""
    survivors = list(group)
    changed = True
    while changed and len(survivors) > 1:
        changed = False
        for i, a in enumerate(survivors):
            for b in survivors[i + 1:]:
                if not net.same_channel(a, b):
                    continue
                # Upstream is the smaller catchment: area only grows downstream.
                lower = b if net.area.get(a, 0) <= net.area.get(b, 0) else a
                survivors.remove(lower)
                dropped.append(lower)
                rules.add("same channel")
                changed = True
                break
            if changed:
                break
    return survivors
