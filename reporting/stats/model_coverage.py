#!/usr/bin/env python3
"""What each river model contributes, measured from the delivered releases.

Run it and it re-derives the table from whatever is on disk, so the numbers in
the manual and on a poster can be refreshed rather than retyped.

    python3 reporting/stats/model_coverage.py
    python3 reporting/stats/model_coverage.py --since 2026-11-13

Every run prints the table and writes reporting/out/tables/model-coverage.{csv,tex}.
Nothing it writes is committed: see reporting/stats/_emit.py. For the spreadsheet,
run reporting/stats/workbook.py, which puts this and every other table in one book.

By default it uses every release from FORECASTER_SINCE onward, so the window
widens on its own as the pipeline runs. Pass --since to move the floor, or -n
to take a fixed number of the most recent releases instead.

WHAT IT COUNTS, and why those choices

Day 0 only. GloFAS reports in lead BANDS and an undated Flood Hub gauge spans
the whole horizon, so a forecast from either is live on many days at once.
Counting across the horizon would tally one GloFAS alert up to sixteen times
while GEOGLOWS -- which names single days -- is counted once each, and the
ranking you got back would be an artefact of how each model reports rather
than anything about the flooding. Day 0 is the one day every model speaks for
on equal terms. Pass --day to look at another, knowing that caveat.

Ranges, not averages. Five releases is too few for a mean to say much, and the
low--high spread answers the more useful question: is this pattern steady?
GEOGLOWS holds inside four points on every measure; Flood Hub swings between a
third and two thirds because it issues single-digit alert counts, which is a
sample-size artefact and not volatility. --average prints means instead.

Shares describe who issues the alerts, not who is right. Nothing here is
verified against observed floods.
"""

import argparse
import json
import os
import statistics
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "backend"))

from common import config as C  # noqa: E402

import _emit  # noqa: E402  -- shared with the other tables in this folder

# Releases before this one predate the forecast timeline, so their day spans
# were not assigned the way the ones here are and the two are not comparable.
# Counting from a date rather than a release count means the window widens by
# itself as the pipeline runs, instead of silently sliding off the start.
# 13 Nov is the first release built after the day-collapse bug in step 2 was
# fixed; anything earlier carries ~10% of GEOGLOWS's day-0 forecasts and is not
# comparable. It is also the first release with the GEOGLOWS blackout lifted.
FORECASTER_SINCE = "2026-11-13"

SEVERITIES = ["warning", "danger", "extreme"]
# The side panel's names, in the order the panel lists them.
LABELS = {"geoglows": "GEOGLOWS", "flood_hub": "Flood Hub", "glofas": "GloFAS"}
# Flash is deliberately absent: it is a polygon overlay rather than a cell
# model, and its 24-hour window makes a share-of-the-day figure meaningless.


def day_zero_counts(release, day):
    """Alerts issued by each river model for `day` of one release.

    Returns {model_label: {severity: count}}. A forecast counts when the day
    falls inside its published span -- which for GloFAS is a lead band and for
    an undated Flood Hub gauge is the whole horizon.
    """
    path = os.path.join(C.processed_dir(release), C.PROCESSED["forecasts"])
    with open(path, encoding="utf-8") as fh:
        table = json.load(fh)
    col = {name: n for n, name in enumerate(table["columns"])}
    out = {label: dict.fromkeys(SEVERITIES, 0) for label in LABELS.values()}
    for row in table["rows"]:
        label = LABELS.get(row[col["model"]])
        if label is None:
            continue
        severity = row[col["severity"]]
        if severity not in out[label]:
            continue
        try:
            first = int(float(row[col["day_from"]]))
            last = int(float(row[col["day_to"]]))
        except (TypeError, ValueError):
            continue          # a row with no span names no day at all
        if first <= day <= last:
            out[label][severity] += 1
    return out


def concurrence(release, day):
    """Per model: its flagged cells, and how many carry other models too.

    Measured on CELLS, not alerts, because corroboration is spatial -- two
    models agree by landing on the same ground, and a count of forecasts cannot
    express that. Resolution 6 only: a coarse cell lists every model beneath it,
    which would call forecasts a hundred kilometres apart an agreement.

    Flash is excluded from the tally. It is a different hazard on a different
    geometry, so a flash polygon over a flagged river cell is not two models
    corroborating one flood.
    """
    path = os.path.join(C.processed_dir(release), C.PROCESSED["day"].format(day=day))
    with open(path, encoding="utf-8") as fh:
        table = json.load(fh)["cells"]      # the day file wraps its cell table
    col = {name: n for n, name in enumerate(table["columns"])}
    out = {label: {"cells": 0, 2: 0, 3: 0} for label in LABELS.values()}
    for row in table["rows"]:
        if row[col["res"]] != "6":
            continue
        here = {LABELS[m] for m in row[col["models"]].split("+") if m in LABELS}
        if not here:
            continue
        for label in here:
            out[label]["cells"] += 1
            if len(here) >= 3:
                out[label][3] += 1
            elif len(here) == 2:
                out[label][2] += 1
    return out


