"""Minimal MVT reader: feature properties for one layer, no geometry."""
def _fields(buf):
    i = 0
    while i < len(buf):
        k = 0; s = 0
        while True:
            b = buf[i]; i += 1; k |= (b & 0x7F) << s
            if not b & 0x80: break
            s += 7
        fn, wt = k >> 3, k & 7
        if wt == 2:
            ln = 0; s = 0
            while True:
                b = buf[i]; i += 1; ln |= (b & 0x7F) << s
                if not b & 0x80: break
                s += 7
            yield fn, buf[i:i+ln]; i += ln
        elif wt == 0:
            v = 0; s = 0
            while True:
                b = buf[i]; i += 1; v |= (b & 0x7F) << s
                if not b & 0x80: break
                s += 7
            yield fn, v
        elif wt == 5: yield fn, int.from_bytes(buf[i:i+4], "little"); i += 4
        elif wt == 1: yield fn, int.from_bytes(buf[i:i+8], "little"); i += 8
        else: raise ValueError(wt)

def _varints(buf):
    i = 0; out = []
    while i < len(buf):
        v = 0; s = 0
        while True:
            b = buf[i]; i += 1; v |= (b & 0x7F) << s
            if not b & 0x80: break
            s += 7
        out.append(v)
    return out

def _value(buf):
    import struct
    for fn, v in _fields(buf):
        if fn == 1: return v.decode("utf-8", "replace")
        if fn == 2: return struct.unpack("<f", v.to_bytes(4, "little"))[0]
        if fn == 3: return struct.unpack("<d", v.to_bytes(8, "little"))[0]
        if fn in (4, 5): return v
        if fn == 6: return (v >> 1) ^ -(v & 1)
        if fn == 7: return bool(v)
    return None

def layer_properties(tile, want_layer):
    """[{prop: value}] for every feature in `want_layer`."""
    out = []
    for fn, val in _fields(tile):
        if fn != 3: continue
        name = None; keys = []; vals = []; feats = []
        for lfn, lval in _fields(val):
            if lfn == 1: name = lval.decode()
            elif lfn == 2: feats.append(lval)
            elif lfn == 3: keys.append(lval.decode())
            elif lfn == 4: vals.append(_value(lval))
        if name != want_layer: continue
        for fbuf in feats:
            tags = []
            for ffn, fval in _fields(fbuf):
                if ffn == 2: tags = _varints(fval) if isinstance(fval, bytes) else [fval]
            props = {}
            for i in range(0, len(tags) - 1, 2):
                ki, vi = tags[i], tags[i+1]
                if ki < len(keys) and vi < len(vals): props[keys[ki]] = vals[vi]
            out.append(props)
    return out
