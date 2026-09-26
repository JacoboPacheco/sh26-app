"""Smoke checks for the boom feature (owned by its track). Loaded by smoke_test.py.

ctx: check(name, fn), request(method, path, data=None, headers=None, expect=200),
auth() -> headers for this run's throwaway user, expected (expected_whatif.json).
Create nothing another user would see; clean up what you create.

AI-boom mode sends several data centers in one case (`sites`). These run against the public grid
endpoints only, so they create nothing."""

# the frontend's preset (frontend/src/features/boom/boomData.js): one substation near each big metro
METROS = [
    (30.3064, -81.666),  # Jacksonville 22
    (28.5603, -81.3734),  # Orlando 40
    (27.9404, -82.4302),  # Tampa 46
    (26.5765, -81.8802),  # Fort Myers 6
    (25.7995, -80.3041),  # Miami 48
]


def _sites(points, mw=1000):
    return [{"lat": lat, "lon": lon, "mw": mw} for lat, lon in points]


def register(ctx):
    def five_campuses_whatif():
        r = ctx.request("POST", "/api/grid/whatif", {"sites": _sites(METROS)})
        assert len(r["sites"]) == 5, f"expected 5 sites back, got {len(r['sites'])}"
        assert r["mw"] == 5000, f"total added load should be 5,000 MW, got {r['mw']}"
        assert len({s["sub"] for s in r["sites"]}) == 5, "the five campuses should connect at five different substations"
        assert r["overloaded"], "five 1 GW campuses should push at least one line over its limit"
        pcts = [o["pct"] for o in r["overloaded"]]
        assert pcts == sorted(pcts, reverse=True), "overloaded lines must come worst first (the UI's 'breaks first')"

    def five_campuses_cascade():
        c = ctx.request("POST", "/api/grid/cascade", {"sites": _sites(METROS)})
        assert len(c["sites"]) == 5, f"expected 5 sites back, got {len(c['sites'])}"
        assert c["total_steps"] >= 1, f"expected the cascade to take at least one step, got {c['total_steps']}"
        assert len(c["steps"]) == c["total_steps"], "one step entry per trip"

    def twelve_sites_ok():
        # the main site plus the 11 extras the UI allows
        pts = [(lat, lon) for lat, lon in METROS] + [(lat + 0.1, lon) for lat, lon in METROS] + [(28.0, -81.9)]
        body = {"lat": 27.5, "lon": -81.5, "mw": 100, "sites": _sites(pts, 100)}
        r = ctx.request("POST", "/api/grid/whatif", body)
        assert len(r["sites"]) == 12

    def thirteen_sites_rejected():
        pts = [(lat, lon) for lat, lon in METROS] + [(lat + 0.1, lon) for lat, lon in METROS] + [(28.0, -81.9), (27.5, -81.5), (29.0, -82.0)]
        assert len(pts) == 13
        r = ctx.request("POST", "/api/grid/whatif", {"sites": _sites(pts)}, expect=422)
        assert "12" in r["detail"], r["detail"]

    def site_outside_florida_rejected():
        pts = METROS[:2] + [(40.71, -74.01)]  # New York
        r = ctx.request("POST", "/api/grid/whatif", {"sites": _sites(pts)}, expect=422)
        assert "outside Florida" in r["detail"], r["detail"]
        ctx.request("POST", "/api/grid/cascade", {"sites": _sites(pts)}, expect=422)

    def site_off_the_model_rejected():
        # inside the bounding box but far out in the Gulf: no substation within reach
        r = ctx.request("POST", "/api/grid/whatif", {"sites": _sites(METROS[:1] + [(25.0, -84.5)])}, expect=422)
        assert "No grid here" in r["detail"], r["detail"]

    def campus_size_limits():
        ctx.request("POST", "/api/grid/whatif", {"sites": _sites(METROS[:1], 50001)}, expect=422)  # over the 50 GW custom-size cap
        ctx.request("POST", "/api/grid/whatif", {"sites": _sites(METROS[:1], 0)}, expect=422)

    ctx.check("boom: five 1 GW campuses solve together, worst line first", five_campuses_whatif)
    ctx.check("boom: five 1 GW campuses cascade at least one step", five_campuses_cascade)
    ctx.check("boom: 12 data centers (main + 11) are accepted", twelve_sites_ok)
    ctx.check("boom: 13 data centers are rejected", thirteen_sites_rejected)
    ctx.check("boom: a campus outside Florida is rejected", site_outside_florida_rejected)
    ctx.check("boom: a campus off the grid model is rejected", site_off_the_model_rejected)
    ctx.check("boom: campus size must be 1-5,000 MW", campus_size_limits)