def blacked_out(releases):
    """Releases built while the GEOGLOWS blackout box was on, and what it took.

    A release records what step 2 withheld, so the table can say when its own
    GEOGLOWS figures are missing a region. Without this the caveat is invisible:
    the numbers look like any other day's, and a window straddling the day the
    box came off would mix the two with nothing on the page saying so.
    """
    out = {}
    for release in releases:
        path = os.path.join(C.processed_dir(release), C.PROCESSED["release"])
        with open(path, encoding="utf-8") as fh:
            manifest = json.load(fh)
        n = (manifest.get("run", {}).get("models", {})
             .get("geoglows", {}).get("suppressed", 0))
        if n:
            out[release] = n
    return out


def caveat(hidden, releases):
    """One sentence naming the blackout, or None when nothing was withheld."""
    if not hidden:
        return None
    where = ("every release here" if len(hidden) == len(releases)
             else f"{len(hidden)} of {len(releases)} releases "
                  f"({min(hidden)} to {max(hidden)})")
    return (f"GEOGLOWS figures exclude the blacked-out region in {where}: "
            f"{sum(hidden.values()):,} forecast(s) withheld in total.")


def collect(releases, day):
    per_release = {}
    for release in releases:
        per_release[release] = day_zero_counts(release, day)
    return per_release


def shared_percentages(releases, day):
    """Per release, per model: the share of its cells that are corroborated."""
    out = {}
    for release in releases:
        conc = concurrence(release, day)
        out[release] = {
            label: {2: (v[2] / v["cells"] if v["cells"] else None),
                    3: (v[3] / v["cells"] if v["cells"] else None)}
            for label, v in conc.items()}
    return out


def percentages(per_release):
    """Per release: each model's share of the day's alerts, and its own mix."""
    out = {}
    for release, models in per_release.items():
        total = sum(sum(counts.values()) for counts in models.values())
        row = {}
        for label, counts in models.items():
            issued = sum(counts.values())
            row[label] = {
                "alerts": issued,
                "share": issued / total if total else 0.0,
                # A model that issued nothing has no severity mix. Scoring that
                # as 0% averages a non-event in with real ones -- it is what
                # turned three silent Flood Hub days into "0.0% - 100.0%".
                **{s: (counts[s] / issued if issued else None)
                   for s in SEVERITIES},
            }
        out[release] = row
    return out


def span(values, average=False):
    values = [v for v in values if v is not None]
    if not values:
        return "\u2014"
    """A low--high range, or a mean when --average is on.

    Collapses to a single figure when low and high agree, so a column that
    never moved does not read as the spurious range "0.0% - 0.0%".
    """
    if average:
        return f"{statistics.mean(values):.1%}"
    low, high = min(values), max(values)
    if abs(high - low) < 5e-4:
        return f"{low:.1%}"
    return f"{low:.1%} – {high:.1%}"


def build_rows(pct, shared, average=False):
    """One row per metric, one column per model."""
    col = {}
    for label in LABELS.values():
        cells = [pct[r][label] for r in pct]
        sh = [shared[r][label] for r in shared]
        col[label] = [
            span([c["share"] for c in cells], average),
            *[span([c[s] for c in cells], average) for s in SEVERITIES],
            span([x[2] for x in sh], average),
            span([x[3] for x in sh], average),
        ]
    return [[m] + [col[lb][i] for lb in LABELS.values()]
            for i, m in enumerate(METRICS)]


# Transposed: metrics down the side, the three models across the top. Six
# metrics by three models fits a poster column far better than three rows of
# seven ranges, which had to be squeezed to clear the page width.
METRICS = ["Share of Alerts", "Warning", "Danger", "Extreme",
           "In 2-model Cells", "In 3-model Cells"]
HEADER = ["River Model"] + list(LABELS.values())



def write_tex(path, rows, releases, day, note=None):
    """A \\input-able table, matching the manual's booktabs style. The caption
    and the column spec live here rather than in _emit because they are part of
    what this particular table means, not of how tables are written."""
    esc = lambda s: s.replace("%", r"\%").replace("–", "--")
    note_tex = f" {esc(note)}" if note else ""
    body = "\n".join("  " + " & ".join(esc(c) for c in r) + r" \\" for r in rows)
    tex = f"""% Generated by reporting/stats/model_coverage.py -- do not hand-edit.
% Source: day {day} of releases {releases[0]} to {releases[-1]}.
\\begin{{table}}[htbp]
\\centering
\\small
\\setlength{{\\tabcolsep}}{{3pt}}
\\begin{{tabular}}{{l r r r}}
\\toprule
{" & ".join(esc(h) for h in HEADER)} \\\\
\\midrule
{body}
\\midrule
\\multicolumn{{4}}{{l}}{{\\itshape Day~{day}, {releases[0]} to {releases[-1]}}} \\\\
\\bottomrule
\\end{{tabular}}
\\caption[What each river model contributes]{{%
  \\textbf{{What each river model contributes.}}
  Every figure is the low--high range across day~{day} of
  {len(releases)} releases ({releases[0]} to {releases[-1]}).
  \\emph{{Share of alerts}} is each model's portion of all river forecasts
  issued for that day, so the column adds to 100\\%. The severity columns are
  the mix \\emph{{within}} each model, so each row adds to 100\\%. Shares say
  who issues the alerts, not who is right.
  The last two rows change denominator: they are the share of that model's
  own flagged \\emph{{cells}} that also carry one, or two, other models --
  corroboration is spatial, so it is counted on cells rather than on
  alerts.{note_tex}}}
\\label{{tab:model-coverage}}
\\end{{table}}
"""
    return _emit.write_text(path, tex)


