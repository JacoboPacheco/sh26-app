"""Smoke checks for "How we know" (backend/evidence.py): the validation stats, the N-1 screen of a fix and the hours a year
a case overloads. Loaded by smoke_test.py. Read-only: every call is a public what-if-style request, nothing is created,
so it is safe against the deployed backend.

ctx: check(name, fn), request(method, path, data=None, headers=None, expect=200),
auth() -> headers for this run's throwaway user, expected (expected_whatif.json).

Florida only against a deployed server (every other state model would be loaded on a 512 MB instance): the Georgia check
runs only against a local server."""

import math
import os

# the Gulf landfall preset's path (backend/hurricane.py PRESETS[0]): a Florida storm case
GULF = [[-82.9, 25.95], [-82.25, 26.35], [-81.85, 26.7], [-81.4, 27.25], [-80.95, 27.85], [-80.5, 28.45]]


def _local() -> bool:
    """Is the server under test on this machine? (smoke_test.py keeps it in BASE, scratch/run_one_smoke.py in base.)"""
    import __main__

    base = next((v for v in (getattr(__main__, n, None) for n in ("BASE", "base")) if isinstance(v, str) and v.startswith("http")), None)
    base = base or os.getenv("SMOKE_BASE_URL", "http://localhost:8000")
    return any(h in base for h in ("://localhost", "://127.0.0.1", "://[::1]"))


