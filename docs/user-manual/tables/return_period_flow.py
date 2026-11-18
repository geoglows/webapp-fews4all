#!/usr/bin/env python3
"""Discharge against return period, per model, over the delivered releases.

    python3 docs/user-manual/tables/return_period_flow.py
    python3 docs/user-manual/tables/return_period_flow.py --tex tables/return-period-flow.tex
    python3 docs/user-manual/tables/return_period_flow.py --cut 25

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

GloFAS is excluded because it publishes no discharge at all -- only ensemble
exceedance counts against its thresholds -- so there is no flow column to build.
Flash is excluded for the same reason.
"""

import argparse
import json
import os
import statistics
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "backend"))

from common import config as C  # noqa: E402

# Keep this in step with model_coverage.py: the same window, for the same
# reason -- releases before it predate the forecast timeline.
FORECASTER_SINCE = "2026-11-06"

# Only the models that publish a discharge. See the module docstring.
LABELS = {"geoglows": "GEOGLOWS", "flood_hub": "Flood Hub"}

HEADER = ["Return period", "Forecasts", "Min flow", "Median flow", "Under cut"]


def flows(releases):
    """{model: {return_period: [flow, ...]}} pooled over the releases.

    Pooled rather than averaged per release: this is a distribution, and the
    rarer return periods carry so few forecasts a day that a per-release median
    would be noise. Every forecast day is counted, not just day 0 -- a reach
    whose return period is wrong is wrong on every day it appears.
    """
    out = {label: {} for label in LABELS.values()}
    missing = {label: 0 for label in LABELS.values()}
    for release in releases:
        path = os.path.join(C.processed_dir(release), C.PROCESSED["forecasts"])
        with open(path, encoding="utf-8") as fh:
            table = json.load(fh)
        col = {name: n for n, name in enumerate(table["columns"])}
        for row in table["rows"]:
            label = LABELS.get(row[col["model"]])
            if label is None:
                continue
            try:
                flow = float(row[col["flow_cms"]])
            except (TypeError, ValueError):
                missing[label] += 1      # Flood Hub leaves some blank
                continue
            rp = str(row[col["return_period_yr"]]).strip()
            if not rp:
                continue
            out[label].setdefault(rp, []).append(flow)
    return out, missing


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


def rows_for(by_rp, cut):
    rows = []
    for rp in sorted(by_rp, key=lambda x: float(x)):
        v = sorted(by_rp[rp])
        # Flood Hub publishes "2.0" where GEOGLOWS publishes "2". Coercing
        # through float drops the trailing zero without flattening a genuine
        # fraction, so a 1.5-year band would still read "1.5-year".
        n = float(rp)
        rows.append([
            f"{n:g}-year",
            f"{len(v):,}",
            f"{v[0]:,.1f}",
            f"{statistics.median(v):,.1f}",
            f"{sum(1 for x in v if x < cut) / len(v):.1%}",
        ])
    return rows


def header(cut):
    return HEADER[:-1] + [f"Under {cut:g} m3/s"]


def print_table(blocks, releases, cut, note, missing):
    head = header(cut)
    width = [len(h) for h in head]
    for _label, rows in blocks:
        for r in rows:
            width = [max(w, len(c)) for w, c in zip(width, r)]
    print(f"\nAll forecast days, {len(releases)} release(s): "
          f"{releases[0]} to {releases[-1]}\n")
    for label, rows in blocks:
        print(label)
        print("  " + "  ".join(h.ljust(w) for h, w in zip(head, width)))
        print("  " + "  ".join("-" * w for w in width))
        for r in rows:
            print("  " + "  ".join(c.ljust(w) for c, w in zip(r, width)))
        if missing.get(label):
            print(f"  ({missing[label]:,} forecast(s) published no flow value)")
        print()
    if note:
        print(f"note: {note}\n")


def write_tex(path, blocks, releases, cut, note):
    esc = lambda s: s.replace("%", r"\%").replace("–", "--")
    head = [h.replace("m3/s", r"m\textsuperscript{3}/s") for h in header(cut)]
    body = []
    for n, (label, rows) in enumerate(blocks):
        if n:
            body.append(r"\midrule")
        body.append(r"\multicolumn{5}{l}{\textbf{%s}} \\" % esc(label))
        body += ["  " + " & ".join(esc(c) for c in r) + r" \\" for r in rows]
    note_tex = f" {esc(note)}" if note else ""
    tex = f"""% Generated by docs/user-manual/tables/return_period_flow.py -- do not hand-edit.
% Source: all forecast days of releases {releases[0]} to {releases[-1]}.
\\begin{{table}}[htbp]
\\centering
\\small
\\begin{{tabular}}{{l r r r r}}
\\toprule
{" & ".join(head)} \\\\
\\midrule
{chr(10).join(body)}
\\bottomrule
\\end{{tabular}}
\\caption[Discharge against return period]{{%
  \\textbf{{Discharge against return period.}}
  Every forecast day of {len(releases)} releases ({releases[0]} to
  {releases[-1]}), pooled. Read down the median column: if the climatology
  behind a return period is sound, a rarer event carries \\emph{{more}} water.
  Where the median falls as the return period rises, the return period is not
  measuring what it claims --- the same defect as the blackout described above,
  at a scale too thin to see on the map. GloFAS is absent because it publishes
  no discharge, only ensemble exceedance counts.{note_tex}}}
\\label{{tab:return-period-flow}}
\\end{{table}}
"""
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(tex)
    print(f"wrote {path}")


def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__.split("\n\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--since", metavar="YYYY-MM-DD", default=FORECASTER_SINCE,
                    help=f"earliest release to include (default "
                         f"{FORECASTER_SINCE})")
    ap.add_argument("-n", "--releases", type=int, default=None,
                    help="use the N most recent releases instead of --since")
    ap.add_argument("--cut", type=float, default=10.0,
                    help="the low-flow threshold the last column counts "
                         "(default 10 m3/s)")
    ap.add_argument("--tex", metavar="PATH",
                    help="also write an \\input-able LaTeX table")
    args = ap.parse_args(argv)

    every = C.list_releases()
    if not every:
        sys.exit(f"No published releases under {C.DATA_DIR}.")
    releases = (every[-args.releases:] if args.releases
                else [r for r in every if r >= args.since])
    if not releases:
        sys.exit(f"No releases on or after {args.since}. "
                 f"The newest is {every[-1]}.")

    by_model, missing = flows(releases)
    blocks = [(label, rows_for(by_model[label], args.cut))
              for label in LABELS.values() if by_model[label]]
    if not blocks:
        sys.exit("No forecasts with both a flow and a return period.")

    hidden = blacked_out(releases)
    note = None
    if hidden:
        where = ("every release here" if len(hidden) == len(releases)
                 else f"{len(hidden)} of {len(releases)} releases")
        note = (f"GEOGLOWS figures exclude the blacked-out region in {where}, "
                f"so the defect shown here is the part of it that survives "
                f"outside that box.")
    print_table(blocks, releases, args.cut, note, missing)
    if args.tex:
        write_tex(args.tex, blocks, releases, args.cut, note)


if __name__ == "__main__":
    main()
