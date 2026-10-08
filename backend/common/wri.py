"""The Weighted Risk Index: three categories kept separate, one score out.

A map that paints one colour per model answers "who is forecasting here". It does
not answer "where should someone be sent first", because that question compares a
corroborated danger over a city against an uncorroborated extreme over a floodplain
and the per-model view has no way to hold both at once. This is the one number that
does, and the three numbers it is made of, which are delivered alongside it so the
score is never a black box.

THE THREE CATEGORIES

  concurrence   how many independent sources say this cell is flooding, from the
                models that genuinely share one BASE cell (step 3's co_models, not
                a coarse cell's own model list), plus a flash footprint over the
                same ground.

  severity      the worst rung any of them reached, on the shared 2 / 5 / 20-year
                return-period ladder.

  impact        what is underneath, from the static impact table: population,
                buildings, roads, farmland, railways.

WHY THEY ARE NOT THREE EQUAL WEIGHTS

Measured over one release at res 6 (32,426 cells):

    concurrence       5 distinct values    31,023 cells (95.7%) at zero
    severity          3 distinct values         0 at zero
    impact       21,787 distinct values     7,620 at zero

They are not three comparable quantities. One is near-constant, one has three
levels, one is effectively continuous. Weighted as peers in a sum, the continuous
one does all the ordering whatever the stated priorities say — and the compensatory
arithmetic lets a well-corroborated warning over a city outrank an extreme. At the
priority weights .50 / .30 / .20 that happens to 3,314 of 3,452 extreme cells.

Four combination rules were measured against each other on the same categories and
the same weights. Additive and weighted-geometric agreed on 48 of the top 50 cells
and both inverted the ladder; rank aggregation produced a clean top 50 and still
inverted 3,184 extreme cells in the middle, because ranking flattens severity's
three levels into three plateaus. Only anchoring on severity held the ladder:

    WRI = severity x (1 + a.concurrence) x (1 + b.impact)

a and b are not chosen; they are derived from the ladder they have to respect. The
headroom is the smallest ratio between adjacent rungs — extreme/danger = 1.5 — and
the two caps multiply back to exactly that, so the three rungs tile the scale end to
end:

    warning   0.222 .. 0.333
    danger    0.444 .. 0.667
    extreme   0.667 .. 1.000

A fully corroborated danger over a city lands exactly where a bare extreme starts
and never above it. Severity owns the band; concurrence and impact decide the
position inside it. That is a property of the FORM — no choice of weights makes a
compensatory sum safe, and chosen caps drift out of step with the ladder the moment
the rungs change. Measured at an earlier hand-picked 0.42/0.33, 2,098 extreme cells
had already fallen below the best danger cell.

The split between the two caps, the weights and the rungs live in config.py, so the
index can be retuned without editing this file and a release records what it was
drawn with. `inversions` re-checks the tiling on every run rather than trusting it.
"""

import bisect

from . import config as C


def concurrence(row, has_flash):
    """How many sources agree here, 0..1.

    Counted from `co_models`, which step 3 fills only with model sets that really
    do share one res-6 cell. A coarse cell's own `models` column lists everything
    beneath it and would report agreement between models a hundred kilometres
    apart — the distinction the whole co_models mechanism exists for.
    """
    sets = [s for s in (row.get("co_models") or "").split("|") if s]
    agree = max((len(s.split("+")) for s in sets), default=1)
    rung = C.WRI_CONCURRENCE.get(min(agree, max(C.WRI_CONCURRENCE)), 0.0)
    if has_flash:
        rung += C.WRI_FLASH_BONUS
    return min(rung, 1.0)


def severity(row):
    return C.WRI_SEVERITY.get((row.get("severity") or "").lower(), 0.0)


def impact_ranker(rows):
    """Percentile rank of each impact component, within one resolution.

    Built per resolution rather than across the whole release: a res-3 cell holds
    the population of everything beneath it, so ranking it against res-6 cells
    would put every coarse cell at the top of the scale by construction.

    Blank is not zero. The impact join writes blank where no data reached the cell
    at all, and ranking that as zero would state "nobody lives here" with the
    confidence of a measurement — so a blank row gets no impact score and no impact
    factor, rather than the worst possible one.
    """
    scales = {}
    for f in C.WRI_IMPACT_WEIGHTS:
        vals = sorted(_f(r.get(f)) for r in rows if _f(r.get(f)) is not None)
        scales[f] = vals

    def rank(row):
        total, seen = 0.0, False
        for f, w in C.WRI_IMPACT_WEIGHTS.items():
            v = _f(row.get(f))
            if v is None:
                continue
            seen = True
            vals = scales[f]
            n = len(vals) - 1
            total += w * (bisect.bisect_left(vals, v) / n if n > 0 else 0.0)
        return total if seen else None

    return rank


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# The largest the product can be, used to put the delivered score on 0..1. Stated
# from the caps rather than from the day's data, so the same score means the same
# thing in every release — a quiet day has to be allowed to look quiet.
def ceiling():
    return (1 + C.WRI_CAP_CONCURRENCE) * (1 + C.WRI_CAP_IMPACT)


def score(sev, conc, imp):
    """The anchored product, scaled to 0..1. `imp` may be None (unknown impact)."""
    out = sev * (1 + C.WRI_CAP_CONCURRENCE * conc)
    if imp is not None:
        out *= (1 + C.WRI_CAP_IMPACT * imp)
    return out / ceiling()


def add(rows, flash_cells=()):
    """Score every cell row in place, one resolution at a time.

    `rows` are the delivered cell rows as dicts; `flash_cells` the res-6 cells a
    flash footprint covers (a coarse cell counts as covered when any descendant
    is). Returns a per-resolution tally for the run log.
    """
    flash = set(flash_cells)
    by_res = {}
    for r in rows:
        by_res.setdefault(int(r["res"]), []).append(r)

    tally = {}
    for res, group in sorted(by_res.items()):
        rank = impact_ranker(group)
        for r in group:
            sev = severity(r)
            conc = concurrence(r, r["h3_id"] in flash)
            imp = rank(r)
            r["wri_severity"] = round(sev, 4)
            r["wri_concurrence"] = round(conc, 4)
            r["wri_impact"] = "" if imp is None else round(imp, 4)
            r["wri"] = round(score(sev, conc, imp), 4)
        vals = sorted(r["wri"] for r in group)
        tally[res] = {"cells": len(group), "min": vals[0], "max": vals[-1],
                      "median": vals[len(vals) // 2],
                      "with_agreement": sum(1 for r in group if r["wri_concurrence"] > 0)}
    return tally


def inversions(rows):
    """How many cells of a worse rung score below the best cell of a milder one.

    The guard this whole design exists for. Anything other than zero means the
    modifiers have outgrown the ladder and the caps need lowering — so it is
    measured on every run rather than argued about once.
    """
    best = {}
    for r in rows:
        s = (r.get("severity") or "").lower()
        if s in C.WRI_SEVERITY:
            best[s] = max(best.get(s, 0.0), r["wri"])
    out = {}
    order = sorted(C.WRI_SEVERITY, key=lambda k: C.WRI_SEVERITY[k])
    for i, worse in enumerate(order):
        for milder in order[:i]:
            n = sum(1 for r in rows
                    if (r.get("severity") or "").lower() == worse
                    and r["wri"] < best.get(milder, 0.0))
            if n:
                out[f"{worse} under {milder}"] = n
    return out
