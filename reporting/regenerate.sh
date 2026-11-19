#!/usr/bin/env bash
# Rebuild every generated table and figure under reporting/out/.
#
# Nothing regenerates on its own. A table's .csv and .tex, and a figure's .pdf,
# are OUTPUTS: editing the script or the .mmd that produced one changes nothing
# on the page until that producer is run again. This script is the "run them
# again" step, so the manual is never built from a stale artefact.
#
# None of what it writes is in git, so a fresh clone has to run this once
# before the manual will compile. See reporting/.gitignore for why.
#
#   ./regenerate.sh              everything
#   ./regenerate.sh tables       the three table scripts
#   ./regenerate.sh figures      coverage trend + forecast timeline
#   ./regenerate.sh timeline     the forecast timeline alone (fastest loop)
#   ./regenerate.sh workbook     just the spreadsheet
#
# Anything after the target is passed straight to the python producers, so the
# flags they share work here too:
#
#   ./regenerate.sh tables --since 2026-11-08
#   ./regenerate.sh all -n 5
#
# The timeline ignores those flags -- it reads no data, only the .mmd.
#
# Afterwards, build the manual itself:  cd reporting/manual && latexmk -pdf main.tex
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
OUT="$HERE/out"

target="all"
if [ $# -gt 0 ] && [[ "$1" != -* ]]; then target="$1"; shift; fi

# Which python runs the generators. They need pyarrow, numpy, h3 and openpyxl;
# the timeline needs none of it.
PY=python3
command -v python >/dev/null 2>&1 && PY=python

# If the shell is ALREADY in an env that can do the work -- the usual case, you
# ran `conda activate hydroinformatics` an hour ago -- use it and leave it
# alone. Activating from inside an active env is how you end up running in a
# different one than the prompt claims. Only fall back to variables.env when
# the current python cannot import what the scripts need.
activate() {
  [ -n "${FEWS_ENV_READY:-}" ] && return 0
  FEWS_ENV_READY=1

  if "$PY" -c "import pyarrow, numpy, openpyxl" >/dev/null 2>&1; then
    echo "  env  the active python ($("$PY" --version 2>&1))"
    return 0
  fi

  # FEWS_ROOT is exported BEFORE the settings are read, exactly as fews4all.sh
  # does it: variables.env writes paths like "$FEWS_ROOT/logs", and a settings
  # file is a shell script, so anything it references must already exist. Under
  # `set -u` an unset one is not a blank, it is a fatal error.
  export FEWS_ROOT="$ROOT"
  # shellcheck source=../variables.env.example
  set +u; source "$ROOT/variables.env"; set -u

  [ -f "${CONDA_SH:-}" ] || {
    echo "This python cannot import pyarrow, and there is no conda at" >&2
    echo "  '${CONDA_SH:-<CONDA_SH unset>}' to fall back to." >&2
    echo "Either activate the project env first (conda activate \$CONDA_ENV)" >&2
    echo "or fix CONDA_SH in variables.env." >&2
    exit 1
  }
  set +u; source "$CONDA_SH"; conda activate "$CONDA_ENV"; set -u
  PY=python
  echo "  env  $CONDA_ENV ($(python --version 2>&1))"
}

# Each script prints its table and writes its own .csv and .tex into out/.
# No output paths here: a path in two places is a path that goes wrong in one.
tables() {
  activate
  echo "== tables"
  "$PY" "$HERE/stats/model_coverage.py"     "$@"
  "$PY" "$HERE/stats/return_period_flow.py" "$@"
  "$PY" "$HERE/stats/blob_extent.py"        "$@"
}

trend() {
  activate
  echo "== coverage trend"
  "$PY" "$HERE/stats/coverage_trend.py" "$@"
}

# One workbook, every table a sheet. It re-measures rather than reading the
# .csv files back, so a sheet can never disagree with the table it came from.
workbook() {
  activate
  echo "== workbook"
  "$PY" "$HERE/stats/workbook.py" "$@"
}

# mermaid-cli is a node tool, not a python one -- if mmdc is not on PATH, npx
# fetches it on the spot, which needs a network connection the first time.
timeline() {
  echo "== forecast timeline"
  local mmd="$HERE/diagrams/forecast-timeline.mmd"
  local out="$OUT/figures/forecast-timeline.pdf"
  mkdir -p "$(dirname "$out")"
  if command -v mmdc >/dev/null 2>&1; then
    mmdc -i "$mmd" -o "$out" -b white --pdfFit
  else
    npx -y @mermaid-js/mermaid-cli -i "$mmd" -o "$out" -b white --pdfFit
  fi
  echo "wrote $out"
}

case "$target" in
  all)      tables "$@"; trend "$@"; timeline; workbook "$@" ;;
  tables)   tables "$@" ;;
  figures)  trend "$@"; timeline ;;
  trend)    trend "$@" ;;
  timeline) timeline ;;
  workbook) workbook "$@" ;;
  *) echo "unknown target '$target' -- use all, tables, figures, trend, timeline or workbook" >&2; exit 2 ;;
esac

echo
echo "done -- everything is under reporting/out/"
echo "  tables   $OUT/tables      (.csv for the numbers, .tex for the manual)"
echo "  figures  $OUT/figures     (.pdf, found by \\graphicspath in main.tex)"
echo "  book     $OUT/fews4all-tables.xlsx"
echo "  manual   $OUT/manual      (latexmk writes the PDF here)"
