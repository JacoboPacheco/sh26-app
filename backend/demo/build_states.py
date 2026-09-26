"""Build a synthetic grid model for every state in the lower 48, validate each, write a manifest.

    backend/venv/Scripts/python backend/demo/build_states.py [--only TX,VA]

Uses build_grid.py (the Florida pipeline) per state on the cached raw CSVs (backend/demo/raw/),
writes backend/demo/grids/<ST>.json, then checks each model the way validate.py checks Florida:
DC flows vs the dataset's own solved flows, a calm base case, and whether a 500 MW drop overloads
something. Florida keeps its committed, hand-validated backend/demo/florida_grid.json.
The manifest backend/demo/grids/index.json is what the backend serves as /api/regions.

A state is one interconnect's zones in that state: Texas = ERCOT (the Texas interconnect), New
Mexico and Montana = their Western-interconnect zones (the AC grids can't be joined across).
"""

import argparse
import csv
import json
import os
import random
import subprocess
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
from powerflow import Grid  # noqa: E402

OUT = os.path.join(HERE, "grids")

STATES = {
    "AL": ("Alabama", [24]), "AZ": ("Arizona", [209]), "AR": ("Arkansas", [42]), "CA": ("California", [203, 204, 205, 206, 207]),
    "CO": ("Colorado", [212]), "CT": ("Connecticut", [6]), "DE": ("Delaware", [12]), "FL": ("Florida", [21, 22, 23]),
    "GA": ("Georgia", [19, 20]), "ID": ("Idaho", [214]), "IL": ("Illinois", [34, 35]), "IN": ("Indiana", [33]),
    "IA": ("Iowa", [39]), "KS": ("Kansas", [48]), "KY": ("Kentucky", [27]), "LA": ("Louisiana", [43]),
    "ME": ("Maine", [1]), "MD": ("Maryland", [13]), "MA": ("Massachusetts", [4]), "MI": ("Michigan", [31, 32]),
    "MN": ("Minnesota", [37, 38]), "MS": ("Mississippi", [25]), "MO": ("Missouri", [40, 41]), "MT": ("Montana", [215]),
    "NE": ("Nebraska", [49]), "NV": ("Nevada", [208]), "NH": ("New Hampshire", [2]), "NJ": ("New Jersey", [9]),
    "NM": ("New Mexico", [211]), "NY": ("New York", [7, 8]), "NC": ("North Carolina", [16, 17]), "ND": ("North Dakota", [51]),
    "OH": ("Ohio", [29, 30]), "OK": ("Oklahoma", [47]), "OR": ("Oregon", [202]), "PA": ("Pennsylvania", [10, 11]),
    "RI": ("Rhode Island", [5]), "SC": ("South Carolina", [18]), "SD": ("South Dakota", [50]), "TN": ("Tennessee", [26]),
    "TX": ("Texas", "Texas"), "UT": ("Utah", [210]), "VT": ("Vermont", [3]), "VA": ("Virginia", [14, 15]),
    "WA": ("Washington", [201]), "WV": ("West Virginia", [28]), "WI": ("Wisconsin", [36]), "WY": ("Wyoming", [213]),
}


def interconnect_of(zones) -> str:
    if zones == "Texas":
        return "Texas (ERCOT)"
    return "Western" if min(zones) >= 200 else "Eastern"


def validate(path: str) -> dict:
    g = Grid.from_file(path)
    st = g.base
    m = np.abs(g.pf_ref) > 1.0
    corr = float(np.corrcoef(st.flow[m], g.pf_ref[m])[0, 1]) if m.sum() > 2 else float("nan")
    rng = random.Random(11)
    hits = 0
    for _ in range(20):
        bus = g.connect_bus(rng.randrange(len(g.sub_ids)))
        if g.overloaded(g.whatif(bus, 500)):
            hits += 1
    hb = g.headroom_by_sub()
    vals = np.array(list(hb.values()))
    return {
        "buses": g.n,
        "subs": len(g.sub_ids),
        "branches": g.m,
        "load_mw": round(float(g.pd.sum())),
        "gen_mw": round(float(g.pmax.sum())),
        "bbox": [round(float(g.sub_lon.min()), 3), round(float(g.sub_lat.min()), 3), round(float(g.sub_lon.max()), 3), round(float(g.sub_lat.max()), 3)],
        "center": [round(float(np.median(g.sub_lon)), 3), round(float(np.median(g.sub_lat)), 3)],
        "corr": round(corr, 3),
        "base_over": int((st.loading_pct > 100).sum()),
        "base_max_pct": round(float(st.loading_pct.max()), 1),
        "base_lost_mw": round(st.lost_mw, 1),
        "overload_500": hits,
        "headroom_p50": round(float(np.median(vals))),
        "headroom_max": round(float(vals.max())),
        "notes": g.meta.get("notes", []),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="", help="comma-separated state codes (default: all)")
    args = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    only = {s.strip().upper() for s in args.only.split(",") if s.strip()}
    index_path = os.path.join(OUT, "index.json")
    index = json.load(open(index_path, encoding="utf-8")) if os.path.exists(index_path) else {"regions": {}}
    for code, (name, zones) in STATES.items():
        if only and code not in only:
            continue
        t = time.time()
        if code == "FL":
            path, rel = os.path.join(HERE, "florida_grid.json"), "florida_grid.json"  # hand-validated, peninsula
        else:
            path, rel = os.path.join(OUT, f"{code}.json"), f"grids/{code}.json"
            sel = ["--interconnect", "Texas"] if zones == "Texas" else ["--zones", ",".join(map(str, zones))]
            r = subprocess.run([sys.executable, os.path.join(HERE, "build_grid.py"), "--no-download", "--out", path, *sel], capture_output=True, text=True)
            if r.returncode != 0:
                print(f"{code}: BUILD FAILED {r.stdout[-300:]} {r.stderr[-300:]}")
                continue
        try:
            v = validate(path)
        except Exception as e:  # noqa: BLE001 — report and keep going
            print(f"{code}: VALIDATE FAILED {e}")
            continue
        ok = v["corr"] >= 0.9 and v["base_over"] <= max(2, 0.02 * v["branches"]) and v["base_lost_mw"] < 1
        index["regions"][code] = {
            "code": code, "name": name, "file": rel, "interconnect": interconnect_of(zones),
            "zones": zones if zones != "Texas" else "Texas interconnect", "valid": ok, **v,
        }
        print(f"{code} {name:15s} {v['buses']:5d} buses {v['subs']:5d} subs | corr {v['corr']:.3f} over {v['base_over']} max {v['base_max_pct']:.0f}% | 500MW {v['overload_500']}/20 | load {v['load_mw']/1000:5.1f} GW | {'OK' if ok else 'CHECK'} ({time.time()-t:.1f}s)")
    index["source"] = ("Breakthrough Energy Sciences U.S. Test System (PowerSimData usa_tamu), from Texas A&M ACTIVSg synthetic grids, CC-BY 4.0. "
                       "Synthetic: no model represents any actual utility's network.")
    index["regions"] = dict(sorted(index["regions"].items()))
    with open(index_path, "w", encoding="utf-8") as fh:
        json.dump(index, fh, indent=1)
    print(f"wrote {index_path}: {len(index['regions'])} regions, {sum(1 for r in index['regions'].values() if r['valid'])} valid")


if __name__ == "__main__":
    main()
