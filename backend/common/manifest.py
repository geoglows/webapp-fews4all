"""The run record — what a release actually contains, and what it is missing.

Written by step 2 into the release folder as run.json, and appended to by the
later steps. It exists because a live pipeline's hardest question is asked after
the fact: the map looks thin this morning, so was it a quiet day, did a feed
arrive truncated, or did a file simply never land? Without a record, every one of
those looks identical a week later.

It carries, per model: the file that was read and when that file was written, the
issue date and where that date came from, how many forecasts were flagged and how
many survived to the grid, and a status with the reasons behind it.

It is also the baseline. "Far fewer forecasts than usual" needs a definition of
usual, and the only honest source for that is what this pipeline itself published
on the preceding days — not a number somebody typed into config once.

Reading a manifest is best-effort by design. A missing or malformed one costs the
volume check its baseline, which degrades that check to silence; it must never
cost the run.
"""

import json
import os
import re
import statistics
import time

from . import config as C


def path_for(release_dir):
    return os.path.join(release_dir, C.MANIFEST_NAME)


def blank(release):
    return {
        "release": release,
        "generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "steps": {},
        "models": {},
        "notes": [],
    }


def write(release_dir, data):
    """Atomically, so a crash mid-write cannot leave an unparseable record."""
    os.makedirs(release_dir, exist_ok=True)
    p = path_for(release_dir)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp, p)
    return p


def read(release_dir):
    try:
        with open(path_for(release_dir), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def update(release_dir, **fields):
    """Merge fields into an existing manifest, or start one. Never raises."""
    try:
        data = read(release_dir) or blank(os.path.basename(release_dir))
        for k, v in fields.items():
            if isinstance(v, dict) and isinstance(data.get(k), dict):
                data[k].update(v)
            else:
                data[k] = v
        write(release_dir, data)
        return data
    except Exception as e:                      # a record is not worth a run
        print(f"  note: could not update {C.MANIFEST_NAME} ({e})", flush=True)
        return None


def recent(output_dir=None, limit=None, exclude=None):
    """The last few manifests, newest first. Used only for the volume baseline."""
    root = output_dir or C.OUTPUT_DIR
    limit = limit or C.MANIFEST_BASELINE_N
    if not os.path.isdir(root):
        return []
    names = sorted((n for n in os.listdir(root)
                    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", n)
                    and os.path.isdir(os.path.join(root, n))), reverse=True)
    out = []
    for n in names:
        if exclude and n == exclude:
            continue
        m = read(os.path.join(root, n))
        if m:
            out.append(m)
        if len(out) >= limit:
            break
    return out


def baseline(model, output_dir=None, exclude=None):
    """The median flagged count this model has delivered recently, or None.

    Median rather than mean: one truncated morning in the window should not drag
    the yardstick down and quietly excuse the next one. None when there is not
    enough history to say anything, which is the honest answer on a new install
    and is treated as "no opinion" rather than as zero.
    """
    counts = []
    for m in recent(output_dir, exclude=exclude):
        row = (m.get("models") or {}).get(model) or {}
        n = row.get("flagged")
        # Only days the model was actually trusted set the expectation.
        if isinstance(n, int) and row.get("status") in (C.MODEL_OK, C.MODEL_THIN):
            counts.append(n)
    if len(counts) < 3:
        return None
    return statistics.median(counts)


def all_releases(output_dir=None):
    """Every release folder, oldest first. Names only."""
    root = output_dir or C.OUTPUT_DIR
    if not os.path.isdir(root):
        return []
    return sorted(n for n in os.listdir(root)
                  if re.fullmatch(r"\d{4}-\d{2}-\d{2}", n)
                  and os.path.isdir(os.path.join(root, n)))


def _row(release, root):
    """One release, reduced to what a time slider needs.

    Deliberately small: this file is fetched on every page load and grows by a
    row a day, so it carries counts and statuses rather than anything a release
    folder can answer for itself.
    """
    data = read(os.path.join(root, release)) or {}
    models = data.get("models") or {}
    steps = data.get("steps") or {}
    row = {
        "release": release,
        "models": {m: (r.get("status") or C.MODEL_SKIPPED) for m, r in models.items()},
        "flagged": {m: r.get("flagged") for m, r in models.items()
                    if isinstance(r.get("flagged"), int)},
    }
    # Which models are NOT in this release, named rather than inferred, so the
    # slider can mark the day without re-deriving the rule.
    row["missing"] = sorted(m for m, st in row["models"].items()
                            if st in (C.MODEL_SKIPPED, C.MODEL_DROPPED))
    three = steps.get("step3_compare") or {}
    if three.get("by_res"):
        row["cells"] = three["by_res"]
    if three.get("agreement"):
        row["agreement"] = three["agreement"]
    if not models:
        # A folder with no run record at all: published before manifests existed,
        # or a half-built one. Listed, because the tables may still be fine, but
        # marked so nothing claims to know what was in it.
        row["unknown"] = True
    return row


def index(output_dir=None, current=None):
    root = output_dir or C.OUTPUT_DIR
    releases = all_releases(root)
    return {
        "generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "current": current or (releases[-1] if releases else None),
        "releases": [_row(r, root) for r in releases],
    }


def write_index(output_dir=None, current=None):
    """Rebuilt from scratch each time rather than appended to.

    A release can be rerun and change, and folders can be pruned, so an appended
    index would drift from what is actually on disk. Rebuilding costs one small
    JSON read per release and is always right.
    """
    root = output_dir or C.OUTPUT_DIR
    try:
        data = index(root, current)
        os.makedirs(root, exist_ok=True)
        p = os.path.join(root, C.RELEASE_INDEX)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=True)
            f.write("\n")
        os.replace(tmp, p)
        return data
    except Exception as e:                      # an index is not worth a run
        print(f"  note: could not write {C.RELEASE_INDEX} ({e})")
        return None


def summarise(data):
    """One line per model, for the end of a run."""
    out = []
    for name, row in sorted((data.get("models") or {}).items()):
        bits = [f"{row.get('status', '?'):<8}"]
        if row.get("flagged") is not None:
            bits.append(f"{row['flagged']:>7,} flagged")
        if row.get("issued"):
            bits.append(f"issued {row['issued']}")
        for note in row.get("notes") or []:
            bits.append(f"— {note}")
        out.append(f"    {name:<10} " + "  ".join(bits))
    return "\n".join(out)
