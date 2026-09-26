"""Build backend/demo/florida_grid.json from Breakthrough Energy's USA test system (CC-BY 4.0).

    backend/venv/Scripts/python backend/demo/build_grid.py                # Florida zones 21,22,23
    backend/venv/Scripts/python backend/demo/build_grid.py --interconnect Texas --out backend/demo/texas_grid.json
    backend/venv/Scripts/python backend/demo/build_grid.py --no-download  # reuse backend/demo/raw/

Source files (raw GitHub, PowerSimData develop branch): bus.csv, branch.csv, sub.csv,
bus2sub.csv, plant.csv, zone.csv. They are cached in backend/demo/raw/ (gitignored); only the
built JSON is committed. Nothing downloads at runtime.

The cut (SPEC.md → Data and honesty): keep the selected buses; keep branches with both ends
inside; a branch with one end outside becomes a fixed injection ("tie") at its inside end equal
to the dataset's own solved flow; keep only the largest connected component; give zero ratings
a default by voltage class; scale generation so the island balances (DC is lossless).
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
import urllib.request
from collections import defaultdict

RAW_BASE = "https://raw.githubusercontent.com/Breakthrough-Energy/PowerSimData/develop/powersimdata/network/usa_tamu/data/"
FILES = ["bus.csv", "branch.csv", "sub.csv", "bus2sub.csv", "plant.csv", "zone.csv"]
HERE = os.path.dirname(os.path.abspath(__file__))
RAW_DIR = os.path.join(HERE, "raw")

SOURCE = (
    "Breakthrough Energy Sciences, U.S. Test System (PowerSimData usa_tamu network), derived from "
    "Texas A&M ACTIVSg synthetic grids. Synthetic: does not represent any actual utility's network."
)
LICENSE = "CC-BY 4.0 — https://zenodo.org/records/3530898 ; https://github.com/Breakthrough-Energy/PowerSimData"

# Default thermal rating (MVA) when the dataset gives 0 (= unlimited), by nominal kV.
DEFAULT_RATE = [(500, 2500.0), (345, 1500.0), (230, 600.0), (161, 400.0), (138, 300.0), (115, 200.0), (69, 100.0)]


def default_rate(kv: float) -> float:
    for lo, rate in DEFAULT_RATE:
        if kv >= lo - 1:
            return rate
    return 100.0


def download(no_download: bool) -> None:
    os.makedirs(RAW_DIR, exist_ok=True)
    for name in FILES:
        path = os.path.join(RAW_DIR, name)
        if os.path.exists(path) and os.path.getsize(path) > 0:
            continue
        if no_download:
            sys.exit(f"missing {path} and --no-download was given")
        url = RAW_BASE + name
        print(f"downloading {url}")
        t0 = time.time()
        req = urllib.request.Request(url, headers={"User-Agent": "sh26-overload-build/1.0"})
        with urllib.request.urlopen(req, timeout=120) as resp, open(path, "wb") as out:
            out.write(resp.read())
        print(f"  {os.path.getsize(path) / 1e6:.1f} MB in {time.time() - t0:.1f} s")


def read_csv(name: str) -> list[dict]:
    with open(os.path.join(RAW_DIR, name), newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def fnum(row: dict, key: str, default: float = 0.0) -> float:
    v = row.get(key)
    if v is None or v == "":
        return default
    try:
        return float(v)
    except ValueError:
        return default


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--zones", default="21,22,23", help="zone_id list to keep (default: Florida)")
    ap.add_argument("--interconnect", default=None, help="keep a whole interconnect instead (e.g. Texas)")
    ap.add_argument("--out", default=os.path.join(HERE, "florida_grid.json"))
    ap.add_argument("--no-download", action="store_true")
    args = ap.parse_args()

    download(args.no_download)
    buses = read_csv("bus.csv")
    branches = read_csv("branch.csv")
    subs = read_csv("sub.csv")
    bus2sub = read_csv("bus2sub.csv")
    plants = read_csv("plant.csv")
    zones = {int(z["zone_id"]): z["zone_name"] for z in read_csv("zone.csv")}
    print(f"read: {len(buses)} buses, {len(branches)} branches, {len(subs)} subs, {len(plants)} plants")
    print("branch columns:", list(branches[0].keys()))

    if args.interconnect:
        keep_bus = {int(b["bus_id"]) for b in buses if b.get("interconnect") == args.interconnect}
        region = f"{args.interconnect} interconnect"
    else:
        zone_ids = {int(z) for z in args.zones.split(",")}
        keep_bus = {int(b["bus_id"]) for b in buses if int(b["zone_id"]) in zone_ids}
        region = ", ".join(zones.get(z, str(z)) for z in sorted(zone_ids))
    print(f"region: {region}: {len(keep_bus)} buses")

    bus_row = {int(b["bus_id"]): b for b in buses if int(b["bus_id"]) in keep_bus}
    sub_of = {int(r["bus_id"]): int(r["sub_id"]) for r in bus2sub if int(r["bus_id"]) in keep_bus}
    sub_row = {int(s["sub_id"]): s for s in subs}

    # generation per bus
    pg = defaultdict(float)
    pmax = defaultdict(float)
    for p in plants:
        bid = int(p["bus_id"])
        if bid not in keep_bus:
            continue
        if "status" in p and fnum(p, "status", 1.0) == 0.0:
            continue
        pg[bid] += fnum(p, "Pg")
        pmax[bid] += max(fnum(p, "Pmax"), fnum(p, "Pg"))

    # branches inside, and ties for branches that cross the cut
    inside, ties = [], defaultdict(float)
    n_default_rate = 0
    for br in branches:
        if "status" in br and fnum(br, "status", 1.0) == 0.0:
            continue
        f, t = int(br["from_bus_id"]), int(br["to_bus_id"])
        fin, tin = f in keep_bus, t in keep_bus
        if fin and tin:
            inside.append(br)
        elif fin:
            ties[f] += -fnum(br, "Pf")  # Pf > 0 flows from f outward: a withdrawal at f
        elif tin:
            ties[t] += -fnum(br, "Pt")  # Pt is the flow at t in the from->to convention: -Pt arrives at t
    print(f"internal branches: {len(inside)}; tie injections at {len(ties)} buses, net import {sum(ties.values()):.0f} MW")

    # largest connected component only
    parent = {b: b for b in keep_bus}

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    for br in inside:
        a, b = find(int(br["from_bus_id"])), find(int(br["to_bus_id"]))
        if a != b:
            parent[a] = b
    comps = defaultdict(list)
    for b in keep_bus:
        comps[find(b)].append(b)
    main_comp = max(comps.values(), key=len)
    main_set = set(main_comp)
    dropped = [c for c in comps.values() if c is not main_comp]
    dropped_load = sum(fnum(bus_row[b], "Pd") for c in dropped for b in c)
    print(f"components: {len(comps)}; keeping the largest ({len(main_comp)} buses); dropping {len(dropped)} islands "
          f"with {sum(len(c) for c in dropped)} buses and {dropped_load:.0f} MW of load")

    out_buses, out_subs, seen_subs = [], [], set()
    for bid in sorted(main_set):
        row = bus_row[bid]
        sid = sub_of.get(bid)
        if sid is None:
            print(f"WARN bus {bid} has no substation; skipped")
            continue
        s = sub_row[sid]
        if sid not in seen_subs:
            seen_subs.add(sid)
            out_subs.append({"id": sid, "name": s.get("name", str(sid)), "lat": float(s["lat"]), "lon": float(s["lon"])})
        out_buses.append(
            {
                "id": bid,
                "sub": sid,
                "kv": fnum(row, "baseKV"),
                "pd": round(fnum(row, "Pd"), 3),
                "pg": round(pg.get(bid, 0.0), 3),
                "pmax": round(pmax.get(bid, 0.0), 3),
                "va_ref": fnum(row, "Va"),
            }
        )
    kept_bus = {b["id"] for b in out_buses}

    out_branches = []
    for br in inside:
        f, t = int(br["from_bus_id"]), int(br["to_bus_id"])
        if f not in kept_bus or t not in kept_bus:
            continue
        kv = max(fnum(bus_row[f], "baseKV"), fnum(bus_row[t], "baseKV"))
        rate = fnum(br, "rateA")
        if rate <= 0:
            rate = default_rate(kv)
            n_default_rate += 1
        out_branches.append(
            {
                "id": int(br.get("branch_id") or len(out_branches) + 1),
                "f": f,
                "t": t,
                "x": fnum(br, "x"),
                "rate": rate,
                "kv": kv,
                "pf_ref": round(fnum(br, "Pf"), 3),
            }
        )
    out_ties = [{"bus": b, "mw": round(mw, 3)} for b, mw in sorted(ties.items()) if b in kept_bus]

    total_pd = sum(b["pd"] for b in out_buses)
    total_pg = sum(b["pg"] for b in out_buses)
    total_tie = sum(t["mw"] for t in out_ties)
    print(f"load {total_pd:.0f} MW, generation {total_pg:.0f} MW, ties {total_tie:+.0f} MW, "
          f"mismatch (≈ AC losses) {total_pg + total_tie - total_pd:+.0f} MW; {n_default_rate} branches got a default rating")

    data = {
        "meta": {
            "source": SOURCE,
            "license": LICENSE,
            "synthetic": True,
            "region": region,
            "built_at": time.strftime("%Y-%m-%d %H:%M"),
            "notes": [
                f"{len(dropped)} disconnected islands dropped ({dropped_load:.0f} MW)",
                f"{n_default_rate} branches with rating 0 given a default by kV class",
                f"net tie import {total_tie:+.0f} MW held fixed; generation dispatched to balance",
            ],
        },
        "buses": out_buses,
        "subs": out_subs,
        "branches": out_branches,
        "ties": out_ties,
    }
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(data, fh, separators=(",", ":"))
    print(f"wrote {args.out}: {len(out_buses)} buses, {len(out_subs)} subs, {len(out_branches)} branches, "
          f"{os.path.getsize(args.out) / 1e6:.2f} MB")


if __name__ == "__main__":
    main()
