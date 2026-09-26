"""Build backend/demo/plants/<ST>.json — every power plant in each state model, for Plant Down.

    backend/venv/Scripts/python backend/demo/build_plants.py [--only FL,TX]

Reads the cached raw plant list (backend/demo/raw/plant.csv, Breakthrough Energy's U.S. Test
System, CC-BY 4.0 — synthetic plants, not real ones) and, for every region in grids/index.json,
keeps the plants whose bus is in that region's committed model. Offline units (status 0) are left
out: build_grid.py leaves them out of the buses' generation too.

A "plant" here is every online unit of one fuel at one substation (e.g. "HOMESTEAD 20 nuclear"):
one glyph on the map, one line in a list. `parts` keeps the exact per-bus amounts, so removing a
plant subtracts precisely what build_grid.py added to each bus (Pg, and max(Pmax, Pg) as Pmax) —
the check at the end proves the parts add back up to every bus's generation in the model.

`cost` is the dataset's own synthetic fuel cost times its heat-rate slope ($/MWh, capacity-
weighted) — a rough merit-order number, never a real plant's cost.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
RAW = os.path.join(HERE, "raw", "plant.csv")
OUT = os.path.join(HERE, "plants")

FUELS = {
    "ng": "gas",
    "coal": "coal",
    "nuclear": "nuclear",
    "dfo": "oil",
    "solar": "solar",
    "wind": "wind",
    "wind_offshore": "offshore wind",
    "hydro": "hydro",
    "geothermal": "geothermal",
    "other": "other",
}

SOURCE = (
    "Plants: Breakthrough Energy Sciences U.S. Test System (PowerSimData usa_tamu plant.csv), from Texas A&M "
    "ACTIVSg synthetic grids, CC-BY 4.0. Synthetic: each plant sits on the model's synthetic buses and is named after "
    "its substation; every result describes the model, not any real plant or utility."
)


def fnum(row: dict, key: str) -> float:
    try:
        return float(row.get(key) or 0.0)
    except ValueError:
        return 0.0


def area_of(name: str) -> str:
    """Same rule as powerflow.area_of: "NAPLES 12" -> "Naples"."""
    base = re.sub(r"\s+\d+$", "", str(name or "")).strip().lower()
    return re.sub(r"\b\w", lambda m: m.group(0).upper(), base)


def build_region(code: str, region: dict, rows: list[dict]) -> tuple[dict, int]:
    path = os.path.join(HERE, region["file"])
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    bus = {int(b["id"]): b for b in data["buses"]}
    sub = {int(s["id"]): s for s in data["subs"]}

    groups: dict[tuple[int, str], list[dict]] = defaultdict(list)
    for r in rows:
        bid = int(r["bus_id"])
        if bid not in bus or fnum(r, "status") == 0.0:
            continue
        fuel = FUELS.get(r["type"], "other")
        groups[(int(bus[bid]["sub"]), fuel)].append(r)

    plants = []
    for (sid, fuel), units in groups.items():
        parts: dict[int, list[float]] = defaultdict(lambda: [0.0, 0.0])
        cost_w = cost_sum = 0.0
        for u in units:
            pg, pmax = fnum(u, "Pg"), max(fnum(u, "Pmax"), fnum(u, "Pg"))
            p = parts[int(u["bus_id"])]
            p[0] += pg
            p[1] += pmax
            c = fnum(u, "GenFuelCost") * fnum(u, "GenIOB")
            cost_sum += c * pmax
            cost_w += pmax
        part_list = sorted(([b, round(v[0], 3), round(v[1], 3)] for b, v in parts.items()), key=lambda p: -p[2])
        pmax_total = sum(p[2] for p in part_list)
        s = sub[sid]
        plants.append(
            {
                "id": min(int(u["plant_id"]) for u in units),
                "name": f"{s.get('name', sid)} {fuel}",
                "bus": part_list[0][0],  # the bus with the most capacity
                "sub": sid,
                "sub_name": s.get("name", str(sid)),
                "area": area_of(s.get("name", "")),
                "lat": round(float(s["lat"]), 4),
                "lon": round(float(s["lon"]), 4),
                "fuel": fuel,
                "pmax": round(pmax_total, 1),
                "pg": round(sum(p[1] for p in part_list), 1),
                "cost": round(cost_sum / cost_w, 1) if cost_w > 0 else 0.0,
                "units": len(units),
                "parts": part_list,  # [bus id, Pg, Pmax] exactly as the model's buses carry them
            }
        )
    plants.sort(key=lambda p: (-p["pmax"], p["id"]))

    # The parts must add back up to each bus's generation in the model, or a trip would be wrong.
    pg_sum, pmax_sum = defaultdict(float), defaultdict(float)
    for p in plants:
        for b, pg, pmax in p["parts"]:
            pg_sum[b] += pg
            pmax_sum[b] += pmax
    bad = sum(1 for b in bus.values() if abs(float(b.get("pg", 0)) - pg_sum.get(int(b["id"]), 0.0)) > 0.01
              or abs(float(b.get("pmax", 0)) - pmax_sum.get(int(b["id"]), 0.0)) > 0.01)
    return {"region": code, "source": SOURCE, "plants": plants}, bad


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="", help="comma-separated state codes (default: every region)")
    args = ap.parse_args()
    if not os.path.exists(RAW):
        sys.exit(f"missing {RAW} — run build_grid.py once to download the raw CSVs")
    with open(RAW, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    with open(os.path.join(HERE, "grids", "index.json"), encoding="utf-8") as fh:
        regions = json.load(fh)["regions"]
    only = {s.strip().upper() for s in args.only.split(",") if s.strip()}
    os.makedirs(OUT, exist_ok=True)
    total_bytes = total_plants = 0
    failed = []
    for code, region in regions.items():
        if only and code not in only:
            continue
        out, bad = build_region(code, region, rows)
        path = os.path.join(OUT, f"{code}.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(out, fh, separators=(",", ":"))
        size = os.path.getsize(path)
        total_bytes += size
        total_plants += len(out["plants"])
        cap = sum(p["pmax"] for p in out["plants"])
        print(f"{code}: {len(out['plants']):4d} plants, {cap / 1000:6.1f} GW, {size / 1000:6.1f} KB"
              f"{'' if not bad else f'  MISMATCH on {bad} buses'}")
        if bad:
            failed.append(code)
    print(f"wrote {total_plants} plants, {total_bytes / 1e6:.2f} MB")
    if failed:
        sys.exit(f"generation mismatch in {', '.join(failed)}")


if __name__ == "__main__":
    main()
