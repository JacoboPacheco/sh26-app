"""Smoke checks for the sources list (backend/sources.py). Loaded by smoke_test.py."""


def register(ctx):
    def every_dataset_has_a_license_and_a_use():
        r = ctx.request("GET", "/api/sources")
        ids = [d["id"] for d in r["datasets"]]
        for want in ("grid", "census_pop", "osm_hospitals", "compute_atlas", "epoch", "catalog"):
            assert want in ids, ids
        for d in r["datasets"]:
            assert d["name"] and d["license"] and d["used_for"], d
            assert d["url"] is None or d["url"].startswith("https://"), d
        grid = next(d for d in r["datasets"] if d["id"] == "grid")
        assert "CC BY 4.0" in grid["license"] and "SYNTHETIC" in grid["used_for"], grid
        assert len(r["filings"]) >= 2 and all(f["url"].startswith("https://") for f in r["filings"]), r["filings"]
        refs = r["references"]
        assert any(x["id"].startswith("cost.") for x in refs) and any(x["id"].startswith("gridlock.") for x in refs), [x["id"] for x in refs][:10]
        assert all(x["name"] and (x["url"] or "").startswith("https://") for x in refs), [x for x in refs if not (x["url"] or "").startswith("https://")][:3]
        assert any("synthetic" in n.lower() for n in r["notes"]), r["notes"]

    ctx.check("sources: every dataset has a license, a link and what it is used for; filings and cited references listed", every_dataset_has_a_license_and_a_use)
