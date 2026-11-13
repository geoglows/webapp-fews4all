#!/usr/bin/env python3
"""What each river model contributes, measured from the delivered releases.

Run it and it re-derives the table from whatever is on disk, so the numbers in
the manual and on a poster can be refreshed rather than retyped.

    python3 docs/user-manual/tables/model_coverage.py
    python3 docs/user-manual/tables/model_coverage.py --tex tables/model-coverage.tex
    python3 docs/user-manual/tables/model_coverage.py --xlsx coverage.xlsx --font-size 25

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

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "backend"))

from common import config as C  # noqa: E402

# Releases before this one predate the forecast timeline, so their day spans
# were not assigned the way the ones here are and the two are not comparable.
# Counting from a date rather than a release count means the window widens by
# itself as the pipeline runs, instead of silently sliding off the start.
FORECASTER_SINCE = "2026-11-06"

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


def reach(release):
    """How far each model's forecasts run, as the release itself recorded it."""
    path = os.path.join(C.processed_dir(release), C.PROCESSED["release"])
    with open(path, encoding="utf-8") as fh:
        manifest = json.load(fh)
    # model_reach is the last day index, so +1 turns it into a count of days.
    return {LABELS[m]: v + 1 for m, v in manifest.get("model_reach", {}).items()
            if m in LABELS}


def collect(releases, day):
    per_release = {}
    for release in releases:
        per_release[release] = day_zero_counts(release, day)
    return per_release


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
                **{s: (counts[s] / issued if issued else 0.0) for s in SEVERITIES},
            }
        out[release] = row
    return out


def span(values, average=False):
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


def build_rows(pct, days_ahead, average=False):
    rows = []
    for label in LABELS.values():
        cells = [pct[r][label] for r in pct]
        rows.append([
            label,
            span([c["share"] for c in cells], average),
            *[span([c[s] for c in cells], average) for s in SEVERITIES],
            str(days_ahead.get(label, "")),
        ])
    return rows


HEADER = ["River model", "Share of alerts", "Warning", "Danger", "Extreme",
          "Days ahead"]


def print_table(rows, releases, day, average, note=None):
    width = [max(len(r[i]) for r in [HEADER] + rows) for i in range(len(HEADER))]
    rule = "  ".join("-" * w for w in width)
    kind = "mean" if average else "low–high range"
    print(f"\nDay {day} of {len(releases)} release(s): "
          f"{releases[0]} to {releases[-1]}   ({kind})\n")
    print("  ".join(h.ljust(w) for h, w in zip(HEADER, width)))
    print(rule)
    for row in rows:
        print("  ".join(c.ljust(w) for c, w in zip(row, width)))
    print()
    if note:
        print(f"note: {note}\n")


def write_tex(path, rows, releases, day, note=None):
    """A \\input-able table, matching the manual's booktabs style."""
    esc = lambda s: s.replace("%", r"\%").replace("–", "--")
    note_tex = f" {esc(note)}" if note else ""
    body = "\n".join("  " + " & ".join(esc(c) for c in r) + r" \\" for r in rows)
    tex = f"""% Generated by docs/user-manual/tables/model_coverage.py -- do not hand-edit.
% Source: day {day} of releases {releases[0]} to {releases[-1]}.
\\begin{{table}}[htbp]
\\centering
\\begin{{tabular}}{{l r r r r r}}
\\toprule
{" & ".join(esc(h) for h in HEADER)} \\\\
\\midrule
{body}
\\bottomrule
\\end{{tabular}}
\\caption[What each river model contributes]{{%
  \\textbf{{What each river model contributes.}}
  Every figure is the low--high range across day~{day} of
  {len(releases)} releases ({releases[0]} to {releases[-1]}).
  \\emph{{Share of alerts}} is each model's portion of all river forecasts
  issued for that day, so the column adds to 100\\%. The severity columns are
  the mix \\emph{{within}} each model, so each row adds to 100\\%. Shares say
  who issues the alerts, not who is right.{note_tex}}}
\\label{{tab:model-coverage}}
\\end{{table}}
"""
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(tex)
    print(f"wrote {path}")


