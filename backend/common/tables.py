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
import json
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


def table(columns, rows):
    """A table as JSON, column names once rather than once per row.

    `{"columns": [...], "rows": [[...], ...]}` instead of a list of objects.

    This is the whole reason the published tables could move to JSON without
    costing the map much. Measured on the 2026-11-05 release:

                        csv      columnar        records
        cells         0.51 MB   0.76 MB (+49%)  2.11 MB (+316%)
        forecasts     0.58 MB   0.81 MB (+39%)  2.05 MB (+250%)
        cell_index    2.48 MB   2.88 MB (+16%)  4.51 MB  (+82%)

    Records repeat every key on every row, which on the cells table is three
    times the bytes for the same answer. This shape pays only for brackets and
    quotes -- and once gzipped, which is how any server or bucket sends it, the
    whole difference against CSV falls to 6-10%. That is the number that matters,
    and it is small enough that "one file per day instead of two" wins.

    Values stay as the strings the pipeline produced. Casting here would mean
    deciding what every column IS, in a module that deliberately knows nothing
    about any of them -- and a blank would have to become null or 0, which are
    the two things the impact columns go out of their way to tell apart.
    """
    return {"columns": list(columns), "rows": [list(r) for r in rows]}


def write_json(path, obj):
    """Write `obj` as JSON atomically, by the same bargain as write_csv.

    Separators without spaces: on a 15,000-row table the default ", " and ": "
    are a few hundred kilobytes of nothing, and this file is fetched by a browser.
    """
    return write_text(path, json.dumps(obj, separators=(",", ":"), sort_keys=False))


def read_json(path, default=None):
    """Read JSON back, or `default` if it is missing or unreadable.

    Never raises. A step that cannot read an optional companion file should carry
    on and say so, not end the morning.
    """
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


# ---- reading a big static table ---------------------------------------------

def table_path(path):
    """The parquet beside a .csv path if there is one, else the path given.

    Lets a caller keep naming the table it wants without knowing which format is
    on disk today -- and lets a machine that has not been converted yet keep
    working off the CSV. Returns whichever exists, preferring parquet.
    """
    if path.lower().endswith(".csv"):
        pq = path[:-4] + ".parquet"
        if os.path.exists(pq):
            return pq
    return path


def columns_of(path, names=None):
    """{name: list of values} for a static table, parquet or CSV.

    COLUMNS, not rows. Every one of these tables is read the same way -- pull two
    or three fields out of several million rows and build a lookup -- and a dict
    per row is the expensive part of doing that: a 12.5M-row crosswalk is 12.5M
    throwaway objects. Parallel lists cost one object per COLUMN.

    pyarrow reads both formats and is the reason this is worth doing at all. On
    the 449 MB crosswalk it parses the CSV in 0.6s against 3.0s for the stdlib
    reader, and the parquet in 0.8s from 69 MB of disk. Converting buys the disk
    and the types; using pyarrow at all buys most of the speed.

    Values come back as Python objects of whatever type the file carries --
    parquet keeps its types, CSV gets pyarrow's inference. A caller that needs
    strings should say so by asking for them.

    USE columns_where INSTEAD when you only want some of the rows. This builds a
    Python object for every value in every column asked for, which on the 12.5M
    row crosswalk is gigabytes -- worse than the row-at-a-time CSV reader it
    replaced, which at least threw each row away. This is for the tables that are
    genuinely wanted whole, like the stream topology.
    """
    import pyarrow.parquet as pq
    import pyarrow.csv as pacsv

    path = table_path(path)
    if path.lower().endswith(".parquet"):
        t = pq.read_table(path, columns=list(names) if names else None)
    else:
        t = pacsv.read_csv(path)
        if names:
            t = t.select([n for n in names if n in t.column_names])
    return {name: t[name].to_pylist() for name in t.column_names}


def columns_where(path, names, key, values):
    """columns_of, but only the rows whose `key` is in `values`.

    The filter happens inside Arrow, before anything becomes a Python object. That
    is the whole point: these tables are millions of rows and a release touches
    thousands of them, so materialising the other 99% is the expensive part of
    the job and it is pure waste.

    It is also what makes parquet worth having here rather than just a smaller
    CSV -- the column chunks that cannot contain a wanted value are skipped
    without being decompressed.
    """
    import pyarrow as pa
    import pyarrow.compute as pc
    import pyarrow.parquet as pq
    import pyarrow.csv as pacsv

    path = table_path(path)
    cols = list(dict.fromkeys([key] + list(names)))
    if path.lower().endswith(".parquet"):
        t = pq.read_table(path, columns=cols)
    else:
        t = pacsv.read_csv(path).select([c for c in cols if c])

    want = pa.array(list(values))
    # Ids are written as floats in places, so a string set will not match an int
    # column and the other way round. Compare as strings when the types differ --
    # it costs a cast and saves a silent empty result.
    k = t[key]
    if not pa.types.is_string(k.type) and pa.types.is_string(want.type):
        k = pc.cast(k, pa.string())
    elif pa.types.is_string(k.type) and not pa.types.is_string(want.type):
        want = pc.cast(want, pa.string())
    t = t.filter(pc.is_in(k, value_set=want))
    return {name: t[name].to_pylist() for name in t.column_names}
