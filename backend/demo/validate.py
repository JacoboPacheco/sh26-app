"""Milestone 0 gate (SPEC.md → Milestones): is the DC power flow on the built grid trustworthy?

    backend/venv/Scripts/python backend/demo/validate.py [backend/demo/florida_grid.json]
    backend/venv/Scripts/python backend/demo/validate.py --all     # every state model -> validation.json

Prints PASS/FAIL against the spec thresholds and, on the seeded Orlando site, writes
backend/demo/expected_whatif.json — the only place that file is ever regenerated.

Pass: correlation(DC base flows, dataset Pf) >= 0.9 on internal branches; base-case branches over
100 % <= 2 %; a 500 MW drop at some sampled site produces at least one overload.

--all runs the flow comparison (a) and the calm-base-case check (b) on all 48 committed state models
(Florida's is florida_grid.json) and writes backend/demo/validation.json, which GET /api/ai/status and
GET /api/ai/validation serve to the "How we know it's right" card. It never touches expected_whatif.json.
What the comparison is: our DC power flow (lossless, steady state; the dataset's loads, its generator dispatch
and its flows on the border-crossing lines held fixed, generation scaled only so each island balances) against
the branch flows in the dataset's own published solved base case (its Pf
column, an AC solution: it carries reactive power and losses). A synthetic grid: agreement with the dataset's
solution, never with any real utility's network. Offline and fast (well under a minute for all 48).
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
VALIDATION_OUT = os.path.join(HERE, "validation.json")
CORR_MIN = 0.9  # the Milestone 0 threshold
REF_MIN_MW = 1.0  # branches the dataset's solution gives under 1 MW are left out (as in Milestone 0): a relative gap there means nothing
# The demo's hero site (CLAUDE.md → Decisions, Sat 00:58): cascades at HERO_MW, calm at CALM_MW.
HERO = ("Fort Myers", 26.64, -81.87)
HERO_MW, CALM_MW = 1500, 500
SITES = {
    "Orlando": (28.5384, -81.3789, 500),
    "Fort Myers": (HERO[1], HERO[2], HERO_MW),
    "Miami": (25.7617, -80.1918, 1500),
    "Tampa": (27.9506, -82.4572, 500),
    "Jacksonville": (30.3322, -81.6557, 500),
}


def line(ok: bool, text: str) -> bool:
    print(("PASS  " if ok else "FAIL  ") + text)
    return ok


def compare(g: Grid) -> dict:
    """Our DC base-case flows against the dataset's own solved flows on one model, plus the base case's calm."""
    st = g.base
    ref = g.pf_ref
    mask = np.abs(ref) > REF_MIN_MW
    ours, want = st.flow[mask], ref[mask]
    err = np.abs(ours - want)
    n = int(mask.sum())
    corr = float(np.corrcoef(ours, want)[0, 1]) if n > 2 else float("nan")
    close = err <= np.maximum(2.0, 0.05 * np.abs(want))  # within 2 MW or 5 % of the dataset's flow
    worst = int(np.argmax(err)) if n else 0
    worst_i = int(np.flatnonzero(mask)[worst]) if n else 0
    return {
        "buses": int(g.n),
        "substations": int(len(g.sub_ids)),
        "branches": int(g.m),
        "compared": n,
        "corr": round(corr, 5),
        "mean_abs_err_mw": round(float(err.mean()), 2) if n else None,
        "median_abs_err_mw": round(float(np.median(err)), 2) if n else None,
        "p95_abs_err_mw": round(float(np.percentile(err, 95)), 1) if n else None,
        "max_abs_err_mw": round(float(err.max()), 1) if n else None,
        # the biggest single gap, with the flow it sits on (a gap on a heavy line reads differently from one on a light line)
        "max_err_branch": {"id": int(g.br_ids[worst_i]), "kv": round(float(g.br_kv[worst_i])), "ours_mw": round(float(st.flow[worst_i]), 1),
                           "dataset_mw": round(float(ref[worst_i]), 1)} if n else None,
        "flow_weighted_err_pct": round(float(err.sum() / np.abs(want).sum() * 100), 2) if n else None,
        "within_2mw_or_5pct_share": round(float(close.mean()), 4) if n else None,
        "base_over": int((st.loading_pct > 100.0).sum()),
        "base_max_pct": round(float(st.loading_pct.max()), 1),
        "base_lost_mw": round(float(st.lost_mw), 1),
    }


def validate_all() -> None:
    """--all: every committed state model, written to validation.json (see the module docstring)."""
    t0 = time.time()
    with open(os.path.join(HERE, "grids", "index.json"), encoding="utf-8") as fh:
        regions = json.load(fh)["regions"]
    states: dict[str, dict] = {}
    for code in sorted(regions):
        r = regions[code]
        t = time.time()
        g = Grid.from_file(os.path.join(HERE, r["file"]))
        row = {"name": r.get("name", code), **compare(g)}
        row["pass"] = bool(row["corr"] >= CORR_MIN and row["base_over"] <= max(2, 0.02 * row["branches"]) and row["base_lost_mw"] < 1)
        states[code] = row
        print(f"{code} {row['name']:15s} corr {row['corr']:.4f} on {row['compared']:5d} branches | mean gap {row['mean_abs_err_mw']:6.2f} MW, "
              f"median {row['median_abs_err_mw']:5.2f}, max {row['max_abs_err_mw']:6.1f} | base over {row['base_over']} | "
              f"{'PASS' if row['pass'] else 'FAIL'} ({time.time() - t:.1f}s)")
    corrs = sorted((v["corr"], k) for k, v in states.items())
    compared = sum(v["compared"] for v in states.values())
    out = {
        "generated": time.strftime("%Y-%m-%d %H:%M"),
        "command": "backend/venv/Scripts/python backend/demo/validate.py --all",
        "method": ("Our DC power flow (lossless, steady state) on each committed state model, given the same inputs as the dataset's "
                   "solution: its loads, its generator dispatch and its flows on the lines that cross the state border (held fixed), "
                   "with generation scaled only so each island balances; compared branch by branch with the flows in the dataset's own "
                   f"published solved base case. Branches the dataset gives under {REF_MIN_MW:g} MW are left out."),
        "reference": ("Breakthrough Energy / Texas A&M U.S. test system (CC-BY 4.0), branch Pf of its solved base case: an AC solution "
                      "(it carries reactive power and losses), so a small gap to a lossless DC flow is expected."),
        "limits": "DC power flow, steady state, a synthetic grid: agreement with the dataset's own solution, not with any real utility's network.",
        "threshold": {"corr_min": CORR_MIN, "base_over_max_share": 0.02},
        "summary": {
            "states": len(states),
            "passed": sum(1 for v in states.values() if v["pass"]),
            "branches_compared": compared,
            "corr_min": {"code": corrs[0][1], "name": states[corrs[0][1]]["name"], "corr": corrs[0][0]},
            "corr_median": round(float(np.median([c for c, _ in corrs])), 5),
            "states_corr_999": sum(1 for c, _ in corrs if c >= 0.999),
            "mean_abs_err_mw": round(sum(v["mean_abs_err_mw"] * v["compared"] for v in states.values()) / compared, 2),
        },
        "states": states,
    }
    with open(VALIDATION_OUT, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=1)
        fh.write("\n")
    s = out["summary"]
    print(f"\n{s['passed']} of {s['states']} states pass; {compared:,} branches compared; lowest correlation {s['corr_min']['corr']:.4f} "
          f"({s['corr_min']['code']}), median {s['corr_median']:.4f}; {time.time() - t0:.1f} s")
    print(f"wrote {VALIDATION_OUT}")
    sys.exit(0 if s["passed"] == s["states"] else 1)


def main() -> None:
    if "--all" in sys.argv[1:]:
        validate_all()
        return
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
              f"existing load lost {casc['lost_mw']:.0f} MW (~{casc['homes']:,} homes), site itself dark {casc['site_dark_mw']:.0f} MW")
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

    # the hero: a real cascade at HERO_MW, nothing over limit at CALM_MW on the same spot
    hbus = g.site_bus(HERO[1], HERO[2])
    hc = g.cascade(hbus, HERO_MW)
    hdark = sum(len(s["dark_subs"]) for s in hc["steps"])
    calm_over = len(g.overloaded(g.whatif(hbus, CALM_MW)))
    print(f"hero {HERO[0]}: {HERO_MW} MW -> {hc['total_steps']} steps, {hdark} substations dark, ~{hc['homes']:,} homes; "
          f"{CALM_MW} MW -> {calm_over} over limit")
    results.append(line(hc["total_steps"] >= 3 and hdark >= 5 and calm_over == 0,
                        "hero site cascades at the big size and is calm at the small one"))
    if expected is not None:
        expected["hero"] = {
            "site": HERO[0],
            "lat": HERO[1],
            "lon": HERO[2],
            "mw": HERO_MW,
            "calm_mw": CALM_MW,
            "sub": int(g.sub_ids[g.bus_sub_idx[hbus]]),
            "cascade_steps": hc["total_steps"],
            "cascade_outcome": hc["outcome"],
            "dark_subs": hdark,
            "homes": hc["homes"],
        }

    if expected is not None:
        out = os.path.join(HERE, "expected_whatif.json")
        with open(out, "w", encoding="utf-8") as fh:
            json.dump(expected, fh, indent=2)
        print(f"wrote {out}")

    print("\nMILESTONE 0:", "PASS" if all(results) else "FAIL — see SPEC.md → Milestone 0 for the fallback chain")
    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    main()
