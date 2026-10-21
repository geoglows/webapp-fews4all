"""Writing a delivered table, once, for every step that delivers one.

There was a copy of write_csv in each of steps 2 to 5, byte for byte identical,
and each of them streamed its rows straight into the destination. That is the one
shape of failure this pipeline cannot see: a CSV truncated halfway through parses
perfectly well as a shorter CSV. An interrupted step 2 does not leave a corrupt
file for step 3 to reject — it leaves half a world of forecasts that every step
downstream accepts without complaint, and the map just looks like a quiet day.

So the rows go to a temp file beside the target and the target is moved into place
with os.replace, which is atomic within a filesystem. A reader sees the whole
previous file or the whole new one, never a partial.

Four copies of a rule like that is three too many: the next person to improve one
of them improves one of four, and the other three keep the old behaviour in the
steps nobody was looking at.
"""

import csv
import os


def write_csv(path, columns, rows):
    """Write `rows` under `columns` to `path` atomically. Returns the byte size."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(columns)
        w.writerows(rows)
    os.replace(tmp, path)
    return os.path.getsize(path)


def write_text(path, text):
    """The same bargain for a non-CSV artifact (GeoJSON, JSON, a pointer)."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)
    return os.path.getsize(path)
