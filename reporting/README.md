# FEWS4All reporting

Everything the project publishes about itself: the user manual, the tables and
figures that go on a poster or in a talk, and the scripts that measure them from
the delivered releases.

It is `reporting/` rather than `docs/` because half of what is here is not
documentation — it is analysis code that reads the data tree. A folder called
`docs` invites prose and makes the scripts look misfiled.

## Layout

```
reporting/
  regenerate.sh      run this to rebuild everything below out/

  stats/             reads the data tree; the answer changes with the data
  diagrams/          drawn by hand; changes only when a person changes it
  manual/            the user manual, in LaTeX
  out/               everything generated — not in git
  archive/           superseded originals — not in git
```

**`stats/` and `diagrams/` split on where the content comes from, not on what
shape it comes out in.** A statistic is re-measured on every run and is wrong by
the next morning's release; a diagram is a drawing and is only ever as wrong as
the person who drew it. That is the difference that decides how you treat a
file, so it is the one the folders follow. Splitting by *output* instead — a
`tables/` and a `figures/` — is what put `coverage_trend.py`, a measuring script
that happens to draw a line, in a different folder from the four scripts it
shares all its machinery with.

| Folder | Holds | Regenerated |
|---|---|---|
| `stats/` | `model_coverage.py`, `return_period_flow.py`, `blob_extent.py`, `coverage_trend.py`, `workbook.py`, `_emit.py` | every run |
| `diagrams/` | `forecast-timeline.mmd` and the `.tex` that captions it | only by hand |
| `manual/` | `main.tex` (the look), `content.tex` (the prose), `images/` | never |
| `out/` | `tables/`, `figures/`, `fews4all-tables.xlsx`, `manual/main.pdf` | all of it |

**Output lands here and nowhere else.** The manual, the figures and the tables
are written into `out/` by the scripts that make them, and read from there. No
copies in a delivery folder at the repo root, none in `Other/`, none beside the
poster — regenerate instead. A copy is a second answer to the same question, and
the copy is always the one that is out of date.

## Regenerating

```bash
reporting/regenerate.sh              # everything
reporting/regenerate.sh tables       # the three table scripts
reporting/regenerate.sh trend        # coverage-trend.pdf and its numbers
reporting/regenerate.sh timeline     # the Mermaid diagram — reads no data, fastest
reporting/regenerate.sh workbook     # just the spreadsheet
reporting/regenerate.sh tables --since 2026-11-08    # flags pass through
```

It uses the python already active if that python can import `pyarrow`, `numpy`
and `openpyxl` — the usual case, since you are normally in the project env. Only
from a bare shell does it fall back to `CONDA_SH` in `variables.env`.

Each run writes into `out/`:

| What | Where | For |
|---|---|---|
| aligned text | your terminal, every run | reading the answer the moment it exists |
| `.csv` | `out/tables/` | the numbers on their own — a spreadsheet, a plot, anything that reads a column |
| `.tex` | `out/tables/` | `\input` straight into the manual |
| `.pdf` | `out/figures/` | the manual, via `\graphicspath` in `main.tex` |
| `.xlsx` | `out/fews4all-tables.xlsx` | the poster — one sheet per table |

The workbook is one file rather than five because the tables are read together
or not at all. Copy a range in Excel and *Paste Special* into PowerPoint and it
lands as a real, editable PowerPoint table — which is why no table here is ever
rendered as a picture. Sheets are 25pt by default, sized for a 44-inch board
read from a metre away; `--font-size` turns that down for a report.

`stats/_emit.py` is the one copy of terminal/CSV/worksheet. A stats script
measures and formats; where the result goes is not its business.

## Building the manual

```bash
cd reporting/manual
latexmk -pdf main.tex          # writes ../out/manual/main.pdf
```

`tectonic main.tex` does the same with no TeX Live install
(`conda install -c conda-forge tectonic`).

**Run `regenerate.sh` first on a fresh clone**, or the `\input`s under
`../out/tables/` are missing and the build stops on the first one.

**Build from inside `manual/`.** TeX resolves `\input` and `\includegraphics`
against the *current working directory*, not against the file being compiled, so
running `latexmk reporting/manual/main.tex` from the repo root fails to find
`content.tex`. It reports a missing picture and then `Division by 0`, which
points at the image and says nothing about the real cause. From elsewhere,
`latexmk -cd` changes directory first and works.

## Naming

| Thing | Convention | Example |
|---|---|---|
| Folders | lowercase, one word, named for what is inside | `stats/`, not `scripts/` or `utils/` |
| Python modules | `snake_case.py` | `return_period_flow.py` |
| Everything else | `kebab-case` | `return-period-flow.csv` |
| A script and its output | the same words, the separator swapped | `blob_extent.py` → `blob-extent.csv` |
| Helpers, not products | leading underscore | `_emit.py` |
| A variant of a file | a middle extension | `forecast-timeline.tikz.tex` |
| The live version | takes the plain name | `forecast-timeline.tex` |

**Why two cases.** Python module names have to be importable, so they cannot
carry hyphens; filenames are not identifiers and read better with them. Mixing
the two in one tree looks careless until you notice the rule, so the rule is
written down: an underscore means something imports it.

**Why the script and its output share a name.** `_emit.Table` derives the
"Generated by …" line on every table from its own filename by swapping the
separator. Break the pairing and a table starts citing a script that does not
exist.

**Why a middle extension for variants.** `forecast-timeline.tikz.tex` keeps
`forecast-timeline` greppable as one string and reads as *a thing, qualified*.
`forecast-timeline-tikz.tex` reads as a different thing with a longer name, and
sorts away from its siblings.

**No dates or versions in filenames.** `model-coverage-nov18-v2.csv` is a lie
within a day. Git has the history, and every table prints the release window it
measured under its own title.

**Nothing in `out/` is ever hand-edited.** If a number is wrong, the script that
produced it is wrong. Fix it there and re-run — an edit in `out/` survives
exactly until the next person runs `regenerate.sh`.

## Writing conventions

**One sentence per line** in the prose files. It looks odd and changes nothing in
the output — LaTeX reflows paragraphs regardless. The point is the diff: edit one
sentence and the diff is one line, rather than a reflowed paragraph showing up as
"everything changed". This is most of the difference between writing that can be
reviewed in a pull request and writing that can only be re-read.

**Split `content.tex` into `sections/` once it outgrows one file** — somewhere
past ten pages. Until then one file is easier to move around in than ten.

**A number in the prose that a script could measure should be an `\input`
instead.** A typed figure is correct on the day it is typed.
