"""Build the map outlines from the Census Bureau's cartographic boundary file:

    frontend/src/data/florida_outline.json   Florida's rings + Lake Okeechobee (the demo's hero map)
    frontend/src/data/states_outline.json    all 48 lower states + DC, simplified, keyed by state code,
                                             with each state's bbox and the national bbox

    curl -o backend/demo/raw/census/cb_2024_us_state_20m.zip \
        https://www2.census.gov/geo/tiger/GENZ2024/shp/cb_2024_us_state_20m.zip   (~190 KB, public domain)
    (unzip the .shp and .dbf into backend/demo/raw/census/)
    backend/venv/Scripts/python backend/demo/build_outline.py

Reads the shapefile with `struct` (no GIS dependency), rounds to ~100 m, simplifies the national
outlines (Douglas-Peucker, ~0.5 km), and adds Lake Okeechobee as an ellipse (state polygons don't
cut out inland water). Run offline, once; the output is committed and nothing is fetched at runtime.
"""

import json
import math
import struct
from pathlib import Path

HERE = Path(__file__).parent
RAW = HERE / "raw" / "census" / "cb_2024_us_state_20m"
DATA = HERE.parent.parent / "frontend" / "src" / "data"
OUT = DATA / "florida_outline.json"
OUT_STATES = DATA / "states_outline.json"
SIMPLIFY_DEG = 0.005  # Douglas-Peucker tolerance for the national outlines (~0.5 km)
SKIP = {"AK", "HI", "PR"}  # the models cover the lower 48 (+ DC, drawn for completeness)
# Lake Okeechobee as an ellipse (~40 x 50 km); state polygons don't cut out inland water
LAKE_OKEECHOBEE = [
    [round(-80.83 + 0.2 * math.cos(2 * math.pi * i / 32), 3), round(26.95 + 0.22 * math.sin(2 * math.pi * i / 32), 3)]
    for i in range(32)
]


def dbf_records(path: Path) -> list[dict[str, str]]:
    data = path.read_bytes()
    n_rec, header_len, rec_len = struct.unpack("<IHH", data[4:12])
    fields, pos = [], 32
    while data[pos] != 0x0D:
        name = data[pos : pos + 11].split(bytes(1))[0].decode()
        fields.append((name, data[pos + 16]))
        pos += 32
    out = []
    for i in range(n_rec):
        rec, off = {}, header_len + i * rec_len + 1  # + the deletion flag
        for name, width in fields:
            rec[name] = data[off : off + width].decode("latin-1").strip()
            off += width
        out.append(rec)
    return out


def dbf_names(path: Path) -> list[str]:
    return [r["NAME"] for r in dbf_records(path)]


def shp_polygons(path: Path) -> list[list[list[tuple[float, float]]]]:
    data = path.read_bytes()
    pos, shapes = 100, []
    while pos < len(data):
        _, words = struct.unpack(">II", data[pos : pos + 8])
        content = data[pos + 8 : pos + 8 + words * 2]
        pos += 8 + words * 2
        if struct.unpack("<i", content[:4])[0] != 5:  # 5 = polygon
            shapes.append([])
            continue
        n_parts, n_points = struct.unpack("<ii", content[36:44])
        parts = list(struct.unpack(f"<{n_parts}i", content[44 : 44 + 4 * n_parts])) + [n_points]
        base = 44 + 4 * n_parts
        pts = [struct.unpack("<dd", content[base + 16 * i : base + 16 * i + 16]) for i in range(n_points)]
        shapes.append([pts[parts[k] : parts[k + 1]] for k in range(n_parts)])
    return shapes


def _dp(pts: list, tol: float) -> list:
    """Douglas-Peucker on a list of [lon, lat] (iterative, keeps both ends)."""
    if len(pts) < 3:
        return pts
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        a, b = stack.pop()
        (x1, y1), (x2, y2) = pts[a], pts[b]
        dx, dy = x2 - x1, y2 - y1
        norm = math.hypot(dx, dy) or 1e-12
        best, idx = 0.0, None
        for k in range(a + 1, b):
            x0, y0 = pts[k]
            d = abs(dy * x0 - dx * y0 + x2 * y1 - y2 * x1) / norm if (dx or dy) else math.hypot(x0 - x1, y0 - y1)
            if d > best:
                best, idx = d, k
        if idx is not None and best > tol:
            keep[idx] = True
            stack += [(a, idx), (idx, b)]
    return [p for p, k in zip(pts, keep) if k]


def rings_of(shape: list, tol: float = 0.0) -> list:
    land = []
    for ring in shape:
        pts = []
        for lon, lat in ring:
            p = [round(lon, 3), round(lat, 3)]
            if not pts or p != pts[-1]:
                pts.append(p)
        if tol:
            pts = _dp(pts, tol)
        if len(pts) >= 4:
            land.append(pts)
    land.sort(key=len, reverse=True)
    return land


def bbox_of(rings: list) -> list:
    xs = [p[0] for r in rings for p in r]
    ys = [p[1] for r in rings for p in r]
    return [min(xs), min(ys), max(xs), max(ys)]


def main() -> None:
    recs = dbf_records(RAW.with_suffix(".dbf"))
    shapes = shp_polygons(RAW.with_suffix(".shp"))
    names = [r["NAME"] for r in recs]
    land = rings_of(shapes[names.index("Florida")])
    out = {
        "_source": "U.S. Census Bureau cartographic boundary file cb_2024_us_state_20m (public domain), "
        "converted by backend/demo/build_outline.py; Lake Okeechobee approximated as an ellipse.",
        "land": land,
        "lakes": [LAKE_OKEECHOBEE],
    }
    OUT.write_text(json.dumps(out, separators=(",", ":")), encoding="utf-8")
    print(f"wrote {OUT}: {len(land)} rings, {sum(len(r) for r in land)} points, {OUT.stat().st_size / 1024:.0f} KB")

    states = {}
    for rec, shape in zip(recs, shapes):
        code = rec["STUSPS"]
        if code in SKIP or not shape:
            continue
        rings = rings_of(shape, SIMPLIFY_DEG)
        # drop specks (tiny islands) so the national map stays light; keep every state's main ring
        rings = [r for i, r in enumerate(rings) if i == 0 or len(r) >= 8]
        states[code] = {"name": rec["NAME"], "bbox": bbox_of(rings), "rings": rings}
    states = dict(sorted(states.items()))
    allb = [s["bbox"] for s in states.values()]
    national = [min(b[0] for b in allb), min(b[1] for b in allb), max(b[2] for b in allb), max(b[3] for b in allb)]
    out = {
        "_source": "U.S. Census Bureau cartographic boundary file cb_2024_us_state_20m (public domain), "
        f"lower 48 + DC, simplified (Douglas-Peucker {SIMPLIFY_DEG} deg) by backend/demo/build_outline.py.",
        "bbox": national,
        "states": states,
    }
    OUT_STATES.write_text(json.dumps(out, separators=(",", ":")), encoding="utf-8")
    pts = sum(len(r) for s in states.values() for r in s["rings"])
    print(f"wrote {OUT_STATES}: {len(states)} states, {pts} points, {OUT_STATES.stat().st_size / 1024:.0f} KB")


if __name__ == "__main__":
    main()
