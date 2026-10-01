#!/usr/bin/env python3
"""Merge several PMTiles archives into one, without tippecanoe.

`tile-join` does this, but it means carrying tippecanoe purely to concatenate
four files we already produced, and its PMTiles support is version-sensitive.
The merge itself is small enough to own: PMTiles v3 is a header, a directory of
(tile id -> offset, length) and a run of tile blobs, and the only genuinely
interesting case is a tile id that two inputs both carry.

That happens here by design. Each resolution is tiled across its own zoom band,
and the bands share an edge -- res 3 ends at z4 where res 4 begins, and so on --
so at z4, z6 and z8 two archives hold a tile at the same id. Both are Mapbox
Vector Tiles, and an MVT is a protobuf whose layers are a repeated field, so the
merge of two tiles is the concatenation of their decompressed bytes. That is only
sound because the layer names differ (cells_r3, cells_r4, ...): same-named layers
would end up duplicated in one tile with nothing to tell them apart, which is the
same reason `tile-join` needs distinct names.

Tiles at non-shared zooms -- about 85% of them -- are copied byte for byte with
their gzip intact. Only the overlapping ones are decompressed and re-encoded.

    python3 backend/static/pmtiles_merge.py out.pmtiles in1.pmtiles in2.pmtiles ...
"""

import gzip
import hashlib
import json
import os
import struct
import sys
import tempfile
import time

HEADER_LEN = 127
MAGIC = b"PMTiles"
ROOT_TARGET = 16384          # readers fetch the header and root in one request


# --- varints ----------------------------------------------------------------

def _read_varint(buf, i):
    result = shift = 0
    while True:
        b = buf[i]
        i += 1
        result |= (b & 0x7F) << shift
        if not b & 0x80:
            return result, i
        shift += 7


def _write_varint(out, n):
    while n >= 0x80:
        out.append((n & 0x7F) | 0x80)
        n >>= 7
    out.append(n)


# --- directories ------------------------------------------------------------
# A directory is five columns, each varint-encoded and run end to end: the count,
# then delta-encoded ids, run lengths, lengths, and offsets. An offset of 0 means
# "directly after the previous entry", which is most of them in a clustered file.

def deserialize_dir(buf):
    n, i = _read_varint(buf, 0)
    ids = []
    last = 0
    for _ in range(n):
        d, i = _read_varint(buf, i)
        last += d
        ids.append(last)
    runs = []
    for _ in range(n):
        v, i = _read_varint(buf, i)
        runs.append(v)
    lens = []
    for _ in range(n):
        v, i = _read_varint(buf, i)
        lens.append(v)
    offs = []
    for j in range(n):
        v, i = _read_varint(buf, i)
        offs.append(offs[j - 1] + lens[j - 1] if (v == 0 and j > 0) else v - 1)
    return list(zip(ids, offs, lens, runs))


def serialize_dir(entries):
    out = bytearray()
    _write_varint(out, len(entries))
    last = 0
    for tid, _o, _l, _r in entries:
        _write_varint(out, tid - last)
        last = tid
    for _t, _o, _l, run in entries:
        _write_varint(out, run)
    for _t, _o, ln, _r in entries:
        _write_varint(out, ln)
    for j, (_t, off, _l, _r) in enumerate(entries):
        prev = entries[j - 1] if j else None
        if prev and off == prev[1] + prev[2]:
            _write_varint(out, 0)
        else:
            _write_varint(out, off + 1)
    return bytes(out)


def build_directories(entries):
    """Root + leaves, with the root kept under the size a reader expects."""
    root = gzip.compress(serialize_dir(entries))
    if len(root) <= ROOT_TARGET:
        return root, b"", 0

    leaf_size = 4096
    while True:
        leaf_blob = bytearray()
        root_entries = []
        for s in range(0, len(entries), leaf_size):
            chunk = entries[s:s + leaf_size]
            packed = gzip.compress(serialize_dir(chunk))
            root_entries.append((chunk[0][0], len(leaf_blob), len(packed), 0))
            leaf_blob += packed
        root = gzip.compress(serialize_dir(root_entries))
        if len(root) <= ROOT_TARGET or leaf_size > len(entries):
            return root, bytes(leaf_blob), len(root_entries)
        leaf_size *= 2


