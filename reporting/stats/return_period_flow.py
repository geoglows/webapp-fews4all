#!/usr/bin/env python3
"""Discharge against return period, per model, over the delivered releases.

    python3 reporting/stats/return_period_flow.py
    python3 reporting/stats/return_period_flow.py --since 2026-11-13

Every run prints the table and writes reporting/out/tables/return-period-flow.csv
and .tex. Nothing it writes is committed: see reporting/stats/_emit.py.

WHAT IT IS FOR

A return period is only meaningful if the simulated climatology behind it is
sound. Where it is not, ordinary flow on a small reach clears a threshold it
should never reach, and the forecast arrives on the map at the loudest rung.
This table is how that shows up in the delivered data: read DOWN the median
column. If the model is behaving, a rarer event carries MORE water. Where the
figure falls instead, the return period is not measuring what it claims.

The Finland blackout (config.GEOGLOWS_BLACKOUT_BOX, and the manual section this
table sits in) is one geography where the same defect was dense enough to see
by eye. This is the global, quieter version of it.

All three river models are counted. GloFAS carries no discharge at all -- it
publishes ensemble exceedance counts against its thresholds and nothing else --
so its median flow is an em dash rather than a number. That absence is worth
seeing on the page: a third of the alert volume reports no magnitude, which is
why severity cannot be compared across the models on anything but the rung.
Flash is left out; it reports a likelihood, not a return period.
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

# Keep this in step with model_coverage.py: the same window, for the same
# reason -- releases before it predate the forecast timeline.
FORECASTER_SINCE = "2026-11-13"

LABELS = {"geoglows": "GEOGLOWS", "flood_hub": "Flood Hub",
          "glofas": "GloFAS"}

HEADER = ["Return Period", "Forecasts", "Median Flow (m3/s)"]


def flows(releases):
    """{model: {return_period: [flow, ...]}} pooled over the releases.

    Pooled rather than averaged per release: this is a distribution, and the
    rarer return periods carry so few forecasts a day that a per-release median
    would be noise. Every forecast day is counted, not just day 0 -- a reach
    whose return period is wrong is wrong on every day it appears.
    """
    out = {label: {} for label in LABELS.values()}
    missing = {label: 0 for label in LABELS.values()}
    seen = {label: 0 for label in LABELS.values()}
    for release in releases:
        path = os.path.join(C.processed_dir(release), C.PROCESSED["forecasts"])
        with open(path, encoding="utf-8") as fh:
            table = json.load(fh)
        col = {name: n for n, name in enumerate(table["columns"])}
        for row in table["rows"]:
            label = LABELS.get(row[col["model"]])
            if label is None:
                continue
            rp = str(row[col["return_period_yr"]]).strip()
            if not rp:
                continue
            seen[label] += 1
            try:
                flow = float(row[col["flow_cms"]])
            except (TypeError, ValueError):
                # A forecast with no discharge still counts toward its rung --
                # dropping it would understate GloFAS by its entire volume.
                missing[label] += 1
                flow = None
            out[label].setdefault(rp, []).append(flow)
    return out, missing, seen


def blacked_out(releases):
    """Releases built while the GEOGLOWS blackout box was on."""
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


def rows_for(by_rp, cut=None):
    rows = []
    for rp in sorted(by_rp, key=lambda x: float(x)):
        vals = by_rp[rp]
        v = sorted(x for x in vals if x is not None)
        # Flood Hub publishes "2.0" where GEOGLOWS publishes "2". Coercing
        # through float drops the trailing zero without flattening a genuine
        # fraction, so a 1.5-year band would still read "1.5-year".
        n = float(rp)
        rows.append([
            f"{n:g}-year",
            f"{len(vals):,}",
            f"{statistics.median(v):,.1f}" if v else "\u2014",
        ])
    return rows


def header(cut=None):
    return list(HEADER)



def write_tex(path, blocks, releases, note):
    esc = lambda s: s.replace("%", r"\%").replace("–", "--")
    # esc FIRST: the "(%)" in the last header is a LaTeX comment character, and
    # unescaped it swallowed the header row's line ending -- which surfaced as a
    # "Misplaced \noalign" from the next \midrule rather than anything about %.
    head = [esc(h).replace("m3/s", r"m\textsuperscript{3}/s")
            for h in header()]
    body = []
    for n, (label, rows) in enumerate(blocks):
        if n:
            body.append(r"\midrule")
        body.append(r"\multicolumn{3}{l}{\textbf{%s}} \\" % esc(label))
        body += ["  " + " & ".join(esc(c) for c in r) + r" \\" for r in rows]
    note_tex = f" {esc(note)}" if note else ""
    tex = f"""% Generated by reporting/stats/return_period_flow.py -- do not hand-edit.
