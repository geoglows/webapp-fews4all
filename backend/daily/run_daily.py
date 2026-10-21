#!/usr/bin/env python3
"""The daily run — steps 2 to 5, assembled out of sight and published at the end.

WHY THIS EXISTS RATHER THAN A SHELL SCRIPT OF FOUR LINES

Because the four steps are not independent. Step 2 writes the release; step 3
rewrites cell_forecasts.csv in place with the rolled-up resolutions; step 4
rewrites cells.csv with its impact columns and forecasts.csv with its districts;
step 5 adds the style tables. A release is only readable by the app once all four
have run, so a morning that dies at step 4 does not leave yesterday's map up — it
leaves today's folder holding a cells.csv missing the columns the front end
expects, and the app fails on a file that is present and parses.

So the run is built in a staging folder nobody is pointed at, and the finished
folder is moved into place as the last act. The live release is either yesterday's
in full or today's in full. There is no state in between for the app to find.

The previous release is kept beside the new one as <release>.prev until the move
has succeeded, so a failure during the swap itself can be undone rather than
leaving a gap.

WHAT COUNTS AS FAILURE

Any step exiting non-zero. The steps already decide, individually, what is worth
refusing over — step 2 will not write an empty release, and a model it cannot
vouch for is dropped from the run rather than ending it. By the time something
reaches here it is a real failure, and the right answer is to leave the published
release exactly as it was and say what happened.

    python3 backend/daily/run_daily.py
    python3 backend/daily/run_daily.py --input backend/input/2026-09-23
    python3 backend/daily/run_daily.py --keep-staging      # leave it for inspection
"""

import os
import shutil
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common import config as C          # noqa: E402
from common import manifest as MAN      # noqa: E402

import step2_intersect                  # noqa: E402
import step3_compare                    # noqa: E402
import step4_summary                    # noqa: E402
import step5_rules                      # noqa: E402


def rule(title):
    print(f"\n{'=' * 72}\n{title}\n{'=' * 72}")


def promote(staged, live):
    """Move the finished release into place, keeping the old one until it lands.

    os.replace will not rename onto a non-empty directory, so the published
    release is stepped aside first. The order matters: if the move fails, the old
    release is put back, and the app keeps drawing what it was drawing.
    """
    prev = live + ".prev"
    if os.path.exists(prev):
        shutil.rmtree(prev)
    stepped_aside = False
    if os.path.exists(live):
        os.rename(live, prev)
        stepped_aside = True
    try:
        os.makedirs(os.path.dirname(live), exist_ok=True)
        os.rename(staged, live)
    except OSError:
        if stepped_aside:
            os.rename(prev, live)          # put the old release back
        raise
    if stepped_aside:
        try:
            shutil.rmtree(prev)
        except OSError as e:
            print(f"  note: the previous release is still at {prev} ({e})")
    return live


def main(input_dir=None, keep_staging=False):
    t0 = time.time()
    root = C.OUTPUT_DIR
    staging_root = os.path.join(root, C.STAGING_DIR)

    rule("Step 2 — forecasts onto the grid")
    release = step2_intersect.main(input_dir, staging=True)
    staged = os.path.join(staging_root, release)

    rule("Step 3 — agreement and roll-up")
    step3_compare.main(staged)

    rule("Step 4 — impact and districts")
    step4_summary.main(staged)

    rule("Step 5 — style tables")
    step5_rules.main(staged)

    rule(f"Publishing {release}")
    live = promote(staged, os.path.join(root, release))
    C.write_release_pointer(release)
    print(f"  {live}")
    print(f"  {C.RELEASE_POINTER} -> {release}")

    if not keep_staging:
        try:
            if os.path.isdir(staging_root) and not os.listdir(staging_root):
                os.rmdir(staging_root)
        except OSError:
            pass

    data = MAN.read(live)
    if data:
        print()
        print(MAN.summarise(data))
        for note in data.get("notes") or []:
            print(f"    note: {note}")
    print(f"\nDone in {time.time() - t0:.0f}s")
    return release


if __name__ == "__main__":
    a = sys.argv[1:]
    folder = a[a.index("--input") + 1] if "--input" in a else None
    try:
        main(folder, keep_staging="--keep-staging" in a)
    except SystemExit as e:
        # A step refused. The published release has not been touched.
        if e.code not in (0, None):
            print(f"\nRun abandoned: {e}\n  The published release is unchanged.",
                  file=sys.stderr)
        raise
