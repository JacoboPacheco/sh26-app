"""Smoke checks for the data-center catalog (backend/catalog.py). Loaded by smoke_test.py.
Read-only: every call is a public GET or a public what-if/cascade, nothing is created, so it is
safe against the deployed backend.

ctx: check(name, fn), request(method, path, data=None, headers=None, expect=200),
auth() -> headers for this run's throwaway user, expected (expected_whatif.json)."""

import time

WAIT_S = 120  # the batch takes ~5 s locally; Render's free tier is slower


def register(ctx):
    memo = {}

    def ready():
        if "list" not in memo:
            deadline = time.time() + WAIT_S
            while True:
                s = ctx.request("GET", "/api/catalog/status")
                assert s["status"] in ("computing", "ready", "error"), f"status {s['status']!r}"
                assert s["status"] != "error", f"batch failed: {s.get('error')}"
                if s["status"] == "ready":
                    break
                assert time.time() < deadline, f"batch still computing after {WAIT_S} s ({s['done']} of {s['total']})"
                time.sleep(1.0)
            memo["list"] = ctx.request("GET", "/api/catalog")
        return memo["list"]

    def pick(data):
        """A tested Florida campus that overloads something (the demo state), else any tested one."""
        tested = [e for e in data["entries"] if not e["duplicate_of"] and e["test"] and e["test"]["tested"]]
        fl = [e for e in tested if e["state"] == "FL" and e["test"]["overloaded"] > 0]
        assert tested, "no tested campus in the catalog"
        return (fl or tested)[0]

    def test_list_shape():
        r = ctx.request("GET", "/api/catalog")
        assert r["status"] in ("computing", "ready"), f"status {r['status']!r}"
        es = r["entries"]
        assert len(es) >= 10, f"only {len(es)} entries"
        ids = [e["id"] for e in es]
        assert len(set(ids)) == len(ids), "duplicate ids"
        for e in es:
            for k in ("name", "company", "state", "mw", "status", "confidence", "sources", "duplicate_of", "counted"):
                assert k in e, f"{e['id']} lacks {k}"
            assert all(s["url"].startswith(("http://", "https://")) for s in e["sources"]), f"{e['id']} has a non-web source link"
            assert "verification" not in e, "the list should not carry the research notes"
        canon = {e["id"] for e in es if not e["duplicate_of"]}
        assert all(e["duplicate_of"] in canon for e in es if e["duplicate_of"]), "a duplicate points at a missing entry"
        assert "synthetic" in r["frame"] and r["totals"]["campuses"] > 0, "frame / totals missing"

    def test_batch_totals():
        r = ready()
        counted = [e for e in r["entries"] if e["counted"]]
        missing = [e["id"] for e in r["entries"] if not e["duplicate_of"] and not e["test"]]
        assert not missing, f"entries without a test after the batch: {missing[:5]}"
        tested = [e for e in counted if e["test"]["tested"]]
        t = r["totals"]
        assert t["complete"] is True, "totals not complete after the batch"
        assert t["campuses"] == len(counted), f"campuses {t['campuses']} != {len(counted)} counted entries"
        assert t["mw"] == sum(e["mw"] for e in counted), "total MW != the sum of counted entries"
        assert t["overloads"] == sum(1 for e in tested if e["test"]["overloaded"] > 0), "overload count mismatch"
        assert t["people"] == sum(e["test"]["people"] for e in tested), "people total != the sum of tests"
        assert sum(s["mw"] for s in r["by_state"]) == t["mw"], "by-state MW doesn't add up"
        for e in r["entries"]:
            tt = e["test"]
            if tt and not tt["tested"]:
                assert tt.get("reason"), f"{e['id']} untested without a reason"
            if tt and tt["tested"]:
                assert tt["verdict"] in ("fits", "overloads", "outage"), f"{e['id']} verdict {tt['verdict']!r}"
                assert tt["people"] <= (tt["population"] or 10**9), f"{e['id']} people above the population"
                assert "synthetic" in tt["sentence"], f"{e['id']} sentence doesn't say synthetic model"

    def test_detail_matches_engine():
        e = pick(ready())
        d = ctx.request("GET", f"/api/catalog/{e['id']}")
        t = d["test"]
        assert t["tested"] and t["region"] == e["state"], f"detail not tested for {e['id']}"
        assert t["overloaded"] == e["test"]["overloaded"] and t["people"] == e["test"]["people"], "detail != batch summary"
        body = {"region": e["state"], "lat": e["lat"], "lon": e["lon"], "mw": min(e["mw"], d["workspace_max_mw"])}
        if e["mw"] <= d["workspace_max_mw"]:
            w = ctx.request("POST", "/api/grid/whatif", body)
            assert len(w["overloaded"]) == t["overloaded"], f"what-if {len(w['overloaded'])} over != catalog {t['overloaded']}"
            assert abs(w["headroom_mw"] - t["headroom_mw"]) <= 1, f"headroom {w['headroom_mw']} != {t['headroom_mw']}"
            assert w["sub_name"] == t["sub_name"], f"sub {w['sub_name']} != {t['sub_name']}"
            flex = ctx.request("POST", "/api/grid/cascade", body)
            assert flex["people"] == t["flexible"]["people"], f"cascade {flex['people']} != catalog {t['flexible']['people']}"
            firm = ctx.request("POST", "/api/grid/cascade", {**body, "firm": True})
            assert firm["people"] == t["firm"]["people"], f"firm cascade {firm['people']} != catalog {t['firm']['people']}"
            big = t.get("bigger")
            if big and big["mw"] <= d["workspace_max_mw"]:  # the "twice the size" claim in `why` is measured
                twice = ctx.request("POST", "/api/grid/cascade", {**body, "mw": big["mw"]})
                assert twice["people"] == big["people"], f"2x cascade {twice['people']} != catalog {big['people']}"
        assert "verification" in d and d["sources"], "detail lacks sources / verification"
        assert isinstance(t["flexible"]["areas"], list) and isinstance(t["flexible"]["timeline"], list), "no areas / timeline"
        assert len(t["flexible"]["timeline"]) == t["flexible"]["steps"], "timeline length != steps"

    def test_unknown_id():
        ctx.request("GET", "/api/catalog/no-such-campus-here", expect=404)

    ctx.check("catalog: /api/catalog lists campuses with ids, web sources, dedupe links, frame", test_list_shape)
    ctx.check("catalog: the batch tests every campus; totals add up (people = sum of tests, estimate)", test_batch_totals)
    ctx.check("catalog: a campus's detail matches /api/grid/whatif + cascade (flexible and firm)", test_detail_matches_engine)
    ctx.check("catalog: an unknown id is a 404", test_unknown_id)