def register(ctx):
    memo = {}

    def hero_body():
        h = (ctx.expected or {}).get("hero") or {"lat": 26.64, "lon": -81.87, "mw": 1500}
        return {"lat": h["lat"], "lon": h["lon"], "mw": h["mw"]}

    def get(key, method, path, body=None):
        if key not in memo:
            memo[key] = ctx.request(method, path, body)
        return memo[key]

    def storm_body():
        if "storm" not in memo:
            memo["storm"] = ctx.request("POST", "/api/hurricane/track", {"points": GULF, "radius_km": 20})["trip"]
        return {"trip": memo["storm"]}

    def test_validation():
        v = get("val", "GET", "/api/evidence/validation?region=FL")
        assert v["synthetic"] is True and v["region"] == "FL", "not labeled synthetic / wrong region"
        st = v["state"]
        assert st["corr"] >= 0.9 and st["compared"] > 1000, f"FL corr {st['corr']} on {st['compared']} lines"
        gap = v["rating_gap"]
        assert gap["compared"] > 1000, f"rating gap on {gap['compared']} lines"
        assert 0.0 <= gap["within_5pct_of_rating_share"] <= 1.0, gap["within_5pct_of_rating_share"]
        assert gap["median_gap_pct_of_rating"] <= gap["p95_gap_pct_of_rating"], "median gap above the 95th percentile"
        assert v["source"]["url"].startswith("https://"), "the dataset source has no link"
        for k in ("method", "reference", "limits", "sentence", "label", "rating_gap_method"):
            assert v.get(k), f"validation lacks {k}"
        # the same row the "How we know it's right" card serves (validation.json)
        allv = ctx.request("GET", "/api/ai/validation")
        assert allv["states"]["FL"]["corr"] == st["corr"], "FL row differs from /api/ai/validation"
        ctx.request("GET", "/api/evidence/validation?region=ZZ", expect=422)

    def n1_hero():
        return get("n1", "POST", "/api/evidence/n1", hero_body())

    def test_n1_shape():
        r = n1_hero()
        assert r["synthetic"] is True, "not labeled synthetic"
        assert r["label"].startswith("N-1 screen: the ") and "one at a time" in r["label"], r["label"]
        n = r["screened"]
        assert 1 <= n <= r["asked"] <= 30, f"screened {n} of {r['asked']}"
        assert f"the {n} most loaded" in r["label"], "the label does not say how many were screened"
        keys = [v["key"] for v in r["variants"]]
        assert keys == ["with_fix", "without_fix", "grid_alone"], keys
        for v in r["variants"]:
            assert 0 <= v["fails"] <= v["fails_any"] <= n, f"{v['key']}: fails {v['fails']} / any {v['fails_any']} of {n}"
            assert v["holds"] == n - v["fails_any"], f"{v['key']}: holds {v['holds']}"
            assert v["worst"] and v["worst"]["pct"] > 0 and v["worst"]["out"]["name"] and v["worst"]["over"]["name"], f"{v['key']} worst {v['worst']}"
        assert r["center"] and "campus" in r["center"]["what"], r["center"]
        assert r["sentence"] and r["method"] and r["limits"], "no sentence / method / limits"
        # the fix is named by what it is (Fix it's re-rating, never "the fix" of the results panel's button), and a
        # partial re-rating says so
        fx = r["fix"]
        v = {x["key"]: x for x in r["variants"]}
        assert fx["label"] == v["with_fix"]["label"] and fx["label"].startswith("With Fix it's"), fx["label"]
        assert fx["partial"] == (v["with_fix"]["over_before"] > 0), (fx["partial"], v["with_fix"]["over_before"])
        assert r["sentence"].startswith(fx["label"]), r["sentence"]
        assert r["seconds"] < 10, f"N-1 screen took {r['seconds']} s"

    def test_n1_fix_is_fix_it():
        # no upgrades in the case: the fix is the Fix it search's (POST /api/fix), and it clears the what-if's overloads
        r = n1_hero()
        fx = ctx.request("POST", "/api/fix", hero_body())
        assert r["fix"]["source"] == "engine", r["fix"]["source"]
        assert sorted(e["id"] for e in r["fix"]["elements"]) == sorted(u["id"] for u in fx["upgrades"]), "fix differs from /api/fix"
        v = {x["key"]: x for x in r["variants"]}
        w = ctx.request("POST", "/api/grid/whatif", hero_body())
        assert v["without_fix"]["over_before"] == len(w["overloaded"]), f"{v['without_fix']['over_before']} over vs the what-if's {len(w['overloaded'])}"
        assert v["with_fix"]["over_before"] == 0, "the fix leaves a line over with every line in"
        # the case's own upgrades (the fix applied, as the map posts it): the same ratings, so the same answer
        r2 = ctx.request("POST", "/api/evidence/n1", {**hero_body(), "upgrades": fx["apply"]})
        assert r2["fix"]["source"] == "case", r2["fix"]["source"]
        v2 = {x["key"]: x for x in r2["variants"]}
        assert v2["with_fix"]["fails_any"] == v["with_fix"]["fails_any"], "the applied fix screens differently from the engine's"
        # the map with its fix applied (auto_fix=false): the case as it is, never another fix invented for it
        r3 = ctx.request("POST", "/api/evidence/n1?auto_fix=false", {**hero_body(), "upgrades": fx["apply"]})
        v3 = {x["key"]: x for x in r3["variants"]}
        assert r3["fix"]["source"] == "applied" and list(v3) == ["with_fix", "without_fix", "grid_alone"], (r3["fix"]["source"], list(v3))
        assert v3["with_fix"]["label"] == "With the fix applied" and v3["with_fix"]["fails_any"] == v["with_fix"]["fails_any"], v3["with_fix"]["label"]
        # an operating-rule fix (firm service, no upgrades): screened as it is, no engine re-rating
        r4 = ctx.request("POST", "/api/evidence/n1?auto_fix=false", {**hero_body(), "firm": True})
        assert r4["fix"]["source"] == "applied" and r4["fix"]["count"] == 0, r4["fix"]
        assert [x["key"] for x in r4["variants"]] == ["case", "grid_alone"], [x["key"] for x in r4["variants"]]
        h4 = ctx.request("POST", "/api/evidence/hours?auto_fix=false", {**hero_body(), "firm": True})
        assert [x["key"] for x in h4["variants"]] == ["case", "grid_alone"] and h4["fix"]["source"] == "applied", h4["fix"]["source"]

    def test_n1_calm_and_empty():
        h = (ctx.expected or {}).get("hero") or {}
        calm = ctx.request("POST", "/api/evidence/n1", {**hero_body(), "mw": h.get("calm_mw", 500)})
        keys = [v["key"] for v in calm["variants"]]
        assert calm["fix"]["source"] == "none" and keys == ["case", "grid_alone"], (calm["fix"]["source"], keys)
        ctx.request("POST", "/api/evidence/n1", {}, expect=422)  # nothing to check
        ctx.request("POST", "/api/evidence/n1", {**hero_body(), "region": "ZZ"}, expect=422)

    def hours_hero():
        return get("hours", "POST", "/api/evidence/hours", hero_body())

    def test_hours_shape():
        r = hours_hero()
        assert r["synthetic"] is True and r["estimate"] is True, "not labeled synthetic estimates"
        by = {v["key"]: v for v in r["variants"]}
        assert set(by) == {"without_fix", "with_fix", "grid_alone"}, list(by)
        no, fx, al = by["without_fix"], by["with_fix"], by["grid_alone"]
        assert no["pct"] is not None and no["pct"] <= 100, f"the hero overloads at the peak, yet first at {no['pct']} %"
        assert fx["pct"] is None or fx["pct"] > 100, f"the fix clears the peak, yet overloads at {fx['pct']} %"
        assert al["pct"] is None or al["pct"] > 100, f"Florida's model is calm at its peak, yet overloads at {al['pct']} %"
        assert no["at_case"] is True and fx["at_case"] is False and al["at_case"] is False, (no["at_case"], fx["at_case"], al["at_case"])
        assert no["counted"] and no["counted"][0][0] == no["pct"], (no["counted"], no["pct"])
        assert fx["label"] == r["fix"]["label"] and no["label"] == "As it is", (fx["label"], no["label"])
        curve = r["curve"]
        assert [c["hours"] for c in curve] == sorted((c["hours"] for c in curve), reverse=True), "the step curve is not decreasing"
        assert "Coarse" in r["curve_label"] and "not a forecast" in r["curve_label"], "the curve is not labeled coarse"
        h = no["hours"]
        assert h["low"] <= (h["about"] if h["about"] is not None else h["low"]) <= h["high"] <= 8760, h
        assert r["seconds"] < 10, f"hours took {r['seconds']} s"

    def test_hours_threshold_matches_whatif():
        # the bisection's level against the public what-if at the load levels around it (rounded to the engine's 0.01).
        # Local only: each what-if at a new level adds a Florida model to the server's shared cache (512 MB on Render)
        if not _local():
            return
        lv ={v["key"]: v for v in hours_hero()["variants"]}["without_fix"]["level"]
        up = math.ceil(lv * 100 - 1e-9) / 100
        down = round(up - 0.02, 2)
        if up <= 1.4:
            w = ctx.request("POST", "/api/grid/whatif", {**hero_body(), "load_factor": up})
            assert w["overloaded"], f"nothing over at {up}, above the first overload {lv}"
        if down >= 0.4:
            w = ctx.request("POST", "/api/grid/whatif", {**hero_body(), "load_factor": down})
            assert not w["overloaded"], f"{len(w['overloaded'])} over at {down}, below the first overload {lv}"

    def test_storm_case():
        b = storm_body()
        r = ctx.request("POST", "/api/evidence/n1", b)
        assert r["center"] and "storm" in r["center"]["what"], r["center"]
        assert "near the storm's lines" in r["label"], r["label"]
        assert r["strands"]["count"] >= 0 and r["screened"] >= 1, r["screened"]
        assert r["campus_cut_off"] is False, "no campus in a storm-only case"
        assert n1_hero()["campus_cut_off"] is False, "the hero's campus is connected"
        both = ctx.request("POST", "/api/evidence/n1", {**hero_body(), **b})
        assert isinstance(both["campus_cut_off"], bool) and "near the site" in both["label"], both["label"]
        h = ctx.request("POST", "/api/evidence/hours", b)
        assert h["variants"][0]["key"] in ("without_fix", "case"), h["variants"][0]["key"]
        assert h["sentence"], "no sentence"

    def test_preset():
        # the incident stage posts a catastrophe preset instead of the storm's line ids: expanded as the briefing does
        r = ctx.request("POST", "/api/evidence/n1", {"preset": "fl-gulf-fort-myers"})
        assert r["preset"] and r["screened"] >= 1 and r["label"].startswith("N-1 screen: the "), (r["preset"], r["screened"])
        h = ctx.request("POST", "/api/evidence/hours", {"preset": "fl-gulf-fort-myers"})
        assert h["preset"] == r["preset"] and h["sentence"], h["preset"]
        ctx.request("POST", "/api/evidence/n1", {"preset": "no-such-storm"}, expect=422)

    def test_low_load_not_counted():
        # a one-state model's fixed ties overload at low load in many states (Georgia among them), calm by the peak:
        # reported apart, never counted as hours. Local only (it loads the Georgia model).
        if not _local():
            return
        h = ctx.request("POST", "/api/evidence/hours", {"region": "GA", "load_factor": 0.82})
        al = {v["key"]: v for v in h["variants"]}["grid_alone"]
        assert al["pct"] is None or al["pct"] > 100, f"Georgia's model is calm at its peak, yet 'overloads' from {al['pct']} %"
        assert al["low_to_pct"] is not None and al["low_to_pct"] < 100, f"no low-load band reported: {al['bands']}"
        assert "not counted" in h["sentence"], h["sentence"]

    ctx.check("evidence: validation stats for Florida, matching validation.json", test_validation)
    ctx.check("evidence: N-1 screen shape, labeled with how many were screened", test_n1_shape)
    ctx.check("evidence: N-1 screens Fix it's own fix, the case's upgrades the same way, and an applied fix as it is", test_n1_fix_is_fix_it)
    ctx.check("evidence: N-1 of a calm case screens the case itself; empty case refused", test_n1_calm_and_empty)
    ctx.check("evidence: hours a year, with and without the fix, on a labeled coarse curve", test_hours_shape)
    ctx.check("evidence: the first-overload level agrees with the what-if around it (local only)", test_hours_threshold_matches_whatif)
    ctx.check("evidence: N-1 and hours on a Florida storm case", test_storm_case)
    ctx.check("evidence: a catastrophe preset is expanded as the briefing does", test_preset)
    ctx.check("evidence: low-load overloads (a one-state model's fixed ties) are not counted as hours (local only)", test_low_load_not_counted)