# --- reading an archive -----------------------------------------------------

class Archive:
    def __init__(self, path):
        self.path = path
        self.f = open(path, "rb")
        h = self.f.read(HEADER_LEN)
        if h[:7] != MAGIC:
            raise ValueError(f"{path}: not a PMTiles archive")
        if h[7] != 3:
            raise ValueError(f"{path}: PMTiles v{h[7]}, only v3 is handled")
        (self.root_off, self.root_len, self.meta_off, self.meta_len,
         self.leaf_off, self.leaf_len, self.data_off, self.data_len) = \
            struct.unpack("<QQQQQQQQ", h[8:72])
        self.n_addressed, self.n_entries, self.n_contents = struct.unpack("<QQQ", h[72:96])
        self.clustered, self.icomp, self.tcomp, self.ttype = h[96:100]
        self.minzoom, self.maxzoom = h[100], h[101]
        self.bounds = struct.unpack("<iiii", h[102:118])
        self.center_zoom = h[118]
        self.center = struct.unpack("<ii", h[119:127])
        if self.icomp not in (1, 2):
            raise ValueError(f"{path}: internal compression {self.icomp} not handled")

    def _inflate(self, blob):
        return gzip.decompress(blob) if self.icomp == 2 else blob

    def _read(self, off, ln):
        self.f.seek(off)
        return self.f.read(ln)

    def metadata(self):
        return json.loads(self._inflate(self._read(self.meta_off, self.meta_len)))

    def entries(self):
        """Every tile entry, leaves followed in, runs expanded to one id each."""
        out = []
        for tid, off, ln, run in deserialize_dir(self._inflate(self._read(self.root_off, self.root_len))):
            if run == 0:                                  # a pointer to a leaf
                leaf = deserialize_dir(self._inflate(self._read(self.leaf_off + off, ln)))
                out.extend(leaf)
            else:
                out.append((tid, off, ln, run))
        flat = []
        for tid, off, ln, run in out:
            for k in range(max(run, 1)):
                flat.append((tid + k, off, ln))
        return flat

    def tile(self, off, ln):
        return self._read(self.data_off + off, ln)


def zoom_of(tile_id):
    z = 0
    while tile_id >= (4 ** (z + 1) - 1) // 3:
        z += 1
    return z


# --- the merge --------------------------------------------------------------

