"""Build frontend/src/data/florida_outline.json from the Census Bureau's cartographic boundary file.

    curl -o backend/demo/raw/census/cb_2024_us_state_20m.zip \
        https://www2.census.gov/geo/tiger/GENZ2024/shp/cb_2024_us_state_20m.zip   (~190 KB, public domain)
    (unzip the .shp and .dbf into backend/demo/raw/census/)
    backend/venv/Scripts/python backend/demo/build_outline.py

Reads the shapefile with `struct` (no GIS dependency), keeps Florida's rings, rounds to ~100 m, and
adds Lake Okeechobee as an ellipse (state polygons don't cut out inland water). Run offline, once;
the output is committed and nothing is fetched at runtime.
"""

import json
import math
import struct
from pathlib import Path

HERE = Path(__file__).parent
RAW = HERE / "raw" / "census" / "cb_2024_us_state_20m"
OUT = HERE.parent.parent / "frontend" / "src" / "data" / "florida_outline.json"
# Lake Okeechobee as an ellipse (~40 x 50 km); state polygons don't cut out inland water
LAKE_OKEECHOBEE = [
    [round(-80.83 + 0.2 * math.cos(2 * math.pi * i / 32), 3), round(26.95 + 0.22 * math.sin(2 * math.pi * i / 32), 3)]
    for i in range(32)
]


def dbf_names(path: Path) -> list[str]:
    data = path.read_bytes()
    n_rec, header_len, rec_len = struct.unpack("<IHH", data[4:12])
    fields, pos = [], 32
    while data[pos] != 0x0D:
        name = data[pos : pos + 11].split(b"\0")[0].decode()
        fields.append((name, data[pos + 16]))
        pos += 32
    offset = 1  # the deletion flag
    for name, width in fields:
        if name == "NAME":
            break
        offset += width
    else:
        raise SystemExit("no NAME field in the .dbf")
    return [
        data[header_len + i * rec_len + offset : header_len + i * rec_len + offset + width].decode("latin-1").strip()
        for i in range(n_rec)
    ]


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


def main() -> None:
    names = dbf_names(RAW.with_suffix(".dbf"))
    shapes = shp_polygons(RAW.with_suffix(".shp"))
    rings = shapes[names.index("Florida")]
    land = []
    for ring in rings:
        pts = []
        for lon, lat in ring:
            p = [round(lon, 3), round(lat, 3)]
            if not pts or p != pts[-1]:
                pts.append(p)
        if len(pts) >= 4:
            land.append(pts)
    land.sort(key=len, reverse=True)
    out = {
        "_source": "U.S. Census Bureau cartographic boundary file cb_2024_us_state_20m (public domain), "
        "converted by backend/demo/build_outline.py; Lake Okeechobee approximated as an ellipse.",
        "land": land,
        "lakes": [LAKE_OKEECHOBEE],
    }
    OUT.write_text(json.dumps(out, separators=(",", ":")), encoding="utf-8")
    print(f"wrote {OUT}: {len(land)} rings, {sum(len(r) for r in land)} points, {OUT.stat().st_size / 1024:.0f} KB")


if __name__ == "__main__":
    main()
