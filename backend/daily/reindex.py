#!/usr/bin/env python3
"""Rebuild releases.json and current_release.txt from whatever is on disk.

Needed after anything that adds or removes a release behind the pipeline's back.
In practice that means an rsync of the data tree from another machine: the
release folders arrive, but the index either does not, or arrives listing only
the days THAT machine had. Copy a one-release tree onto a seventeen-release one
and its index will quietly claim there is one.

The index is derived and never appended to -- it is rebuilt from the folders
present every time -- so running this is always safe and always right. A release
counts when it has a processed/ folder; raw downloads without one are a run that
fetched and then failed, and must not appear as a day the map can draw.

    python3 backend/daily/reindex.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common import config as C          # noqa: E402
from common import manifest as MAN      # noqa: E402


def main():
    names = C.list_releases()
    if not names:
        sys.exit(f"No published releases under {C.DATA_DIR}")
    idx = MAN.write_index(C.DATA_DIR, current=names[-1])
    C.write_release_pointer(names[-1])
    print(f"  {len(names)} release(s): {names[0]} .. {names[-1]}")
    print(f"  {C.RELEASE_INDEX}  {'rebuilt' if idx else 'FAILED — see the note above'}")
    print(f"  {C.RELEASE_POINTER} -> {names[-1]}")


if __name__ == "__main__":
    main()
