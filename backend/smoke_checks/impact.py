"""Smoke checks for the impact feature (who loses power, by town). Loaded by smoke_test.py.

The towns feed and the map labels group the cascade's `affected` substations by name, so these
check that shape on the hero cascade: every id is a real substation, every MW is positive, every
step's `newly_affected` is [id, MW > 0] pairs that each appear once, and the per-substation MW add
up to the homes counter. Read-only: creates nothing, so it is safe against the deployed backend.

ctx: check(name, fn), request(method, path, data=None, headers=None, expect=200),
auth() -> headers for this run's throwaway user, expected (expected_whatif.json)."""

HOMES_PER_MW = 700  # backend/powerflow.py


def register(ctx):
    state = {}

    def hero_cascade():
        if "cascade" not in state:
            h = ctx.expected["hero"]
            state["subs"] = {s["id"]: s for s in ctx.request("GET", "/api/grid")["subs"]}
            state["cascade"] = ctx.request("POST", "/api/grid/cascade", {"lat": h["lat"], "lon": h["lon"], "mw": h["mw"]})
        return state["subs"], state["cascade"]

    def test_affected_are_substations():
        subs, c = hero_cascade()
        aff = c["affected"]
        assert isinstance(aff, dict) and aff, f"hero cascade lost no load: affected={aff!r}"
        unknown = [k for k in aff if not str(k).isdigit() or int(k) not in subs]
        assert not unknown, f"{len(unknown)} affected ids not in /api/grid, e.g. {unknown[0]!r}"
        bad = {k: v for k, v in aff.items() if not (isinstance(v, (int, float)) and v > 0)}
        assert not bad, f"affected MW not positive: {list(bad.items())[:3]}"
        nameless = [k for k in aff if not str(subs[int(k)].get("name") or "").strip()]
        assert not nameless, f"affected substations with no name to show: {nameless[:3]}"

    def test_newly_affected_pairs():
        subs, c = hero_cascade()
        seen = []
        for st in c["steps"]:
            for e in st["newly_affected"]:
                assert isinstance(e, list) and len(e) == 2, f"step {st['n']}: entry {e!r} is not an [id, MW] pair"
                sid, mw = e
                assert isinstance(sid, int) and sid in subs, f"step {st['n']}: {sid!r} is not a substation in /api/grid"
                assert isinstance(mw, (int, float)) and mw > 0, f"step {st['n']}: substation {sid} lost {mw!r} MW"
                seen.append(sid)
        assert seen, "no step names a substation losing load"
        assert len(seen) == len(set(seen)), "a substation is announced as newly affected twice"
        missing = {int(k) for k in c["affected"]} - set(seen)
        assert not missing, f"{len(missing)} substations dark at the end were never announced, e.g. {sorted(missing)[:3]}"

    def test_affected_adds_up_to_homes():
        _, c = hero_cascade()
        total = sum(c["affected"].values()) * HOMES_PER_MW
        # each value is rounded to 0.1 MW and anything under 0.1 MW is left out
        slack = 0.1 * HOMES_PER_MW * (len(c["affected"]) + 10)
        assert abs(total - c["homes"]) <= slack, f"towns add up to {total:,.0f} homes, the counter says {c['homes']:,}"

    ctx.check("impact: hero cascade's affected ids are named substations with MW > 0", test_affected_are_substations)
    ctx.check("impact: every step's newly_affected is [substation id, MW > 0], each once", test_newly_affected_pairs)
    ctx.check("impact: affected MW per substation adds up to the homes counter", test_affected_adds_up_to_homes)
