#!/usr/bin/env python3
"""What the GEOGLOWS climatology artifact is doing, release by release.

    python3 reporting/stats/blob_extent.py
    python3 reporting/stats/blob_extent.py --since 2026-11-13

Every run prints the table and writes reporting/out/tables/blob-extent.csv and
.tex. Nothing it writes is committed: see reporting/stats/_emit.py.

WHAT IT MEASURES

GEOGLOWS over-reports flooding across Finland and Russian Karelia, where its
retrospective record and its forecast climatology do not meet. The region was
blacked out for a time; the blackout is off, so the artifact is on the map and
has to be watched instead of hidden. config.GEOGLOWS_ARTIFACT_BOX is the
extent, and it is the same constant the suppression would use, so the thing
measured here and the thing that could be switched back on cannot drift apart.

Cells, not forecasts. A reach is a line and crosses several hexagons, so a
count of forecasts says how much the model SAID and a count of cells says how
much of the map it COLOURED -- and it is the colouring that makes the artifact
impossible to miss. A cell counts as inside when a GEOGLOWS forecast placed in
it has its own coordinate inside the box.

Day 0 and the whole horizon are reported side by side because they behave
differently: the artifact is thin on the day the map opens and much heavier
further out, so a single figure would hide half of what it does.

Every figure is the low-high range across the releases in the window, because
one number cannot say whether the thing is growing.
"""

import argparse
import json
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "backend"))

from common import config as C  # noqa: E402

import _emit  # noqa: E402  -- shared with the other tables in this folder

FORECASTER_SINCE = "2026-11-13"
SEVERITIES = ["warning", "danger", "extreme"]
RANK = {s: i for i, s in enumerate(SEVERITIES)}
RES = "6"

METRICS = ["Cells flagged", "Share of GEOGLOWS cells",
           "Warning", "Danger", "Extreme"]
HEADER = ["In the artifact extent", "Day 0", "All forecast days"]


def inside(lon, lat, box):
    w, s, e, n = box
    return w <= lon <= e and s <= lat <= n


def one_release(release, box):
    """(day0, alldays) stats for one release.

    Each is {"in": {cell: worst severity}, "all": {cell}} -- the artifact's
    cells and GEOGLOWS's cells everywhere, so a share can be taken.
    """
    base = C.processed_dir(release)
    with open(os.path.join(base, C.PROCESSED["forecasts"]), encoding="utf-8") as fh:
        fc = json.load(fh)
    i = {c: n for n, c in enumerate(fc["columns"])}
    meta = {}                       # uid -> (severity, in_box, day_from, day_to)
    for row in fc["rows"]:
        if row[i["model"]] != "geoglows":
            continue
        sev = row[i["severity"]]
        if sev not in RANK:
            continue
        try:
            lon, lat = float(row[i["lon"]]), float(row[i["lat"]])
        except (TypeError, ValueError):
            continue                # no coordinate cannot be shown to be inside
        try:
            d0 = int(float(row[i["day_from"]]))
            d1 = int(float(row[i["day_to"]]))
        except (TypeError, ValueError):
            continue
        meta[row[i["forecast_uid"]]] = (sev, inside(lon, lat, box), d0, d1)

    with open(os.path.join(base, C.PROCESSED["cell_index"]), encoding="utf-8") as fh:
        ci = json.load(fh)
    k = {c: n for n, c in enumerate(ci["columns"])}

    day0 = {"in": {}, "all": set()}
    alld = {"in": {}, "all": set()}
    for row in ci["rows"]:
        if row[k["res"]] != RES:
            continue
        m = meta.get(row[k["forecast_uid"]])
        if m is None:
            continue
        sev, in_box, d0, d1 = m
        cell = row[k["h3_id"]]
        for bucket, live in ((day0, d0 <= 0 <= d1), (alld, True)):
            if not live:
                continue
            bucket["all"].add(cell)
            if in_box:
                prev = bucket["in"].get(cell)
                if prev is None or RANK[sev] > RANK[prev]:
                    bucket["in"][cell] = sev
    return day0, alld


def column(stats):
    """The five metrics for one scope, as raw numbers."""
    cells = stats["in"]
    n = len(cells)
    out = {"Cells flagged": n,
           "Share of GEOGLOWS cells":
               (n / len(stats["all"])) if stats["all"] else None}
    for s in SEVERITIES:
        out[s.title()] = (sum(1 for v in cells.values() if v == s) / n
                          if n else None)
    return out


