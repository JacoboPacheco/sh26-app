"""Milestone 0 gate (SPEC.md → Milestones): is the DC power flow on the built grid trustworthy?

    backend/venv/Scripts/python backend/demo/validate.py [backend/demo/florida_grid.json]

Prints PASS/FAIL against the spec thresholds and, on the seeded Orlando site, writes
backend/demo/expected_whatif.json — the only place that file is ever regenerated.

Pass: correlation(DC base flows, dataset Pf) >= 0.9 on internal branches; base-case branches over
100 % <= 2 %; a 500 MW drop at some sampled site produces at least one overload.
"""

from __future__ import annotations

import json
import os
import random
import sys
import time

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from powerflow import Grid  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
SITES = {
    "Orlando": (28.5384, -81.3789, 500),
    "Miami": (25.7617, -80.1918, 1500),
    "Tampa": (27.9506, -82.4572, 500),
    "Jacksonville": (30.3322, -81.6557, 500),
    "Tallahassee": (30.4383, -84.2807, 500),
}


def line(ok: bool, text: str) -> bool:
    print(("PASS  " if ok else "FAIL  ") + text)
    return ok


def main() -> None:
    path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "florida_grid.json")
    t0 = time.time()
    g = Grid.from_file(path)
    print(f"loaded {path}: {g.n} buses, {len(g.sub_ids)} subs, {g.m} branches in {time.time() - t0:.1f} s")
    print("notes:", *g.meta.get("notes", []), sep="\n  ")
    st = g.base
    results = []

    # (a) our DC base case vs the dataset's own solved flows
    ref = g.pf_ref
    mask = np.abs(ref) > 1.0
    corr = float(np.corrcoef(st.flow[mask], ref[mask])[0, 1]) if mask.sum() > 2 else float("nan")
    mape = float(np.median(np.abs(st.flow[mask] - ref[mask]) / np.abs(ref[mask]))) if mask.sum() else float("nan")
    print(f"base case: correlation with dataset Pf = {corr:.3f} on {int(mask.sum())} branches (median rel. error {mape:.2f})")
    results.append(line(corr >= 0.9, "correlation >= 0.9"))

    # (b) a calm base case
    over = st.loading_pct > 100.0
    share = float(over.mean() * 100)
    print(f"base case: {int(over.sum())} of {g.m} branches over 100 % ({share:.2f} %); "
          f"p50 loading {np.percentile(st.loading_pct, 50):.0f} %, p95 {np.percentile(st.loading_pct, 95):.0f} %, max {st.loading_pct.max():.0f} %")
    if over.sum():
        worst = np.argsort(-st.loading_pct)[:5]
        for i in worst:
            print(f"   branch {g.br_ids[i]} {g.br_kv[i]:.0f} kV rate {g.rate[i]:.0f} flow {st.flow[i]:.0f} ({st.loading_pct[i]:.0f} %) ref {ref[i]:.0f}")
    results.append(line(share <= 2.0, "base-case overloads <= 2 % of branches"))
    results.append(line(st.lost_mw < 0.5, f"no load lost in the base case (lost {st.lost_mw:.1f} MW)"))

    # headroom for every substation
    t1 = time.time()
    hb = g.headroom_by_sub()
    vals = np.array(list(hb.values()))
    print(f"headroom for {len(hb)} subs in {time.time() - t1:.1f} s: p10 {np.percentile(vals, 10):.0f} MW, "
          f"p50 {np.percentile(vals, 50):.0f} MW, p90 {np.percentile(vals, 90):.0f} MW; {int((vals < 500).sum())} subs take < 500 MW")

    # (c) named sites
    any_over = False
    expected = None
    for name, (lat, lon, mw) in SITES.items():
        bus = g.site_bus(lat, lon)
        sub = int(g.sub_ids[g.bus_sub_idx[bus]])
        w = g.whatif(bus, mw)
        ov = g.overloaded(w)
        head = g.headroom_bus(bus)
        casc = g.cascade(bus, mw)
        print(f"{name:12s} {mw:5d} MW at sub {sub} ({g.sub_name[g.bus_sub_idx[bus]]}, {g.bus_kv[bus]:.0f} kV): "
              f"{len(ov)} over limit, headroom {head:.0f} MW, cascade {casc['total_steps']} steps -> {casc['outcome']}, "
              f"lost {casc['lost_mw']:.0f} MW (~{casc['homes']:,} homes)")
        if ov:
            any_over = True
        if name == "Orlando":
            expected = {
                "site": name,
                "lat": lat,
                "lon": lon,
                "mw": mw,
                "bus": int(g.bus_ids[bus]),
                "sub": sub,
                "overloaded_ids": [o["id"] for o in ov],
                "headroom_mw": round(head, 2),
                "cascade_steps": casc["total_steps"],
                "cascade_outcome": casc["outcome"],
            }
    # random sample of sites
    rng = random.Random(7)
    hits = 0
    for _ in range(20):
        i = rng.randrange(len(g.sub_ids))
        bus = g.connect_bus(i)
        if g.overloaded(g.whatif(bus, 500)):
            hits += 1
    print(f"500 MW at 20 random substations: {hits} produce at least one overload")
    results.append(line(any_over or hits > 0, "a 500 MW drop somewhere produces at least one overload"))

    if expected is not None:
        out = os.path.join(HERE, "expected_whatif.json")
        with open(out, "w", encoding="utf-8") as fh:
            json.dump(expected, fh, indent=2)
        print(f"wrote {out}")

    print("\nMILESTONE 0:", "PASS" if all(results) else "FAIL — see SPEC.md → Milestone 0 for the fallback chain")
    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    main()
