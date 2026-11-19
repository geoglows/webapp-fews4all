#!/usr/bin/env python3
"""Where a generated table goes, and what shapes it comes out in.

The scripts beside this one measure different things and then all have the same
problem: the same numbers have to reach a terminal, the manual, a poster and a
chat message. Each used to carry its own copy of that code, which is how three
tables drift into three styles. This module is the one copy.

NOTHING WRITTEN HERE IS COMMITTED. Every path below is under reporting/out/, which
.gitignore excludes whole. What git keeps is the sources -- the .py, .mmd and
hand-written .tex files. A table is a thing you run, not a thing you store: a
stored one is wrong by the next morning's release, and a wrong table that looks
official is worse than no table.

THE SHAPES, and who each is for

  stdout   aligned text, every run, so the table is readable the moment it
           exists and you can sanity-check it before it goes anywhere
  .csv     reporting/out/tables/<name>.csv -- the numbers on their own, for a
           spreadsheet, a plot, or anything that reads a column. Written with a
           BOM so Excel on macOS opens the en dashes and degree signs as text
           rather than as mojibake. The title, the window and the caveat follow
           the data as trailing rows, so a plain read_csv still sees the table.
  .tex     reporting/out/tables/<name>.tex -- \\input straight into the manual.
           The LaTeX itself is written by each script, because the caption and
           the column spec are part of what that table means.
  .xlsx    one workbook, one sheet per table, written by workbook.py. Copy a
           range in Excel and Paste Special into PowerPoint and it arrives as a
           real editable table rather than a picture of one -- which is the
           whole reason the poster tables are not rendered images.
"""

import csv
import os

REPORTING = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(REPORTING, "out")
TABLES = os.path.join(OUT, "tables")
FIGURES = os.path.join(OUT, "figures")
WORKBOOK = os.path.join(OUT, "fews4all-tables.xlsx")

# One font and one palette for every sheet, so a poster built by copying four
# ranges out of the workbook does not arrive in four different styles.
FONT = "Arial"
HEAD_FILL = "1F3864"
BAND_FILL = "EDF2F9"
EDGE = "B7C3D4"


class Table:
    """A finished table: the numbers, plus enough about them to label the thing
    wherever it lands. `rows` are already formatted strings -- the formatting
    decisions (a range, a percentage, an em dash for a missing figure) belong to
    the script that knows what the number means, not to the writer."""

    def __init__(self, name, title, subtitle, header, rows,
                 note=None, sheet=None, detail=None, widths=None,
                 source=None):
        self.name = name              # file stem: "model-coverage"
        self.title = title            # human title, for the sheet and the .md
        self.subtitle = subtitle      # the window the numbers came from
        self.header = list(header)
        self.rows = [list(r) for r in rows]
        self.note = note              # a caveat that travels with the numbers
        self.sheet = sheet or title[:31]   # Excel caps a tab name at 31 chars
        self.detail = detail          # optional (title, header, rows) 2nd sheet
        self.widths = widths          # column widths for the workbook
        # Which script to go and edit. Guessed from the name, because three of
        # the four follow the same spelling; passed in by the one that does not.
        self.source = source or f"reporting/stats/{name.replace('-', '_')}.py"


def out(path):
    """Create the folder `path` sits in, then hand the path straight back, so a
    writer can say open(out(...)) without a mkdir dance of its own."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    return path


def tex_path(name):
    return out(os.path.join(TABLES, f"{name}.tex"))


def csv_path(name):
    return out(os.path.join(TABLES, f"{name}.csv"))


def write_text(path, text, what="table"):
    with open(out(path), "w", encoding="utf-8") as fh:
        fh.write(text)
    print(f"wrote {path}")
    return path


def _widths(header, rows):
    return [max(len(r[i]) for r in [header] + rows)
            for i in range(len(header))]


def show(t):
    """The table as aligned text on stdout. Runs on every invocation: the point
    of a generator you can re-run is seeing the answer without opening a file."""
    w = _widths(t.header, t.rows)
    print(f"\n{t.title}")
    print(f"{t.subtitle}\n")
    print("  ".join(h.ljust(x) for h, x in zip(t.header, w)))
    print("  ".join("-" * x for x in w))
    for row in t.rows:
        print("  ".join(c.ljust(x) for c, x in zip(row, w)))
    print()
    if t.note:
        print(f"note: {t.note}\n")


def write_csv(t, path=None):
    """The table as CSV. The header row comes first so anything that reads a
    spreadsheet sees a clean table; what the numbers ARE -- the title, the
    release window, the caveat -- follows as short trailing rows, which pandas
    pads out rather than choking on."""
    path = path or csv_path(t.name)
    with open(out(path), "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(t.header)
        w.writerows(t.rows)
        w.writerow([])
        w.writerow([t.title])
        w.writerow([t.subtitle])
        if t.note:
            w.writerow([f"Note: {t.note}"])
        w.writerow([f"Generated by {t.source} -- do not hand-edit."])
    print(f"wrote {path}")
    return path


def sheet(wb, t, font_size=25, first=False):
    """One worksheet for one table, styled for a poster: 25pt by default,
    because a figure on a 44-inch board is read from a metre away."""
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

    head_fill = PatternFill("solid", fgColor=HEAD_FILL)
    band = PatternFill("solid", fgColor=BAND_FILL)
    side = Side(style="thin", color=EDGE)
    box = Border(left=side, right=side, top=side, bottom=side)

    ws = wb.active if first else wb.create_sheet()
    ws.title = t.sheet

    ws["A1"] = t.title
    ws["A1"].font = Font(name=FONT, size=font_size + 3, bold=True,
                         color=HEAD_FILL)
    ws["A2"] = t.subtitle
    ws["A2"].font = Font(name=FONT, size=font_size, italic=True,
                         color="44546A")

    top = 4
    for i, head in enumerate(t.header, 1):
        c = ws.cell(top, i, head)
        c.font = Font(name=FONT, size=font_size, bold=True, color="FFFFFF")
        c.fill = head_fill
        c.border = box
        c.alignment = Alignment(horizontal="center" if i > 1 else "left",
                                vertical="center", wrap_text=True)
    for n, row in enumerate(t.rows):
        for i, value in enumerate(row, 1):
            c = ws.cell(top + 1 + n, i, value)
            c.font = Font(name=FONT, size=font_size)
            c.border = box
            c.alignment = Alignment(horizontal="center" if i > 1 else "left",
                                    vertical="center")
            if n % 2:
                c.fill = band

    widths = t.widths or ([34] + [24] * (len(t.header) - 1))
    for i, width in enumerate(widths, 1):
        ws.column_dimensions[chr(64 + i)].width = width
    # Row height has to be set per row: a sheet does not take one from content.
    for r in range(1, top + len(t.rows) + 1):
        ws.row_dimensions[r].height = font_size + 17

    last = top + len(t.rows) + 2
    if t.note:
        ws.cell(last, 1, f"Note: {t.note}").font = Font(
            name=FONT, size=font_size - 8, italic=True, color="44546A")
        last += 1
    ws.cell(last, 1, f"Generated by {t.source} — do not hand-edit.").font = (
        Font(name=FONT, size=font_size - 10, italic=True, color="8496B0"))
    return ws
