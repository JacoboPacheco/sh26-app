"""Smoke checks for the flow feature (the living-grid animation). Loaded by smoke_test.py.

The map animates electricity along every line: direction from the sign of each branch's flow,
speed from |flow| against its rating. That needs /api/grid to carry a signed integer base_flow
per branch and /api/grid/whatif to return flow_mw aligned with those branches. Read-only: these
checks create nothing.

ctx: check(name, fn), request(method, path, data=None, headers=None, expect=200),
auth() -> headers for this run's throwaway user, expected (expected_whatif.json)."""


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def register(ctx):
    exp = ctx.expected
    site = {"lat": exp["lat"], "lon": exp["lon"], "mw": exp["mw"]}
    state = {}

    def grid_has_base_flows():
        g = ctx.request("GET", "/api/grid")
        branches = g["branches"]
        assert branches, "no branches"
        bad = [b["id"] for b in branches if not _is_int(b.get("base_flow"))]
        assert not bad, f"{len(bad)} branches without an integer base_flow, e.g. id {bad[0]}"
        # the base case is calm (max ~95 % in validate.py), so no flow can exceed ~2x its rating
        wild = [b["id"] for b in branches if abs(b["base_flow"]) > 2 * b["rate_mva"] + 1]
        assert not wild, f"{len(wild)} base flows above twice their rating, e.g. id {wild[0]}"
        assert any(b["base_flow"] != 0 for b in branches), "every base flow is zero"
        state["branches"] = branches

    def whatif_flow_per_branch():
        branches = state.get("branches") or ctx.request("GET", "/api/grid")["branches"]
        w = ctx.request("POST", "/api/grid/whatif", site)
        flow = w.get("flow_mw")
        assert isinstance(flow, list), f"flow_mw missing: {type(flow).__name__}"
        assert len(flow) == len(branches), f"{len(flow)} flows for {len(branches)} branches"
        bad = [i for i, v in enumerate(flow) if not _is_int(v)]
        assert not bad, f"{len(bad)} flows are not integers, e.g. index {bad[0]}: {flow[bad[0]]!r}"
        # an overloaded branch really carries more than its rating (flow and loading agree)
        by_id = {b["id"]: i for i, b in enumerate(branches)}
        for o in w["overloaded"]:
            i = by_id[o["id"]]
            assert abs(flow[i]) >= branches[i]["rate_mva"] * 0.99 - 1, (
                f"branch {o['id']} is {o['pct']} % loaded but carries {flow[i]} MW of {branches[i]['rate_mva']} MVA"
            )
        # the drop changes flows: the answer is not just the base case echoed back
        assert any(flow[i] != b["base_flow"] for i, b in enumerate(branches)), "what-if flows equal the base flows"

    ctx.check("flow: /api/grid branches carry an integer signed base_flow", grid_has_base_flows)
    ctx.check("flow: what-if on the expected site returns flow_mw, one int per branch, agreeing with the overloads", whatif_flow_per_branch)