def span(values, pct):
    vals = [v for v in values if v is not None]
    if not vals:
        return "—"
    lo, hi = min(vals), max(vals)
    fmt = (lambda v: f"{v:.1%}") if pct else (lambda v: f"{v:,.0f}")
    return fmt(lo) if (hi - lo) < (5e-4 if pct else 0.5) else \
        f"{fmt(lo)} – {fmt(hi)}"


def build_rows(per_release):
    rows = []
    for metric in METRICS:
        pct = metric != "Cells flagged"
        rows.append([metric] + [
            span([per_release[r][scope][metric] for r in per_release], pct)
            for scope in (0, 1)])
    return rows



def write_tex(path, rows, releases, box):
    esc = lambda s: s.replace("%", r"\%").replace("–", "--")
    w, s, e, n = box
    body = "\n".join("  " + " & ".join(esc(c) for c in r) + r" \\" for r in rows)
    tex = f"""% Generated by reporting/stats/blob_extent.py -- do not hand-edit.
\\begin{{table}}[htbp]
\\centering
\\small
\\begin{{tabular}}{{l r r}}
\\toprule
{" & ".join(esc(h) for h in HEADER)} \\\\
\\midrule
{body}
\\midrule
\\multicolumn{{3}}{{l}}{{\\itshape {w:g}--{e:g}\\textdegree E, {s:g}--{n:g}\\textdegree N
  \\quad {releases[0]} to {releases[-1]}}} \\\\
\\bottomrule
\\end{{tabular}}
\\caption[The GEOGLOWS artifact, release by release]{{%
  \\textbf{{What the climatology artifact is doing.}}
  GEOGLOWS cells inside the extent that was once blacked out, as a low--high
  range across {len(releases)} releases. Cells rather than forecasts: a reach
  crosses several hexagons, and it is the colouring that makes the region
  impossible to miss. Day~0 and the whole horizon differ because the artifact
  is thin on the day the map opens and much heavier further out.}}
\\label{{tab:blob-extent}}
\\end{{table}}
"""
    return _emit.write_text(path, tex)


def build(args):
    """Measure, and hand back the finished table."""
    box = getattr(C, "GEOGLOWS_ARTIFACT_BOX", None)
    if not box:
        sys.exit("config.GEOGLOWS_ARTIFACT_BOX is unset; nothing to measure.")

    every = C.list_releases()
    releases = (every[-args.releases:] if args.releases
                else [r for r in every if r >= args.since])
    if not releases:
        sys.exit(f"No releases on or after {args.since}; newest is {every[-1]}.")

    per_release = {}
    for r in releases:
        d0, al = one_release(r, box)
        per_release[r] = {0: column(d0), 1: column(al)}
    rows = build_rows(per_release)
    w, s, e, n = box

    # The per-release cell counts. The summary is a low-high range over these,
    # and a range cannot say whether the thing is growing or just noisy -- the
    # release-by-release column can, so it goes in the workbook.
    detail = ("Artifact by release",
              ["Release", "Day 0 cells", "All forecast days"],
              [[r, per_release[r][0]["Cells flagged"],
                per_release[r][1]["Cells flagged"]] for r in releases])

    table = _emit.Table(
        name="blob-extent",
        title="What the GEOGLOWS climatology artifact is doing",
        subtitle=f"{w:g}–{e:g}°E, {s:g}–{n:g}°N   "
                 f"{releases[0]} to {releases[-1]} ({len(releases)} releases)",
        header=HEADER, rows=rows, sheet="Artifact extent",
        detail=detail, widths=[34, 26, 26])
    table.releases = releases
    table.box = box
    table.per_release = per_release
    return table


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--since", metavar="YYYY-MM-DD", default=FORECASTER_SINCE)
    ap.add_argument("-n", "--releases", type=int, default=None,
                    help="use the N most recent releases instead of --since")
    ap.add_argument("--tex", metavar="PATH", default=None,
                    help="where to write the LaTeX table (default "
                         "reporting/out/tables/blob-extent.tex)")
    ap.add_argument("--csv", metavar="PATH", default=None,
                    help="where to write the CSV table (default "
                         "reporting/out/tables/blob-extent.csv)")
    args = ap.parse_args(argv)

    table = build(args)
    _emit.show(table)
    print("per release:")
    for r in table.releases:
        print(f"   {r}  day 0: {table.per_release[r][0]['Cells flagged']:>6,} "
              f"cell(s)   all days: "
              f"{table.per_release[r][1]['Cells flagged']:>6,}")
    print()
    _emit.write_csv(table, args.csv)
    write_tex(args.tex or _emit.tex_path(table.name),
              table.rows, table.releases, table.box)


if __name__ == "__main__":
    main()
