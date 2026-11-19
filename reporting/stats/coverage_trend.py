#!/usr/bin/env python3
"""How much of the map each model covers, release by release.

    python3 reporting/stats/coverage_trend.py
    python3 reporting/stats/coverage_trend.py --since 2026-11-06
    python3 reporting/stats/coverage_trend.py --scope all

Writes reporting/out/figures/coverage-trend.pdf and the series behind it as
reporting/out/tables/coverage-trend.csv, so the picture and its numbers never
drift apart. Neither is committed -- see reporting/stats/_emit.py. PDF rather than
PNG: the figure is lines and text, which stay sharp at any size as vector and
go soft the moment they are rasterised for print.

WHAT IT PLOTS

Res-6 cells carrying at least one forecast from that model, on day 0 -- what
the map actually colours when it opens. Cells rather than forecasts, because a
GEOGLOWS reach crosses several hexagons while a GloFAS pixel sits in one, so
forecast counts compare the models' reporting habits and cell counts compare
their coverage. --scope all counts a cell on any forecast day instead.

The y axis is logarithmic. Flood Hub covers single or double digits of cells
while the other two cover thousands; on a linear axis its line would lie flat
on the x axis and the only readable thing would be GEOGLOWS.

RELEASES BEFORE 13 NOV ARE NOT COMPARABLE and the figure says so where they
appear. Until then step 2 kept one forecast per reach rather than one per reach
per day, so a reach flooding all week reached the map on its LAST day only and
roughly 90% of GEOGLOWS's day-0 coverage was missing. The detection is from the
data, not the date: in a collapsed release every GEOGLOWS reach appears exactly
once, which cannot happen when a week-long flood is reported honestly.
"""

import argparse
import json
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "backend"))

import _emit  # noqa: E402  -- in this folder: a figure that ships a table

import matplotlib
matplotlib.use("Agg")                     # no display here, and none wanted
import matplotlib.pyplot as plt           # noqa: E402
import matplotlib.dates as mdates         # noqa: E402
from datetime import date                 # noqa: E402

from common import config as C            # noqa: E402

FORECASTER_SINCE = "2026-11-06"
FIXED_SINCE = "2026-11-13"
RES = "6"
# The document's model palette, matching main.tex's modelgeo / modelfh /
# modelglofas and the forecast-timeline diagram: one colour per model
# everywhere, so the key is learned once.
MODELS = [("geoglows",  "GEOGLOWS",  "#EA580C"),    # orange
          ("flood_hub", "Flood Hub", "#2563EB"),    # blue
          ("glofas",    "GloFAS",    "#15803D")]    # green


def collapsed(release):
    """True when this release predates the per-day fix in step 2."""
    with open(os.path.join(C.processed_dir(release),
                           C.PROCESSED["forecasts"]), encoding="utf-8") as fh:
        fc = json.load(fh)
    i = {c: n for n, c in enumerate(fc["columns"])}
    ids = [r[i["native_id"]] for r in fc["rows"] if r[i["model"]] == "geoglows"]
    return bool(ids) and len(ids) == len(set(ids))


def cells(release, scope):
    """{model: number of res-6 cells it covers} for one release."""
    base = C.processed_dir(release)
    days = range(C.HORIZON_DAYS) if scope == "all" else [0]
    out = {m: set() for m, _l, _c in MODELS}
    for d in days:
        path = os.path.join(base, C.PROCESSED["day"].format(day=d))
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as fh:
            tbl = json.load(fh)["cells"]
        j = {c: n for n, c in enumerate(tbl["columns"])}
        for row in tbl["rows"]:
            if row[j["res"]] != RES:
                continue
            for m in row[j["models"]].split("+"):
                if m in out:
                    out[m].add(row[j["h3_id"]])
    return {m: len(v) for m, v in out.items()}


