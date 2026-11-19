#!/usr/bin/env python3
"""Every generated table, in one workbook.

    python3 reporting/stats/workbook.py
    python3 reporting/stats/workbook.py --since 2026-11-13 --font-size 18

Writes reporting/out/fews4all-tables.xlsx: a cover sheet, then one sheet per
table, then the release-by-release detail behind the tables that are ranges.

WHY A WORKBOOK AND NOT FOUR FILES

The tables are read together or not at all -- coverage, the artifact inside it,
the return periods underneath it. One file is one thing to open, one thing to
send someone, and one thing that cannot be half-stale.

WHY EXCEL AND NOT AN IMAGE

A poster table has to be editable at the last minute, in the room, by whoever
is holding the laptop. Copy a range here and Paste Special into PowerPoint and
it arrives as a real PowerPoint table: fonts follow the deck, a typo is a
click, a column can be widened. A rendered picture of a table can do none of
that, which is why nothing here draws one.

Default 25pt, sized for a 44-inch board read from a metre away. --font-size
turns it down for a report.
"""

import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import _emit                                        # noqa: E402
import blob_extent                                  # noqa: E402
import coverage_trend                               # noqa: E402
import model_coverage                               # noqa: E402
import return_period_flow                           # noqa: E402

# Order matters: this is the order someone reads them in. Coverage first --
# how much of the map each model paints -- then the trend behind that number,
# then the two tables that say what is wrong with some of it.
SOURCES = [model_coverage, coverage_trend, return_period_flow, blob_extent]


class Args:
    """The options one builder gets. Pass --since here and every sheet uses it;
    leave it off and each script keeps its own default window, which is not the
    same for all of them -- the coverage trend starts a week earlier on purpose,
    so the releases that predate the step-2 fix are visible as the thing the
    fix corrected. Every sheet states its own window under its title, so two
    windows in one book cannot be mistaken for one."""

    def __init__(self, **kw):
        self.__dict__.update(kw)


def cover(wb, tables):
    from openpyxl.styles import Font
    ws = wb.active
    ws.title = "Contents"
    ws["A1"] = "FEWS4All — generated tables"
    ws["A1"].font = Font(name=_emit.FONT, size=20, bold=True,
                         color=_emit.HEAD_FILL)
    ws["A2"] = ("Every sheet is regenerated from the delivered releases by "
                "reporting/regenerate.sh. Nothing here is hand-edited, and nothing "
                "here is in git — re-run rather than correct.")
    ws["A2"].font = Font(name=_emit.FONT, size=11, italic=True,
                         color="44546A")
    row = 4
    for t in tables:
        ws.cell(row, 1, t.sheet).font = Font(name=_emit.FONT, size=12,
                                             bold=True)
        ws.cell(row, 2, t.title).font = Font(name=_emit.FONT, size=12)
        ws.cell(row, 3, t.subtitle).font = Font(name=_emit.FONT, size=12,
                                                color="44546A")
        ws.cell(row, 4, t.source).font = Font(name=_emit.FONT, size=11,
                                              color="8496B0")
        row += 1
    for col, width in zip("ABCD", [24, 46, 52, 40]):
        ws.column_dimensions[col].width = width
    return ws


def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__.split("\n\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--since", metavar="YYYY-MM-DD", default=None,
                    help="earliest release to include, for every sheet "
                         "(default: each script's own window)")
    ap.add_argument("-n", "--releases", type=int, default=None,
                    help="use the N most recent releases instead of --since")
    ap.add_argument("-d", "--day", type=int, default=0,
                    help="forecast day for the coverage sheet (default 0)")
    ap.add_argument("--average", action="store_true",
                    help="means instead of low-high ranges")
    ap.add_argument("--scope", choices=["day0", "all"], default="day0",
                    help="which days the coverage trend counts (default day0)")
    ap.add_argument("--font-size", type=int, default=25,
                    help="point size for the table sheets (default 25, sized "
                         "for a poster)")
    ap.add_argument("--out", default=_emit.WORKBOOK,
                    help=f"workbook path (default {_emit.WORKBOOK})")
    cli = ap.parse_args(argv)

    tables = []
    for module in SOURCES:
        print(f"building {module.__name__} ...")
        tables.append(module.build(Args(
            since=cli.since or module.FORECASTER_SINCE,
            releases=cli.releases, day=cli.day,
            average=cli.average, scope=cli.scope)))

    from openpyxl import Workbook
    wb = Workbook()
    cover(wb, tables)
    for t in tables:
        _emit.sheet(wb, t, cli.font_size)
        # A range hides its own history. Where a builder handed us the
        # release-by-release numbers it was summarising, they follow it.
        if getattr(t, "detail", None):
            title, header, rows = t.detail
            _emit.sheet(wb, _emit.Table(
                name=t.name + "-detail", title=title, subtitle=t.subtitle,
                header=header, rows=rows, sheet=title[:31],
                widths=[22] * len(header), source=t.source),
                max(11, cli.font_size - 11))

    wb.save(_emit.out(cli.out))
    print(f"\nwrote {cli.out}  ({len(wb.sheetnames)} sheets: "
          f"{', '.join(wb.sheetnames)})")


if __name__ == "__main__":
    main()