def merge(out_path, in_paths, verbose=True):
    archives = [Archive(p) for p in in_paths]
    tcomp = archives[0].tcomp
    ttype = archives[0].ttype
    for a in archives[1:]:
        if a.tcomp != tcomp or a.ttype != ttype:
            raise ValueError(f"{a.path}: tile type/compression differs from {in_paths[0]}")
    if ttype != 1:
        raise ValueError("only MVT archives can be merged this way")

    # tile id -> [(archive, offset, length), ...], in input order
    index = {}
    for a in archives:
        n = 0
        for tid, off, ln in a.entries():
            index.setdefault(tid, []).append((a, off, ln))
            n += 1
        if verbose:
            print(f"  {os.path.basename(a.path):26} {n:>8,} tile(s)  z{a.minzoom}-{a.maxzoom}  "
                  f"layer(s): {', '.join(v['id'] for v in a.metadata().get('vector_layers', []))}")

    ids = sorted(index)
    shared = sum(1 for t in ids if len(index[t]) > 1)
    if verbose:
        print(f"\n  {len(ids):,} distinct tile(s); {shared:,} carried by more than one archive")

    # Scratch, not written beside the output: the tile bytes are staged before the
    # header can be written (offsets are not known until then), and a synced or
    # permissioned folder may well refuse to let us delete the file afterwards.
    fd, tmp = tempfile.mkstemp(suffix=".pmtiles.tmp")
    os.close(fd)
    entries = []
    seen = {}                       # content hash -> (offset, length)
    written = deduped = 0
    t0 = time.time()

    with open(tmp, "wb") as data:
        pos = 0
        for tid in ids:
            srcs = index[tid]
            if len(srcs) == 1:
                a, off, ln = srcs[0]
                blob = a.tile(off, ln)          # untouched, gzip and all
            else:
                # Concatenating decompressed MVTs unions their layers.
                raw = b"".join(
                    gzip.decompress(a.tile(off, ln)) if a.tcomp == 2 else a.tile(off, ln)
                    for a, off, ln in srcs)
                blob = gzip.compress(raw, 6) if tcomp == 2 else raw

            key = hashlib.sha256(blob).digest()
            hit = seen.get(key)
            if hit is not None:
                entries.append((tid, hit[0], hit[1], 1))
                deduped += 1
                continue
            data.write(blob)
            seen[key] = (pos, len(blob))
            entries.append((tid, pos, len(blob), 1))
            pos += len(blob)
            written += 1

    if verbose:
        print(f"  {written:,} tile blob(s) written, {deduped:,} deduplicated  "
              f"({pos / 1e6:.1f} MB, {time.time() - t0:.1f}s)")

    # Neighbouring entries pointing at the same blob collapse into one run.
    packed = []
    for tid, off, ln, _run in entries:
        if packed:
            ptid, poff, pln, prun = packed[-1]
            if poff == off and pln == ln and ptid + prun == tid:
                packed[-1] = (ptid, poff, pln, prun + 1)
                continue
        packed.append((tid, off, ln, 1))

    meta = merged_metadata(archives)
    meta_blob = gzip.compress(json.dumps(meta, separators=(",", ":")).encode())
    root, leaves, n_leaf = build_directories(packed)

    root_off = HEADER_LEN
    meta_off = root_off + len(root)
    leaf_off = meta_off + len(meta_blob)
    data_off = leaf_off + len(leaves)

    minz = min(a.minzoom for a in archives)
    maxz = max(a.maxzoom for a in archives)
    b = [min(a.bounds[0] for a in archives), min(a.bounds[1] for a in archives),
         max(a.bounds[2] for a in archives), max(a.bounds[3] for a in archives)]

    header = bytearray(MAGIC + bytes([3]))
    header += struct.pack("<QQQQQQQQ", root_off, len(root), meta_off, len(meta_blob),
                          leaf_off, len(leaves), data_off, pos)
    header += struct.pack("<QQQ", len(entries), len(packed), written)
    header += bytes([1, 2, tcomp, ttype, minz, maxz])       # clustered, gzip dirs
    header += struct.pack("<iiii", *b)
    header += bytes([minz])
    header += struct.pack("<ii", (b[0] + b[2]) // 2, (b[1] + b[3]) // 2)
    assert len(header) == HEADER_LEN, len(header)

    with open(out_path, "wb") as out:
        out.write(header)
        out.write(root)
        out.write(meta_blob)
        out.write(leaves)
        with open(tmp, "rb") as data:
            while True:
                chunk = data.read(8 << 20)
                if not chunk:
                    break
                out.write(chunk)
    try:
        os.remove(tmp)
    except OSError:
        pass

    if verbose:
        print(f"  directory: {len(packed):,} entr(ies), root {len(root):,} B, "
              f"{n_leaf} leaf director(ies)")
        print(f"\n  {out_path}  {os.path.getsize(out_path) / 1e6:.1f} MB  "
              f"z{minz}-{maxz}  {time.time() - t0:.1f}s")
    return out_path


def merged_metadata(archives):
    layers, out = [], None
    for a in archives:
        m = a.metadata()
        if out is None:
            out = dict(m)
        layers.extend(m.get("vector_layers", []))
    out["vector_layers"] = layers
    out.pop("tilestats", None)          # per-archive, and nothing reads it here
    out["minzoom"] = min(a.minzoom for a in archives)
    out["maxzoom"] = max(a.maxzoom for a in archives)
    out["name"] = os.path.splitext(os.path.basename(archives[0].path))[0].split(".")[0]
    return out


if __name__ == "__main__":
    if len(sys.argv) < 4:
        sys.exit(__doc__.strip().splitlines()[-1].strip())
    merge(sys.argv[1], sys.argv[2:])