def plot(series, releases, suspect, scope, out):
    xs = [date.fromisoformat(r) for r in releases]
    fig, ax = plt.subplots(figsize=(9.2, 4.6))

    # A log axis cannot draw 0, and Flood Hub reaches 0. Park those on a floor
    # below every real value, as hollow markers, so a genuine "nothing today"
    # reads as nothing rather than as a line that quietly leaves the figure.
    real = [v for r in releases for v in series[r].values() if v > 0]
    floor = max(1.0, min(real) / 3.0)

    if suspect:
        edge = date.fromisoformat(FIXED_SINCE)
        ax.axvspan(xs[0], edge, color="#94A3B8", alpha=0.14, lw=0, zorder=0)
        ax.axvline(edge, color="#64748B", lw=1.0, ls=(0, (4, 3)), zorder=1)
        ax.annotate("GEOGLOWS under-reported before the per-day fix",
                    xy=(xs[0], 1.035), xycoords=("data", "axes fraction"),
                    xytext=(4, 0), textcoords="offset points",
                    fontsize=8.5, color="#64748B", va="bottom")

    ends = []
    for key, label, colour in MODELS:
        raw = [series[r][key] for r in releases]
        ys = [v if v > 0 else floor for v in raw]
        ax.plot(xs, ys, lw=2.0, color=colour, label=label, zorder=3)
        solid = [(x, y) for x, y, v in zip(xs, ys, raw) if v > 0]
        zero = [(x, y) for x, y, v in zip(xs, ys, raw) if v == 0]
        if solid:
            ax.plot(*zip(*solid), ls="none", marker="o", ms=4.2, color=colour, zorder=4)
        if zero:
            ax.plot(*zip(*zero), ls="none", marker="o", ms=5.6, mfc="white",
                    mec=colour, mew=1.4, zorder=4)
            ax.annotate("0", xy=zero[-1], xytext=(0, -13),
                        textcoords="offset points", fontsize=8.5,
                        color=colour, ha="center")
        ends.append((ys[-1], raw[-1], colour))

    # Two models can finish within a hair of each other; nudge the labels apart
    # rather than printing one on top of the other.
    ends.sort()
    offs, last = {}, None
    for y, v, c in ends:
        dy = -3
        if last is not None and y / last < 1.35:
            dy = offs[last] + 12
        offs[y] = dy
        last = y
        if v == 0:
            continue        # the hollow marker already carries its own "0"
        ax.annotate(f"{v:,}", xy=(xs[-1], y), xytext=(8, dy),
                    textcoords="offset points", fontsize=9.5, color=c,
                    fontweight="bold", va="center")

    ax.set_yscale("log")
    ax.set_ylim(bottom=floor / 1.6)
    ax.set_ylabel(f"res-6 cells covered  ({'any day' if scope == 'all' else 'day 0'})")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d %b"))
    ax.xaxis.set_major_locator(mdates.DayLocator(interval=max(1, len(xs) // 8)))
    ax.grid(True, which="major", axis="y", color="#CBD5E1", lw=0.6)
    ax.grid(True, which="minor", axis="y", color="#E2E8F0", lw=0.4)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    ax.legend(frameon=False, ncol=3, loc="lower left",
              bbox_to_anchor=(0, 1.08), fontsize=10)
    ax.margins(x=0.10)
    fig.tight_layout()
    fig.savefig(out, bbox_inches="tight", facecolor="white")
    print(f"wrote {out}")


def build(args):
    """The series, as a table. Same shape as the three in reporting/stats, so the
    workbook can carry the numbers behind this figure on a sheet of their own
    -- a line on a log axis shows the trend, but you cannot read a value off
    it, and on a poster someone always asks for the value."""
    every = C.list_releases()
    releases = (every[-args.releases:] if args.releases
                else [r for r in every if r >= args.since])
    if len(releases) < 2:
        sys.exit(f"Need at least two releases to draw a trend; found "
                 f"{len(releases)} on or after {args.since}.")

    series, suspect = {}, []
    for r in releases:
        series[r] = cells(r, args.scope)
        if collapsed(r):
            suspect.append(r)

    note = None
    if suspect:
        note = (f"{len(suspect)} release(s) predate the per-day fix in step 2 "
                f"and under-report GEOGLOWS -- {suspect[0]} to {suspect[-1]}. "
                f"They are shaded in the figure.")
        print("note: " + note, file=sys.stderr)

    scope = "any forecast day" if args.scope == "all" else "day 0"
    rows = [[r] + [f"{series[r][k]:,}" for k, _l, _c in MODELS]
            + ["yes" if r in suspect else "no"]
            for r in releases]
    table = _emit.Table(
        name="coverage-trend",
        title="Map coverage by release",
        subtitle=f"res-6 cells covered, {scope}   "
                 f"{releases[0]} to {releases[-1]} ({len(releases)} releases)",
        header=["Release"] + [l for _k, l, _c in MODELS] + ["Pre-fix"],
        rows=rows, note=note, sheet="Coverage trend",
        widths=[22, 22, 22, 22, 18])
    table.series = series
    table.releases = releases
    table.suspect = suspect
    return table


def write_tex(path, table, scope):
    """The float that places the figure in the manual, with its caption.

    Generated rather than hand-written, for the same reason the tables' LaTeX
    is: the caption states the window and whether any release in it predates
    the step-2 fix, and both of those change when the data does. A caption
    typed once is a caption that lies the week after.
    """
    esc = lambda t: t.replace("%", r"\%").replace("–", "--").replace("—", "---")
    first, last = table.releases[0], table.releases[-1]
    shade = ""
    if table.suspect:
        shade = (r" The shaded band on the left is the releases that predate "
                 r"the per-day fix in step~2, when a GEOGLOWS reach reached "
                 r"the map on the furthest-out day it flooded and on no other; "
                 r"they are drawn because the size of the correction is the "
                 r"point, and they are not comparable with what follows.")
    tex = f"""% Generated by reporting/stats/coverage_trend.py -- do not hand-edit.
% Source: {len(table.releases)} releases, {first} to {last}, scope {scope}.
\\begin{{figure}}[htbp]
\\centering
\\includegraphics[width=0.9\\textwidth]{{coverage-trend.pdf}}
\\caption[Map coverage by release]{{%
  \\textbf{{How much of the map each model covers, release by release.}}
  Resolution-6 cells carrying at least one forecast from that model on
  {'any forecast day' if scope == 'all' else 'day~0'}, across
  {len(table.releases)} releases ({esc(first)} to {esc(last)}).
  Cells rather than forecasts: a GEOGLOWS reach crosses several hexagons
  while a GloFAS pixel sits in one, so a forecast count compares the
  models' reporting habits and a cell count compares their coverage.
  The vertical axis is logarithmic --- Flood Hub covers single or double
  digits of cells while the other two cover thousands, and on a linear
  axis its line would lie flat against the bottom.{shade}}}
\\label{{fig:coverage-trend}}
\\end{{figure}}
"""
    return _emit.write_text(path, tex)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--since", metavar="YYYY-MM-DD", default=FORECASTER_SINCE)
    ap.add_argument("-n", "--releases", type=int, default=None)
    ap.add_argument("--scope", choices=["day0", "all"], default="day0")
    ap.add_argument("--out", default=None,
                    help="figure path; the extension picks the format "
                         "(default reporting/out/figures/coverage-trend.pdf)")
    ap.add_argument("--csv", metavar="PATH", default=None,
                    help="where to write the CSV table (default "
                         "reporting/out/tables/coverage-trend.csv)")
    ap.add_argument("--tex", metavar="PATH", default=None,
                    help="where to write the LaTeX float that places the "
                         "figure (default "
                         "reporting/out/figures/coverage-trend.tex)")
    args = ap.parse_args(argv)

    table = build(args)
    _emit.show(table)
    _emit.write_csv(table, args.csv)
    plot(table.series, table.releases, table.suspect, args.scope,
         args.out or _emit.out(os.path.join(_emit.FIGURES,
                                            "coverage-trend.pdf")))
    write_tex(args.tex or _emit.out(os.path.join(_emit.FIGURES,
                                                 "coverage-trend.tex")),
              table, args.scope)


if __name__ == "__main__":
    main()