def write_xlsx(path, rows, per_release, pct, releases, day, font_size):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

    font = "Arial"
    head_fill = PatternFill("solid", fgColor="1F3864")
    band = PatternFill("solid", fgColor="EDF2F9")
    edge = Side(style="thin", color="B7C3D4")
    box = Border(left=edge, right=edge, top=edge, bottom=edge)

    wb = Workbook()
    sheet = wb.active
    sheet.title = "Summary"
    sheet["A1"] = "FEWS4All — what each river model contributes"
    sheet["A1"].font = Font(name=font, size=font_size + 3, bold=True,
                            color="1F3864")
    sheet["A2"] = (f"Range across day {day} of {len(releases)} releases, "
                   f"{releases[0]} to {releases[-1]}")
    sheet["A2"].font = Font(name=font, size=font_size, italic=True,
                            color="44546A")

    top = 4
    for i, head in enumerate(HEADER, 1):
        cell = sheet.cell(top, i, head)
        cell.font = Font(name=font, size=font_size, bold=True, color="FFFFFF")
        cell.fill = head_fill
        cell.border = box
        cell.alignment = Alignment(horizontal="center" if i > 1 else "left",
                                   vertical="center", wrap_text=True)
    for n, row in enumerate(rows):
        for i, value in enumerate(row, 1):
            cell = sheet.cell(top + 1 + n, i, value)
            cell.font = Font(name=font, size=font_size)
            cell.border = box
            cell.alignment = Alignment(
                horizontal="center" if i > 1 else "left", vertical="center")
            if n % 2:
                cell.fill = band
    for col, width in zip("ABCDEF", [26, 26, 26, 26, 26, 17]):
        sheet.column_dimensions[col].width = width
    for r in range(1, top + len(rows) + 1):
        sheet.row_dimensions[r].height = font_size + 17

    raw = wb.create_sheet("By release")
    raw.append(["Release", "Model", *[s.title() for s in SEVERITIES],
                "Alerts", "Share of alerts"])
    for release in releases:
        for label in LABELS.values():
            counts = per_release[release][label]
            raw.append([release, label, *[counts[s] for s in SEVERITIES],
                        pct[release][label]["alerts"],
                        pct[release][label]["share"]])
    for i in range(1, 8):
        cell = raw.cell(1, i)
        cell.font = Font(name=font, size=font_size, bold=True, color="FFFFFF")
        cell.fill = head_fill
        cell.border = box
        cell.alignment = Alignment(horizontal="center", vertical="center",
                                   wrap_text=True)
    for r in range(2, raw.max_row + 1):
        for i in range(1, 8):
            cell = raw.cell(r, i)
            cell.font = Font(name=font, size=font_size)
            cell.border = box
            cell.alignment = Alignment(
                horizontal="center" if i > 2 else "left", vertical="center")
            if i == 7:
                cell.number_format = "0.0%"
            elif i > 2:
                cell.number_format = "#,##0"
            if r % 2:
                cell.fill = band
        raw.row_dimensions[r].height = font_size + 13
    raw.row_dimensions[1].height = font_size + 25
    for col, width in zip("ABCDEFG", [22, 22, 16, 14, 16, 15, 20]):
        raw.column_dimensions[col].width = width
    raw.freeze_panes = "A2"

    wb.save(path)
    print(f"wrote {path}")


def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__.split("\n\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter)
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
    ap.add_argument("--tex", metavar="PATH",
                    help="also write an \\input-able LaTeX table")
    ap.add_argument("--xlsx", metavar="PATH",
                    help="also write a two-sheet workbook")
    ap.add_argument("--font-size", type=int, default=25,
                    help="point size for the workbook (default 25)")
    args = ap.parse_args(argv)

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
    else:
        releases = [r for r in every if r >= args.since]
        if not releases:
            sys.exit(f"No releases on or after {args.since}. "
                     f"The newest is {every[-1]}.")

    per_release = collect(releases, args.day)
    empty = [r for r in releases
             if not any(sum(c.values()) for c in per_release[r].values())]
    if empty:
        sys.exit(f"No day-{args.day} alerts in: {', '.join(empty)}.\n"
                 f"  Either those releases are incomplete, or day {args.day} "
                 f"is past what their models reach.")

    pct = percentages(per_release)
    rows = build_rows(pct, reach(releases[-1]), args.average)
    note = caveat(blacked_out(releases), releases)
    print_table(rows, releases, args.day, args.average, note)
    if args.tex:
        write_tex(args.tex, rows, releases, args.day, note)
    if args.xlsx:
        write_xlsx(args.xlsx, rows, per_release, pct, releases, args.day,
                   args.font_size)


if __name__ == "__main__":
    main()
