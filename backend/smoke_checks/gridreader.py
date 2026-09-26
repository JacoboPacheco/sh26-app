"""Smoke checks for Build plans, Reader C (backend/gridreader.py). Loaded by smoke_test.py.

ctx: check(name, fn), request(method, path, data=None, headers=None, expect=200).
Read-only: the route serves the committed report (backend/demo/gridlock/data/gemini_reader.json, made offline by
backend/demo/gridlock/gemini_reader.py). No Gemini call happens here, so it also runs against Render."""

import json
from pathlib import Path

READER_FILE = Path(__file__).resolve().parent.parent / "demo" / "gridlock" / "data" / "gemini_reader.json"
READERS = {"parser_a", "parser_b", "gemini"}
STATUSES = {"gemini_differs", "parser_b_differs", "parser_a_differs", "all_differ"}


def register(ctx):
    state = {}

    def report_served():
        r = ctx.request("GET", "/api/gridlock/reader")
        assert "Gemini proposes; the pipeline's checks decide" in r["advisory"], r.get("advisory")
        assert r["command"].endswith("gemini_reader.py") and r["model"], (r.get("command"), r.get("model"))
        assert set(r["readers"]) == READERS, list(r["readers"])
        assert {s["id"] for s in r["sources"]} == {"desc", "ga_irp"}, r["sources"]
        pr = r["pages_read"]
        assert pr["desc"] >= 40 and pr["ga_table"] >= 10 and pr["ga_detail"] >= 150, pr
        c = r["calls"]
        assert c["requests"] == sum(s["requests"] for s in c["by_stage"].values()) and 0 <= c["failed"] <= c["requests"], c
        assert c["read_at"] and c["read_at"]["first"] <= c["read_at"]["last"], c.get("read_at")
        if READER_FILE.exists():  # the local file (absent when this runs against a deploy from elsewhere)
            # the stage rewrites the file only when its content changes; the timestamp is still left out of the comparison
            local = json.loads(READER_FILE.read_text(encoding="utf-8"))
            strip = lambda d: {k: v for k, v in d.items() if k != "generated_at"}  # noqa: E731
            assert strip(r) == strip(local), "the route doesn't serve the committed report"
        state["r"] = r

    def agreement_numbers():
        a = state["r"]["agreement"]
        fields = {f["field"]: f for f in a["fields"]}
        for want in ("id", "name", "in_service", "kv", "places", "kind"):
            assert want in fields, sorted(fields)
        for f in a["fields"]:
            assert f["compared"] > 0 and 0 <= f["gemini_matches_pipeline"] <= f["compared"], f
            assert f["rate"] is not None and 0 <= f["rate"] <= 100, f
            assert abs(f["rate"] - 100 * f["gemini_matches_pipeline"] / f["compared"]) < 0.06, f
            if f["three_way_rate"] is not None:
                assert 0 <= f["three_way_rate"] <= 100 and f["three_way"] > 0, f
        o = a["overall"]
        assert o["compared"] == sum(f["compared"] for f in a["fields"]) and 0 <= o["rate"] <= 100, o
        assert sum(a["by_status"].values()) == o["compared"], a["by_status"]

    def disagreements_have_pages():
        r = state["r"]
        d = r["disagreements"]
        assert len(d) == r["agreement"]["overall"]["compared"] - r["agreement"]["by_status"]["agree"], len(d)
        for x in d:
            assert isinstance(x["page"], int) and x["page"] >= 1 and x["page_url"].startswith("https://"), x
            assert x["status"] in STATUSES and x["field"] and x["record"], x
            assert "gemini" in x["values"] and "parser_a" in x["values"], x
            assert set(x["values"]) <= READERS, x["values"]

    def rescues_have_verdicts():
        rs = state["r"]["rescues"]
        items = rs["items"]
        assert rs["asked"] == len(items) and rs["passing"] == sum(1 for i in items if i["passes"]), rs
        assert rs["passing"] == rs["passing_from_title"] + rs["passing_from_page"], rs
        for i in items:
            assert i["id"] and isinstance(i["page"], int) and i["page"] >= 1 and i["reasons_before"], i["id"]
            assert i["verdict"], i["id"]
            for t in i["trials"]:
                ids = {c["id"] for c in t["checks"]}
                assert {"located", "in_region", "place_named"} <= ids, (i["id"], ids)
                for c in t["checks"]:
                    assert c["status"] in ("pass", "warn", "fail") and isinstance(c["blocking"], bool), (i["id"], c)
                failed = [c["id"] for c in t["checks"] if c["blocking"] and c["status"] == "fail"]
                assert t["passes"] == (not failed) and t["blocking_failed"] == failed, (i["id"], failed)
            if i["passes"]:
                t = i["trials"][i["chosen"]] if i["trials"] else None
                assert t and t["passes"], i["id"]
                assert all(g["ok"] for g in i["grounding"] if g["name"] in t["names"]), i["id"]
                # a point only the description names is "in this area": low confidence, in the report and in what --apply publishes
                if i["placed_from"] == "page":
                    assert i["capped"], i["id"]
                if i["capped"]:
                    assert t["confidence"] == "low" and i["caution"], (i["id"], t["confidence"])
                    for e in t["endpoints"]:
                        if e["name"] in i["capped"]:
                            assert e["confidence"] == "low" and e["match"].startswith("in this area"), (i["id"], e)
        assert rs["passing_low_confidence"] == sum(1 for i in items if i["passes"] and i["capped"]), rs
        ap = state["r"]["apply"]
        assert sorted(ap["would_release"]) == sorted(i["id"] for i in items if i["passes"]), ap["would_release"]
        assert set(ap["would_release_title_only"]) <= set(ap["would_release"]), ap
        g = ap["sperry_gate"]
        assert g["before"]["reproduced"] and g["before"]["overlaps_ok"] == 6, g
        assert g["ok"] == (g["after"]["reproduced"] and not g["rematched"] and not g["endpoints_moved_off"]), g
        if ap["applied"] is not None:  # what projects.json holds: an earlier --apply's record
            assert ap["applied"]["released"] and ap["applied"]["at"], ap["applied"]

    ctx.check("gridreader: the Gemini reader report is served (= the committed file) with its advisory label", report_served)
    ctx.check("gridreader: per-field agreement numbers are present, 0-100, and add up", agreement_numbers)
    ctx.check("gridreader: every disagreement has its PDF page and the readers' values", disagreements_have_pages)
    ctx.check("gridreader: every rescue has the checks' verdicts; passes only when no blocking check failed", rescues_have_verdicts)