def add_args(ap):
    """The flags every table script shares, so --since means the same thing in
    all of them and workbook.py can hand the same namespace to each."""
    ap.add_argument("--since", metavar="YYYY-MM-DD", default=FORECASTER_SINCE,
                    help=f"earliest release to include (default "
                         f"{FORECASTER_SINCE}, the first release published "
                         f"with the forecast timeline in place). The window "
                         f"grows on its own as the pipeline runs.")
    ap.add_argument("-n", "--releases", type=int, default=None,
                    help="use the N most recent releases instead of --since")
    ap.add_argument("-d", "--day", type=int, default=0,
                    help="forecast day to measure (default 0; see the module "
                         "docstring before changing it)")
    ap.add_argument("--average", action="store_true",
                    help="print means instead of low-high ranges")
    return ap


def pick_releases(args):
    every = C.list_releases()
    if not every:
        sys.exit(f"No published releases under {C.DATA_DIR}.\n"
                 f"  A release counts once its processed/release.json exists; "
                 f"run backend/daily/reindex.py if you have just copied files "
                 f"in.")
    if args.releases:
        releases = every[-args.releases:]
        if len(releases) < args.releases:
            print(f"note: asked for {args.releases} releases, found "
                  f"{len(releases)}", file=sys.stderr)
        return releases
    releases = [r for r in every if r >= args.since]
    if not releases:
        sys.exit(f"No releases on or after {args.since}. "
                 f"The newest is {every[-1]}.")
    return releases


def build(args):
    """Measure, and hand back the finished table. Separated from main() so that
    workbook.py can collect every table in one process rather than shelling out
    to each script and parsing what it printed."""
    releases = pick_releases(args)
    per_release = collect(releases, args.day)
    empty = [r for r in releases
             if not any(sum(c.values()) for c in per_release[r].values())]
    if empty:
        sys.exit(f"No day-{args.day} alerts in: {', '.join(empty)}.\n"
                 f"  Either those releases are incomplete, or day {args.day} "
                 f"is past what their models reach.")

    pct = percentages(per_release)
    shared = shared_percentages(releases, args.day)
    rows = build_rows(pct, shared, args.average)
    silent = {m: sum(1 for r in releases if not pct[r][m]["alerts"])
              for m in LABELS.values()}
    note = caveat(blacked_out(releases), releases)
    quiet = [f"{m} ({n} of {len(releases)})" for m, n in silent.items() if n]
    if quiet:
        q = ("issued no day-%d alerts in some releases -- %s -- so its "
             "severity mix is taken only from the days it spoke."
             % (args.day, "; ".join(quiet)))
        # capitalize() would lower-case the rest of the sentence, and the
        # model names live in it -- "Flood Hub" came out "flood hub".
        note = (note + " " + q) if note else q[0].upper() + q[1:]

    # The per-release numbers the summary is a range over. They are not in the
    # manual -- six metrics by three models is already as much as a page can
    # carry -- but they are what you look at when a range looks wrong, so the
    # workbook gets them on a second sheet.
    detail = ("Coverage by release",
              ["Release", "Model", *[s.title() for s in SEVERITIES],
               "Alerts", "Share of alerts"],
              [[release, label, *[per_release[release][label][s]
                                  for s in SEVERITIES],
                pct[release][label]["alerts"],
                f"{pct[release][label]['share']:.1%}"]
               for release in releases for label in LABELS.values()])

    kind = "means" if args.average else "low–high ranges"
    table = _emit.Table(
        name="model-coverage",
        title="What each river model contributes",
        subtitle=f"Day {args.day}, {releases[0]} to {releases[-1]} "
                 f"({len(releases)} releases, {kind})",
        header=HEADER, rows=rows, note=note, sheet="Model coverage",
        detail=detail, widths=[30, 26, 26, 26])
    table.releases = releases
    table.day = args.day
    return table


def main(argv=None):
    ap = add_args(argparse.ArgumentParser(
        description=__doc__.split("\n\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter))
    ap.add_argument("--tex", metavar="PATH", default=None,
                    help="where to write the LaTeX table (default "
                         "reporting/out/tables/model-coverage.tex)")
    ap.add_argument("--csv", metavar="PATH", default=None,
                    help="where to write the CSV table (default "
                         "reporting/out/tables/model-coverage.csv)")
    args = ap.parse_args(argv)

    table = build(args)
    _emit.show(table)
    _emit.write_csv(table, args.csv)
    write_tex(args.tex or _emit.tex_path(table.name),
              table.rows, table.releases, table.day, table.note)


if __name__ == "__main__":
    main()
