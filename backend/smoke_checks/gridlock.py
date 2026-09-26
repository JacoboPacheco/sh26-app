"""Smoke checks for build plans / GridLock (owned by its track). Loaded by smoke_test.py.

ctx: check(name, fn), request(method, path, data=None, headers=None, expect=200),
auth() -> headers for this run's throwaway user, expected (expected_whatif.json).

Everything here is read-only: the GridLock endpoints are public GETs over committed data
(backend/demo/gridlock/data/projects.json, or Sperry's worked example until it exists)."""

GEORGIA = {"GPC", "GTC", "MEAG", "DU"}
TIER_EDGES = {"touching": 0.1, "row": 1.6, "site": 8.0}
TIER_LABELS = {
    "touching": "Must coordinate",
    "row": "Share the land",
    "site": "Share site logistics",
    "crews": "Share crews and equipment",
}


def register(ctx):
    state = {}

    def summary_shape():
        s = ctx.request("GET", "/api/gridlock/summary")
        assert "built_at" in s and isinstance(s["sources"], list) and s["sources"], "no sources"
        counts = s["counts"]
        assert "DESC" in counts, f"no DESC counts: {list(counts)}"
        assert GEORGIA & set(counts), f"no Georgia utility in counts: {list(counts)}"
        for code, c in counts.items():
            for k in ("extracted", "located", "quarantined"):
                assert isinstance(c[k], int) and c[k] >= 0, f"{code}.{k} = {c.get(k)!r}"
            assert c["located"] + c["quarantined"] <= c["extracted"], f"{code}: located + quarantined > extracted ({c})"
        assert isinstance(s["report"], dict) and "stages" in s["report"], "report missing its stages"

    def projects_both_sides():
        p = ctx.request("GET", "/api/gridlock/projects")
        projects = p["projects"]
        utilities = {x["utility"] for x in projects}
        assert "DESC" in utilities, f"no DESC projects: {utilities}"
        assert GEORGIA & utilities, f"no Georgia projects: {utilities}"
        ids = [x["id"] for x in projects]
        assert len(ids) == len(set(ids)), "duplicate project ids"
        for x in projects:
            assert x.get("name") and x.get("provenance"), f"{x['id']}: no name or provenance"
            g = x.get("geometry")
            if g:
                assert g["type"] in ("point", "segment") and g["coords"], f"{x['id']}: bad geometry {g}"
                for lon, lat in g["coords"]:
                    assert 29.5 <= lat <= 36 and -86.5 <= lon <= -78, f"{x['id']}: {lat},{lon} is outside SC/GA"
        for q in p["quarantine"]:
            assert q.get("reasons"), f"quarantined {q.get('id')} has no reasons"
        state["projects"] = {x["id"]: x for x in projects}

    def overlaps_default():
        o = ctx.request("GET", "/api/gridlock/overlaps")
        prm = o["params"]
        assert prm["max_km"] == 40 and prm["method"] == "closest", prm
        rows = o["overlaps"]
        assert rows, "no overlaps at the default parameters"
        assert o["flagged"] >= len(rows) and o["total_pairs"] >= o["flagged"], (o["total_pairs"], o["flagged"], len(rows))
        assert [r["rank"] for r in rows] == list(range(1, len(rows) + 1)), "ranks are not 1..n"
        scores = [r["score"] for r in rows]
        assert scores == sorted(scores, reverse=True), "not ranked by score"
        projects = state["projects"]
        for r in rows:
            pa, pb = projects[r["a"]], projects[r["b"]]
            assert pa["utility"] != pb["utility"], f"{r['id']} pairs a utility with itself"
            assert pa["utility"] in prm["a"] and pb["utility"] in prm["b"], f"{r['id']} is outside a={prm['a']} b={prm['b']}"
            d = r["distance_km"]
            assert 0 <= d <= prm["max_km"] + 1e-6, f"{r['id']}: {d} km is over the threshold"
            assert abs(r["distance_mi"] * 1.609344 - d) < 0.01, f"{r['id']}: km and miles disagree"
            t = r["tier"]
            assert r["tier_label"] == TIER_LABELS[t], f"{r['id']}: label {r['tier_label']!r} for tier {t}"
            if t == "touching":
                assert d < 0.1 or r.get("crosses"), f"{r['id']}: touching at {d} km"
            elif t == "row":
                assert 0.1 <= d < 1.6, f"{r['id']}: row at {d} km"
            elif t == "site":
                assert 1.6 <= d < 8, f"{r['id']}: site at {d} km"
            else:
                assert d >= 8, f"{r['id']}: crews at {d} km"
            assert len(r["closest_points"]) == 2 and all(len(pt) == 2 for pt in r["closest_points"]), r["closest_points"]
            assert r["reasons"] and 0 < r["score"] <= 110, f"{r['id']}: score {r['score']} / reasons {r['reasons']}"
        state["top"] = rows[0]

    def overlaps_center_method():
        o = ctx.request("GET", "/api/gridlock/overlaps?method=center&max_km=40.2336")
        for r in o["overlaps"]:
            assert r["center_distance_mi"] < 25.005, f"{r['id']}: centers {r['center_distance_mi']} mi apart"
        wide = ctx.request("GET", "/api/gridlock/overlaps?max_km=8&a=DESC&b=GA")
        assert all(r["distance_km"] < 8 for r in wide["overlaps"]), "a pair over 8 km came back"

    def param_validation():
        for q in (
            "max_km=0", "max_km=-5", "max_km=1000", "max_km=nan", "max_km=abc",
            "window_months=-1", "window_months=1000", "window_months=x",
            "method=nearest", "a=XYZ", "a=DESC&b=DESC", "b=", "limit=0", "limit=100000",
        ):
            ctx.request("GET", f"/api/gridlock/overlaps?{q}", expect=422)
        ctx.request("GET", "/api/gridlock/opportunities?limit=0", expect=422)
        ctx.request("GET", "/api/gridlock/opportunities?limit=500", expect=422)

    def opportunities():
        o = ctx.request("GET", "/api/gridlock/opportunities?limit=5")
        opps = o["opportunities"]
        assert 1 <= len(opps) <= 5, len(opps)
        for x in opps:
            assert x["project_a"]["id"] == x["a"] and x["project_b"]["id"] == x["b"], x["id"]
            assert x["share"], f"{x['id']}: no 'what they could share' line"
        assert opps[0]["id"] == state["top"]["id"], "opportunity #1 differs from overlap #1"

    def estimate_top():
        e = ctx.request("GET", f"/api/gridlock/estimate/{state['top']['id']}")
        assert e["items"], "no estimate items"
        for it in e["items"]:
            assert it["low"] <= it["high"], f"{it['label']}: low {it['low']} > high {it['high']}"
            assert it["basis"] and it["source"] and it["unit"], it
        assert 0 <= e["total_low"] <= e["total_high"], (e["total_low"], e["total_high"])
        assert e["total_high"] > 0, "estimate totals nothing"
        assert e["sources"] and all(s["url"].startswith("https://") for s in e["sources"]), e["sources"]
        assert e["assumptions"], "no assumptions"

    def estimate_unknown():
        ctx.request("GET", "/api/gridlock/estimate/NOPE~NADA", expect=404)
        ctx.request("GET", "/api/gridlock/estimate/not-an-overlap", expect=404)
        same = [p for p in state["projects"].values() if p["utility"] == "DESC" and p.get("geometry")][:2]
        if len(same) == 2:
            ctx.request("GET", f"/api/gridlock/estimate/{same[0]['id']}~{same[1]['id']}", expect=404)

    def sperry_reproduced():
        s = ctx.request("GET", "/api/gridlock/sperry-check")
        assert len(s["rows"]) == 6, f"{len(s['rows'])} rows, Sperry's example has 6 overlaps"
        bad = [r for r in s["rows"] if not r["ok"]]
        assert not bad, f"not reproduced: {bad}"
        assert s["all_ok"] and not s["extra"] and not s["missing"], (s["extra"], s["missing"])
        # their six pairs are also found in the comparison of the full filings, and tagged there
        f = s["found_in_filings"]
        assert f["flagged"] == f["of"] == 6, f"only {f['flagged']} of their pairs flagged in the full comparison"
        top = ctx.request("GET", "/api/gridlock/overlaps?limit=2000")
        tagged = {r["sperry"] for r in top["overlaps"] if r.get("sperry")}
        assert tagged == {r["overlap_id"] for r in s["rows"]}, f"tagged overlaps: {sorted(tagged)}"

    def window_setting_matters():
        # projects without a filed start date take their build window from window_months, so it must
        # change something (it once changed nothing: every window was treated as filed)
        q = "a=DESC&b=GA&max_km=50&limit=2000"
        short = ctx.request("GET", f"/api/gridlock/overlaps?{q}&window_months=6")
        long = ctx.request("GET", f"/api/gridlock/overlaps?{q}&window_months=60")
        assert short["window_assumed"]["projects"] > 0, short["window_assumed"]
        key = lambda r: (r["id"], r["windows_overlap_months"], r["score"])  # noqa: E731
        assert {key(r) for r in short["overlaps"]} != {key(r) for r in long["overlaps"]}, "window_months changes nothing"

    def basemap():
        b = ctx.request("GET", "/api/gridlock/basemap")
        assert isinstance(b["states"], dict) and isinstance(b["lines"], list), "basemap shape"
        x0, y0, x1, y1 = b["bbox"]
        assert x0 < x1 and y0 < y1, b["bbox"]

    ctx.check("gridlock: summary has sources, DESC + Georgia counts, and the pipeline report", summary_shape)
    ctx.check("gridlock: projects cover DESC and a Georgia utility, placed inside SC/GA, quarantine has reasons", projects_both_sides)
    ctx.check("gridlock: default overlaps are ranked cross-utility pairs with tiers matching their distances", overlaps_default)
    ctx.check("gridlock: center method stays under 25 mi; a tighter threshold stays tighter", overlaps_center_method)
    ctx.check("gridlock: bad max_km / window / method / utilities / limits are 422s", param_validation)
    ctx.check("gridlock: opportunities inline both projects and say what they could share", opportunities)
    ctx.check("gridlock: estimate for the top overlap has low <= high items, sources and assumptions", estimate_top)
    ctx.check("gridlock: unknown or same-utility estimate ids are 404s", estimate_unknown)
    ctx.check("gridlock: Sperry's worked example is reproduced (6 of 6, no extra pairs) and found in the full filings", sperry_reproduced)
    ctx.check("gridlock: the build-window setting changes pairs whose filing gives no start date", window_setting_matters)
    ctx.check("gridlock: basemap has states, lines and a bbox", basemap)