% Source: all forecast days of releases {releases[0]} to {releases[-1]}.
\\begin{{table}}[htbp]
\\centering
\\small
\\begin{{tabular}}{{l r r}}
\\toprule
{" & ".join(head)} \\\\
\\midrule
{chr(10).join(body)}
\\midrule
\\multicolumn{{3}}{{l}}{{\\itshape All forecast days, {releases[0]} to {releases[-1]}}} \\\\
\\bottomrule
\\end{{tabular}}
\\caption[Discharge against return period]{{%
  \\textbf{{Discharge against return period.}}
  Every forecast day of {len(releases)} releases ({releases[0]} to
  {releases[-1]}), pooled. Read down the median column: if the climatology
  behind a return period is sound, a rarer event carries \\emph{{more}} water.
  Where the median falls as the return period rises, the return period is not
  measuring what it claims --- the same defect as the blackout described above,
  at a scale too thin to see on the map. GloFAS appears with an em dash for
  flow: it publishes ensemble exceedance counts against its thresholds and no
  discharge at all, so its rungs can be counted but not weighed.{note_tex}}}
\\label{{tab:return-period-flow}}
\\end{{table}}
"""
    return _emit.write_text(path, tex)


def build(args):
    """Measure, and hand back the finished table. The blocks (one per model)
    survive onto the table so the LaTeX writer can keep its sub-headings; the
    flat rows, with the model repeated down a column, are what reads correctly
    in a terminal, in a CSV and in a spreadsheet."""
    every = C.list_releases()
    if not every:
        sys.exit(f"No published releases under {C.DATA_DIR}.")
    releases = (every[-args.releases:] if args.releases
                else [r for r in every if r >= args.since])
    if not releases:
        sys.exit(f"No releases on or after {args.since}. "
                 f"The newest is {every[-1]}.")

    by_model, missing, seen = flows(releases)
    blocks = [(label, rows_for(by_model[label]))
              for label in LABELS.values() if by_model[label]]
    if not blocks:
        sys.exit("No forecasts with both a flow and a return period.")

    hidden = blacked_out(releases)
    notes = []
    if hidden:
        where = ("every release here" if len(hidden) == len(releases)
                 else f"{len(hidden)} of {len(releases)} releases")
        notes.append(f"GEOGLOWS figures exclude the blacked-out region in "
                     f"{where}, so the defect shown here is the part of it "
                     f"that survives outside that box.")
    # Which models published no magnitude at all. In the terminal this used to
    # sit under each block; as a note it travels with the table into the CSV
    # and the workbook, where there are no blocks to sit under.
    for label, _rows in blocks:
        n, of = missing.get(label, 0), seen.get(label, 0)
        if n:
            how = "every one" if of and n == of else f"{n:,} of {of:,}"
            notes.append(f"{label}: {how} published no flow value.")
    note = " ".join(notes) or None

    flat = [[label] + r for label, rows in blocks for r in rows]
    table = _emit.Table(
        name="return-period-flow",
        title="Discharge against return period",
        subtitle=f"All forecast days, {releases[0]} to {releases[-1]} "
                 f"({len(releases)} releases)",
        header=["Model"] + header(), rows=flat, note=note,
        sheet="Return period flow", widths=[22, 22, 20, 28])
    table.blocks = blocks
    table.releases = releases
    return table


def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__.split("\n\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--since", metavar="YYYY-MM-DD", default=FORECASTER_SINCE,
                    help=f"earliest release to include (default "
                         f"{FORECASTER_SINCE})")
    ap.add_argument("-n", "--releases", type=int, default=None,
                    help="use the N most recent releases instead of --since")
    ap.add_argument("--tex", metavar="PATH", default=None,
                    help="where to write the LaTeX table (default "
                         "reporting/out/tables/return-period-flow.tex)")
    ap.add_argument("--csv", metavar="PATH", default=None,
                    help="where to write the CSV table (default "
                         "reporting/out/tables/return-period-flow.csv)")
    args = ap.parse_args(argv)

    table = build(args)
    _emit.show(table)
    _emit.write_csv(table, args.csv)
    write_tex(args.tex or _emit.tex_path(table.name),
              table.blocks, table.releases, table.note)


if __name__ == "__main__":
    main()
