#!/usr/bin/env bash
#
# The daily FEWS4All run: fetch the models, build the release, publish it.
#
# This script does as little thinking as possible. It gets the environment into
# a known state, then calls the Python that does the actual work and reports
# whether each stage succeeded. Anything that has to READ pipeline state — which
# release is current, whether a model was stale, what to do about a half-finished
# run — lives in Python, where there is a manifest to read instead of text to
# parse. That split is deliberate: a bash script that starts doing `jq` on
# run.json is a bash script that will quietly rot.
#
#     ./fews4all.sh              the normal run
#     ./fews4all.sh --dry-run    ask the bucket what it would fetch, change nothing
#
# Schedule it with `crontab -e`. CRON USES LOCAL TIME, not UTC — worth saying
# because the thing you are waiting for is in UTC: the GEOGLOWS 00Z cycle lands
# about 04:50 UTC. Convert before choosing an hour.
#
#     0 7 * * *  /Users/you/FEWS4All/fews4all.sh >/dev/null 2>&1
#
# 07:00 in US Mountain time is 13:00 UTC, eight hours after the cycle lands, which
# leaves room for a late one. Picking an hour that is only minutes past the usual
# landing time means a slow morning upstream looks like a missing cycle.
#
# Two things cron will not do for you on a laptop: it does not run while the
# machine is asleep, and it does not catch up afterwards. If this needs to happen
# on a machine that sleeps, use launchd instead — a LaunchAgent with
# StartCalendarInterval runs a missed job once the machine wakes.
#
# (cron writes nothing to your terminal, which is why the script keeps its own log.)

# -E  functions and subshells inherit the ERR trap below
# -e  stop at the first command that fails, instead of ploughing on
# -u  using an unset variable is an error, not an empty string
# -o pipefail  a pipeline fails if ANY part of it failed, not just the last
set -Eeuo pipefail

# Where this script lives, however it was invoked — cron runs it from a
# different directory than you do, so nothing can be relative to "here".
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# FEWS_ROOT is set BEFORE the settings are read, so that variables.env can write
# paths like "$FEWS_ROOT/logs" and have them mean something. A settings file is
# sourced, not parsed — every line is a shell command run in order — so anything
# it references must already exist. Set it afterwards and "$FEWS_ROOT/logs"
# quietly becomes "/logs", at the root of the disk.
FEWS_ROOT="$SCRIPT_DIR"
# shellcheck source=variables.env.example
source "$SCRIPT_DIR/variables.env"
# ...and defaulted again, in case the settings file blanked it.
FEWS_ROOT="${FEWS_ROOT:-$SCRIPT_DIR}"
LOG_DIR="${LOG_DIR:-$FEWS_ROOT/logs}"
DRY_RUN=""
[ "${1:-}" = "--dry-run" ] && DRY_RUN="--dry-run"

# ---- logging ---------------------------------------------------------------
# Everything from here on goes to the terminal AND to today's log file. `tee -a`
# appends; the >(...) is bash sending our output into that command.
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/$(date -u +%Y-%m-%d).log"
exec > >(tee -a "$LOG") 2>&1
echo "=============================================================="
echo "FEWS4All daily run — $(date -u '+%Y-%m-%d %H:%M:%S')Z"
echo "  root $FEWS_ROOT"
echo "  log  $LOG"

# ---- what to do when something breaks --------------------------------------
fail() {
  echo "FAILED: $1" >&2
  if [ -n "${WEBHOOK_ERROR_URL:-}" ]; then
    curl -fsS -X POST -H 'Content-Type: application/json' \
      -d "{\"text\":\"FEWS4All daily run failed: $1\"}" \
      "$WEBHOOK_ERROR_URL" || true   # a failed alert must not mask the failure
  fi
  exit 1
}
# Any command that fails without being checked lands here, naming the line.
trap 'fail "line $LINENO: $BASH_COMMAND"' ERR

# ---- only one run at a time ------------------------------------------------
# macOS has no `flock`, so the lock is a directory: creating one is atomic, so
# exactly one process can win the race. The PID inside lets us tell a running
# job from a lock left behind by a crash.
LOCK="$FEWS_ROOT/.fews4all.lock"
if [ -d "$LOCK" ]; then
  # Read it once. Interpolating a second `cat` into the message below would run a
  # command that can fail inside a string, which trips the ERR trap and prints a
  # failure for a case that is not one.
  LOCK_PID="$(cat "$LOCK/pid" 2>/dev/null || true)"
  if [ -n "$LOCK_PID" ] && kill -0 "$LOCK_PID" 2>/dev/null; then
    echo "Another run is in progress (pid $LOCK_PID). Nothing to do."
    exit 0
  fi
  echo "note: clearing a stale lock from a run that did not finish"
  rm -rf "$LOCK"
fi
mkdir "$LOCK" && echo $$ > "$LOCK/pid"
trap 'rm -rf "$LOCK"' EXIT     # runs however we leave, including on failure

# ---- grace period ----------------------------------------------------------
# A scheduled run you are watching and do not want can be stopped here.
if [ "${SLEEP_LENGTH:-0}" -gt 0 ] && [ -z "$DRY_RUN" ]; then
  echo "Starting in ${SLEEP_LENGTH}s — Ctrl-C to cancel."
  sleep "$SLEEP_LENGTH"
fi

# ---- the environment -------------------------------------------------------
[ -f "$CONDA_SH" ] || fail "no conda at $CONDA_SH — check CONDA_SH in variables.env"
# conda's own scripts reference unset variables, which `set -u` treats as fatal,
# so it is relaxed just across the activation and turned straight back on.
set +u
# shellcheck disable=SC1090
source "$CONDA_SH"
conda activate "$CONDA_ENV" || fail "could not activate conda env '$CONDA_ENV'"
set -u
echo "  env  $CONDA_ENV ($(python --version 2>&1))"

for cmd in python curl; do
  command -v "$cmd" >/dev/null 2>&1 || fail "'$cmd' is not on the PATH"
done
python -c "import pyarrow" 2>/dev/null || fail "pyarrow is missing from '$CONDA_ENV'"

# ---- the work --------------------------------------------------------------
# Each stage is checked on its own so the message says which one went wrong,
# rather than leaving you to work it out from a stack trace.

echo
echo "--- step 1: retrieve ------------------------------------------"
python "$FEWS_ROOT/backend/daily/step1_fetch.py" $DRY_RUN \
  || fail "step 1 (retrieve)"

if [ -n "$DRY_RUN" ]; then
  echo
  echo "Dry run — stopping before anything is processed."
  exit 0
fi

echo
echo "--- steps 2-5: build the release ------------------------------"
python "$FEWS_ROOT/backend/daily/run_daily.py" \
  || fail "steps 2-5 (build)"

# --- step 6: publish to S3 -------------------------------------------------
# Not written yet. When it is, it goes here, and it must upload the release
# folder BEFORE the pointer — a viewer who loads in between would otherwise get
# a half-uploaded release, and a truncated CSV parses fine.

echo
echo "Done — $(date -u '+%Y-%m-%d %H:%M:%S')Z"
