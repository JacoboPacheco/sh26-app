"""Gemini tries to beat the capacity plan: Strengthen the grid's headline number (capacity.py: how many campuses of one
size the grid carries AT ONCE, and the cheapest upgrades for each next one) gets an AI challenger that the engine
referees. CLAUDE.md -> Decisions -> GEMINI MAX and AI SURFACES: Gemini proposes, the engine verifies and prices, a
labeled fallback (the engine's plan stands alone).

THE BAR. The engine's always-on plan cut at the page's default budget: the largest step at or under $50 million (the
frontend's DEFAULT_CAP_BUDGET; Florida at 1 GW: seven campuses for $36.6 million).
ASK. Gemini gets the plan step by step (the site, each upgrade with its rating before and after and its cost, what
stopped the campus), the candidate sites with the room each has left once the bar's campuses and upgrades are in
place and the lines that bind them (the MVA each would need with a full campus there: a linear estimate), the weak
points, every listed line's price rule (costs.py's figures, high end) and the plants' room. It answers with ONE plan
as structured output (a JSON schema): every campus site in the order they connect, every raise as {branch_id,
to_mva}, which claim (one more campus for no more than the bar's cost, or the same number for less) and why.
VERIFY. The engine re-runs the plan: one exact power-flow solve with every campus and every raise at once (no line
past its rating that wasn't already over with no campus, no load the plants can't supply); raises are snapped UP to
the standard 50 MVA steps and capped at 5x the original rating (each change noted); the engine prices the raises
itself (Study.cost; Gemini's own cost figures are never read) and compares the plan with the engine's own for the
same number of campuses; a plan that holds also runs through the full cascade engine (nothing may trip, no one may
lose power).
REVISE. One revision round with the engine's findings: the lines over their rating and by how much, the raise each
would need and what it costs, what the plan really costs against the bar.
STATUS. beat (verified, at least the bar's campuses, and cheaper than the engine's plan for that many by more than a
rounding margin, or more campuses than the engine's plan ever reaches for no more than its whole plan costs), matched
(verified, the same cost within the margin), lost (every proposal failed, cost more, or carried fewer campuses),
not_configured, offline (Gemini didn't answer), error, skipped (no paid step to challenge), pending (opening(): the
study is published at once and the challenge lands in it when the engine has judged it; unlock._run_job). A win is
claimed only when the engine verified it; savings are reported only for a verified plan.

The trace (proposal -> engine verdict -> findings sent back -> revision) has the row shape
frontend/src/features/ai/AgentTrace.jsx renders. Every number describes the SYNTHETIC grid model (Breakthrough Energy /
Texas A&M), not any real utility's network; costs are labeled estimates.
"""

import logging
import math
import re
import time

import numpy as np
from starlette.concurrency import run_in_threadpool

import capacity
import llm

log = logging.getLogger("uvicorn.error")

BUDGET = 50e6  # the page's default budget: the largest plan step at or under this (capacity.js DEFAULT_CAP_BUDGET)
ROUNDS = 2  # one proposal, one revision with the engine's findings
TIMEOUT_S = 15
SITES_SHOWN = 30  # candidate sites shown to Gemini: the cheapest next campus by the engine's own linear price
SITE_LINES = 4  # other binding lines shown per site
KEY_MAX = 24  # the elements that matter (the distribution-factor table's columns)
LOAD_MIN = 10.0  # MW: smaller loadings are left out of a site's row
LOADS_SHOWN = 4
MOVERS = 6  # per raise a moved campus could save: the movable sites that put the least on it
NEAR_MW = 10.0  # an element whose flow is within this of its original rating: its raise (or its limit) can hinge on one campus
POINTS = 6  # weak points shown
AHEAD = 5  # the engine's steps shown past the bar
PRICE_LINES = 90
EXTRA_MAX = 4  # a proposal may carry at most the bar's campuses + this many
RAISES_MAX = 60
TOL_SHARE, TOL_MIN = 0.02, 250_000.0  # "the same cost": within 2 % or $250k of the engine's
OVER_SHOWN = 6
STEP = 50.0
REASON_MAX = 320  # characters of Gemini's own sentence kept for the trace

SYSTEM = (
    "You are a careful transmission planner competing with a greedy search engine. The grid is a SYNTHETIC model "
    "(Breakthrough Energy / Texas A&M), not any real utility's network, and describes no real project. Never name real "
    "companies, utilities or projects. Use only the ids you are given. Reply with JSON only."
)

SCHEMA = {
    "type": "object",
    "properties": {
        "claim": {"type": "string", "enum": ["more", "less"], "description": "more: one more campus for no more than the bar's cost; less: the same number for less"},
        "sites": {"type": "array", "items": {"type": "integer"}, "description": "every campus site id, in the order they connect"},
        "raises": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"branch_id": {"type": "integer"}, "to_mva": {"type": "number"}},
                "required": ["branch_id", "to_mva"],
            },
            "description": "every raised line or transformer with its FINAL rating in MVA",
        },
        "reason": {"type": "string", "description": "one plain sentence on which sites and lines change and why; no cost figures (the engine prices the plan)"},
    },
    "required": ["claim", "sites", "raises", "reason"],
}

RULES = (
    "The engine re-solved Gemini's whole plan at once (every campus and every raise): no line or transformer past its "
    "rating that wasn't already over with no campus, no load the plants can't supply; each raise sized by the engine's "
    "own rule (the flow plus a 10 % margin, in 50 MVA steps, within 5x the original rating) and priced by the engine at "
    "the high end (Gemini's own figures are not used); then the full cascade: nothing may trip."
)

# ---------------------------------------------------------------------------------- words
_WORDS = ["no", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"]
_ORD = ["zeroth", "first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth", "ninth", "tenth", "eleventh", "twelfth"]


def _count(n: int) -> str:
    return _WORDS[n] if 0 <= n < len(_WORDS) else f"{n:,}"


def _ordinal(n: int) -> str:
    if n < len(_ORD):
        return _ORD[n]
    s = "th" if 11 <= n % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{s}"


def _a_ordinal(n: int) -> str:
    w = _ordinal(n)
    return ("an " if w.startswith(("e", "8", "11th", "18")) else "a ") + w


def _m(x: float) -> str:
    """ "$36.6M", "$1.2B", "$750k" (the frontend's shortMoney)."""
    v = float(x or 0)
    if v >= 1e9:
        s = f"{v / 1e9:.{1 if v >= 1e10 else 2}f}".rstrip("0").rstrip(".")
        return f"${s}B"
    if v >= 1e6:
        s = f"{v / 1e6:.{0 if v >= 1e8 else 1}f}"
        return f"${s[:-2] if s.endswith('.0') else s}M"
    if v >= 1e3:
        return f"${round(v / 1e3)}k"
    return f"${round(v)}"


def _join(xs: list[str]) -> str:
    xs = [x for x in xs if x]
    if len(xs) <= 1:
        return "".join(xs)
    return ", ".join(xs[:-1]) + " and " + xs[-1]


def _campuses(n: int) -> str:
    return f"{_count(n)} {'campus' if n == 1 else 'campuses'}"


# ---------------------------------------------------------------------------------- the bar
def bar_of(m: dict) -> int:
    """How many campuses the page's default budget connects (capacity.js defaultCapBudget + capWithin): 0 when the
    plan has no paid step to challenge."""
    stops = [0.0]
    for st in m.get("steps") or []:
        if not st["free"] and st["cum_cost"]["high"] > stops[-1] + 0.5:
            stops.append(float(st["cum_cost"]["high"]))
    if len(stops) < 2:
        return 0
    within = [v for v in stops if v <= BUDGET]
    budget = within[-1] if len(within) > 1 else stops[1]
    return engine_within(m, budget)


def engine_within(m: dict, money: float) -> int:
    """Campuses the engine's plan connects with `money` (high end): every step whose running total fits."""
    n = 0
    for st in m.get("steps") or []:
        if st["cum_cost"]["high"] > money + 0.5:
            break
        n = st["n"]
    return n


def engine_cost(m: dict, n: int) -> float | None:
    """What the engine's plan spends (high end) for n campuses at once; None past where it stops."""
    steps = m.get("steps") or []
    if n <= 0:
        return 0.0
    return float(steps[n - 1]["cum_cost"]["high"]) if n <= len(steps) else None


# ---------------------------------------------------------------------------------- the case Gemini sees
class _Case:
    """Everything the challenge needs from the study, built once in the engine's thread."""

    def __init__(self, study, m: dict, bar_n: int):
        import unlock

        self.u = unlock
        self.s = study
        self.m = m
        self.g = g = study.g
        self.mw = float(study.mw)
        self.cands = study.cands
        self.ids = {int(g.sub_ids[c[0]]): j for j, c in enumerate(self.cands)}
        self.steps = m["steps"]
        self.today = int(m["today"])
        self.bar_n = bar_n
        self.bar = self.steps[bar_n - 1]["cum_cost"]
        self.cap = np.floor(g.rate * unlock.MAX_RERATE * 10) / 10
        self.fresh = (np.abs(g.base.flow) < g.rate) & g.base.active  # a line already over with no campus doesn't count
        # the engine's plan at the bar: its sites and every raise at its latest rating
        self.eng_sites = [self.ids[int(st["site"]["id"])] for st in self.steps[:bar_n] if int(st["site"]["id"]) in self.ids]
        self.eng_n = {int(st["site"]["id"]): st["n"] for st in self.steps}
        self.eng_raise: dict[int, float] = {}
        for st in self.steps[:bar_n]:
            for p in st["projects"]:
                i = g.br_index[int(p["branch_id"])]
                self.eng_raise[i] = max(self.eng_raise.get(i, 0.0), float(p["rating_after_mva"]))
        self.rate = g.rate.copy()
        for i, L in self.eng_raise.items():
            self.rate[i] = max(self.rate[i], L)
        self._rooms()

    # ------------------------------------------------------------------ the bar's state, as a planner's table
    def _rooms(self) -> None:
        """With the bar's campuses and upgrades in place: every candidate site's room (capacity.py's sensitivity: MW
        until the first line reaches its rating), the lines a full campus there pushes past their rating, and what a
        campus at each site puts on the elements that matter (a distribution-factor table, linear): the lines and
        transformers the engine's plan raises, and the ones a campus anywhere pushes over."""
        g, mw = self.g, self.mw
        extra = np.zeros(g.n)
        for j in self.eng_sites:
            extra[self.cands[j][1]] += mw
        st = g.solve(g.base.active, extra, self.rate)
        self.s.solves += 1
        self.flow = st.flow
        buses = np.array([c[1] for c in self.cands], dtype=int)
        dF = g._sensitivity(buses, capacity._weights(g, st, extra))
        t = capacity._limits(st.flow, dF, self.rate, self.fresh)
        room = t.min(axis=0)
        used = set(self.eng_sites)
        # MW toward each element's limit per full campus at each site (negative: it relieves the element)
        toward = mw * dF * np.sign(np.where(np.abs(st.flow) > 1e-6, st.flow, 1.0))[:, None]
        rows: list[dict] = []
        binds: dict[int, int] = {}
        for j in range(len(self.cands)):
            if j in used:
                continue
            col = t[:, j]
            bind = np.flatnonzero(col < mw)
            bind = bind[np.argsort(col[bind], kind="stable")]
            needs = []
            for i in bind:
                need = abs(float(st.flow[i] + mw * dF[i, j]))
                needs.append({"i": int(i), "need": need, "fixable": need <= float(self.cap[i]) + 1e-6})
                binds[int(i)] = binds.get(int(i), 0) + 1
            # the engine's own price for this site as the next campus (linear): every line it pushes over, sized by its rule
            est = sum(self.price(x["i"], self.size(x["i"], x["need"]))[1] - self.price(x["i"], float(self.rate[x["i"]]))[1] for x in needs if x["fixable"])
            rows.append({"j": j, "room": float(min(room[j], 1e9)), "needs": needs, "est": est if all(x["fixable"] for x in needs) else math.inf})
        n_other = max(len(rows), 1)
        # the elements that matter: the engine's raises (at the bar and just past it) and the lines most sites push over
        key: dict[int, None] = {}
        for st_ in self.steps[: self.bar_n + AHEAD]:
            for p in st_["projects"]:
                key[g.br_index[int(p["branch_id"])]] = None
        self.common = [i for i, k in sorted(binds.items(), key=lambda kv: -kv[1]) if k >= 0.8 * n_other]
        for i in self.common:
            key[i] = None
        self.key = list(key)[:KEY_MAX]
        self.toward = {i: toward[i] for i in self.key}
        # the elements within NEAR_MW of their original rating: a raise a moved campus could make unnecessary, or a line
        # one more campus pushes over; every site's row shows its loading on these, to a tenth of a MW
        self.near = [i for i in self.key if abs(abs(float(st.flow[i])) - float(g.rate[i])) <= NEAR_MW]
        # which sites to show: the cheapest next campus by the engine's own linear price
        self.shown = sorted((r for r in rows if math.isfinite(r["est"])), key=lambda r: (r["est"], -r["room"]))[:SITES_SHOWN]
        # the sites a campus could move to with nothing local in the way (a full campus there pushes no line over except
        # the elements that matter), for the raises-a-move-could-save view
        self.movable = [r["j"] for r in rows if math.isfinite(r["est"]) and all(x["i"] in key for x in r["needs"])]
        shown = {r["j"] for r in self.shown}
        self.shown += [r for r in rows if r["j"] in set(self.movable) - shown and r["j"] in set(self._least_movers())]
        self.shown.sort(key=lambda r: (r["est"], -r["room"]))

    def _least_movers(self) -> list[int]:
        """The movable sites that put the least on each raise a moved campus could save (MOVERS per raise)."""
        out: list[int] = []
        for i in self.saveable():
            out += sorted(self.movable, key=lambda j: float(self.toward[i][j]))[:MOVERS]
        return out

    def saveable(self) -> list[int]:
        """The engine's raises within NEAR_MW of their original rating: a moved campus could make them unnecessary."""
        g = self.g
        return [i for i in self.key if i in self.eng_raise and 0.0 < abs(float(self.flow[i])) - float(g.rate[i]) <= NEAR_MW]

    def size(self, i: int, flow: float) -> float:
        """The rating the engine gives a raised line carrying `flow`: its flow plus the 10 % margin (unlock.MARGIN),
        rounded up to 50 MVA, at most 5x; a line the engine's plan raised may keep that plan's rating when it carries
        the flow (the engine's own sizing, path by path)."""
        lv = min(math.ceil(abs(flow) * self.u.MARGIN / STEP - 1e-9) * STEP, float(self.cap[i]))
        e = self.eng_raise.get(i)
        if e is not None and e + 1e-6 >= abs(flow):
            lv = min(lv, e)
        return lv

    def loads(self, j: int) -> str:
        """ "33767 +3.2, 33762 +15.1, ... | 34217 +55, 34231 +55" (MW toward each element's limit: every element near its
        original rating to a tenth, then the biggest of the others)."""
        near = [f"{self.bid(i)} {float(self.toward[i][j]):+.1f}" for i in self.near]
        xs = sorted(((i, float(self.toward[i][j])) for i in self.key if i not in self.near), key=lambda kv: -abs(kv[1]))
        xs = [(i, v) for i, v in xs if abs(v) >= LOAD_MIN][:LOADS_SHOWN]
        rest = [f"{self.bid(i)} {v:+,.0f}" for i, v in xs]
        return ", ".join(near + rest) or "little on any of them"

    # ------------------------------------------------------------------ names and prices
    def site_id(self, j: int) -> int:
        return int(self.g.sub_ids[self.cands[j][0]])

    def area(self, j: int) -> str:
        return self.cands[j][2]

    def short(self, i: int) -> str:
        return self.u._branch(self.g, i)["short"]

    def bid(self, i: int) -> int:
        return int(self.g.br_ids[i])

    def price(self, i: int, level: float) -> tuple[float, float]:
        lo, hi, _ = self.s.cost(i, level)
        return lo, hi

    def price_rule(self, i: int) -> str:
        g = self.g
        b = self.u._branch(g, i)
        orig = float(g.rate[i])
        cap = float(self.cap[i])
        now = self.rate[i]
        head = f"- {b['branch_id']} {b['short']} ({b['kv']:.0f} kV): original {orig:,.0f} MVA" + (f", {now:,.0f} in the engine's plan" if now > orig + 1e-6 else "") + f", max {cap:,.0f}: "
        if b["kind"] == "transformer":
            lv = min(orig + STEP, cap)
            per = self.price(i, lv)[1] / lv if lv > 0 else 0.0
            return head + f"${per / 1e3:,.1f}k per MVA of its new rating (a new transformer)"
        two = min(2.0 * orig, cap)
        a = self.price(i, two)[1]
        c = self.price(i, cap)[1]
        _, _, it = self.s.cost(i, two)
        miles = f", {it['miles']:,.1f} miles" if it and it.get("miles") else ""
        return head + f"{_m(a)} for any rating up to {two:,.0f} MVA (rebuilt{miles}); {_m(c)} above that (a new double-circuit line)"

    # ------------------------------------------------------------------ the prompt
    def prompt(self, feedback: str = "") -> str:
        g, mw = self.g, self.mw
        from grid import REGIONS

        bar_hi = self.bar["high"]
        next_cost = engine_cost(self.m, self.bar_n + 1)
        rows = []
        lines: dict[int, None] = {}
        for st in self.steps[: min(len(self.steps), self.bar_n + AHEAD)]:
            if st["n"] == self.bar_n + 1:
                rows.append(f"--- the bar: the first {self.bar_n} campuses, {_m(bar_hi)} (high end). The engine's next steps: ---")
            site = f"{st['n']}. site {st['site']['id']} {st['site']['area']}"
            if not st["projects"]:
                rows.append(f"{site}: fits with no new upgrade")
                continue
            ups = []
            for p in st["projects"]:
                i = g.br_index[int(p["branch_id"])]
                lines[i] = None
                ups.append(f"{p['branch_id']} {p['short']} {p['rating_before_mva']:,.0f} -> {p['rating_after_mva']:,.0f} MVA ({_m(p['cost']['high'])})")
            blk = st.get("blocked_by") or {}
            stop = f"; stopped first by {blk['branch_id']} {blk['short']}" if blk.get("branch_id") else ""
            rows.append(f"{site}: raised " + "; ".join(ups) + stop + f"; running total {_m(st['cum_cost']['high'])}")
        keyset = set(self.key)
        elements = []
        for i in self.key:
            lines[i] = None
            orig = float(g.rate[i])
            now = float(self.rate[i])
            f = abs(float(self.flow[i]))
            tag = ""
            if now > orig + 1e-6 and f - orig <= NEAR_MW:
                tag = f"; only {f - orig:,.1f} MW past its original rating: take that much off it and its {_m(self.price(i, now)[1])} raise isn't needed"
            elif now <= orig + 1e-6 and i in self.near:
                tag = f"; {orig - f:,.1f} MW below its rating"
            if i in self.common:
                tag += "; a campus at almost any site pushes it over"
            elements.append(
                f"- {self.bid(i)} {self.short(i)}: {f:,.1f} MW on {now:,.0f} MVA"
                + (f" (raised from {orig:,.0f})" if now > orig + 1e-6 else "")
                + tag
            )
        mine = [f"- {self.site_id(j)} {self.area(j)} (campus {self.eng_n.get(self.site_id(j))}): {self.loads(j)}" for j in self.eng_sites]
        saves = []
        for i in self.saveable():
            f = abs(float(self.flow[i]))
            orig = float(g.rate[i])
            ours = sorted(((self.area(j), float(self.toward[i][j])) for j in self.eng_sites), key=lambda kv: -kv[1])
            least = sorted(self.movable, key=lambda j: float(self.toward[i][j]))[:MOVERS]
            saves.append(
                f"- {self.bid(i)} {self.short(i)}: {f - orig:,.1f} MW past its original {orig:,.0f} MVA ({_m(self.price(i, float(self.rate[i]))[1])} raise). "
                "The engine's campuses put on it: " + ", ".join(f"{a} {v:+.1f}" for a, v in ours)
                + ". Sites with nothing local in the way that put the least on it: "
                + ", ".join(f"{self.site_id(j)} {self.area(j)} {float(self.toward[i][j]):+.1f}" for j in least)
            )
        sites = []
        for r in self.shown:
            j = r["j"]
            other = [x for x in r["needs"] if x["i"] not in keyset]
            parts = []
            for x in other[:SITE_LINES]:
                lines[x["i"]] = None
                parts.append(f"{self.bid(x['i'])} {self.short(x['i'])} ({self.rate[x['i']]:,.0f} MVA now) to ~{x['need']:,.0f} MVA")
            more = len(other) - SITE_LINES
            sites.append(
                f"- {self.site_id(j)} {self.area(j)}: {self.loads(j)}"
                + (f"; also pushes " + "; ".join(parts) + (f" and {more} more" if more > 0 else "") if parts else "")
                + f"; as the next campus the engine would pay about {_m(r['est'])}"
            )
        pts = []
        for p in self.s.points[:POINTS]:
            i = g.br_index[p["branch_id"]]
            lines[i] = None
            pts.append(f"- {p['branch_id']} {p['short']}: blocks {p['sites_blocked']} sites alone, first to fail in {p['first_fail']} simulated cascades")
        prices = [self.price_rule(i) for i in list(lines)[:PRICE_LINES]]
        pl = self.s.capacity.get("plants") or {}
        eng_sites = [self.site_id(j) for j in self.eng_sites]
        eng_raises = [{"branch_id": self.bid(i), "to_mva": round(L, 1)} for i, L in sorted(self.eng_raise.items(), key=lambda kv: -kv[1])]
        more_n = self.bar_n + 1
        return (
            f"A SYNTHETIC grid model of {REGIONS[self.s.code]['name']} (not a real utility's network). The engine connects always-on {mw:,.0f} MW "
            "data-center campuses one at a time, each at the candidate site where it fits with every campus so far (an exact DC power-flow "
            "solve: no line or transformer past its rating), and when none fits it buys the cheapest upgrades for the next one: a greedy search "
            f"that tries only the {capacity.TRY} sites closest to fitting each time.\n\n"
            "THE ENGINE'S PLAN (campus n, site id and town; each raise as line id, name, rating before -> after, its cost at the high end):\n"
            + "\n".join(rows)
            + f"\n\nTHE BAR: {self.bar_n} campuses at once for {_m(bar_hi)} (the page's default budget: the largest step at or under {_m(BUDGET)}). "
            + (f"The engine's plan needs {_m(next_cost)} for {more_n}.\n" if next_cost is not None else f"The engine's plan stops at {len(self.steps)}.\n")
            + f"Its {self.bar_n} sites: {eng_sites}\nIts raises at the bar (final ratings): {eng_raises}\n\n"
            + (
                f"The cheapest next campus the engine can find with its {self.bar_n} in place costs about {_m(self.shown[0]['est'])} more "
                f"({self.area(self.shown[0]['j'])}), so \"more\" fits the bar only if the first {self.bar_n} cost less.\n"
                if self.shown
                else ""
            )
            + f"YOUR TASK: propose ONE plan that beats it: {_campuses(more_n)} for no more than {_m(bar_hi)} (claim \"more\"), or the same {self.bar_n} "
            "for less (claim \"less\"). The greedy search never goes back: ideas it can miss are moving an earlier campus (even one that fit for "
            "free) to a site that loads the raised elements less, so an element raised only a little past its original rating (compare its flow "
            "with the rating it was raised from) needs no raise at all; one raise that serves two campuses; a site that needs only cheap raises; "
            "a transformer raise instead of a line rebuild.\n\n"
            f"THE ELEMENTS THAT MATTER, with the engine's {self.bar_n} campuses and raises in place (id, name: flow on its rating now):\n"
            + "\n".join(elements)
            + (
                "\n\nRAISES A MOVED CAMPUS COULD SAVE (a raise isn't needed once the element's flow is back within its original rating; a move "
                "must not push another element past its rating either):\n" + "\n".join(saves)
                if saves
                else ""
            )
            + f"\n\nWHAT EACH CAMPUS PUTS ON THEM (a distribution-factor table, linear: MW of flow toward each element's limit per full {mw:,.0f} MW "
            "campus, as \"element id +MW\"; a negative number relieves it; removing a campus takes its numbers away).\n"
            f"The engine's {self.bar_n} campuses:\n"
            + "\n".join(mine)
            + "\nOther candidate sites (and the other lines a full campus there would push past their rating, with the rating each would need):\n"
            + "\n".join(sites)
            + "\n\nWEAK POINTS (found by simulating a campus at every site, one at a time):\n"
            + "\n".join(pts)
            + "\n\nPRICES (high end, from each line's ORIGINAL rating; a plan costs the sum, over every raised line, of its price at its final "
            f"rating: the engine's {_m(bar_hi)} is exactly that sum over its raises at the bar):\n"
            + "\n".join(prices)
            + (
                f"\n\nPLANTS: at this load the model's plants can supply {pl.get('room_mw', 0) / 1000:,.1f} GW of new load with a "
                f"{pl.get('reserve_pct', 15):.0f} % planning reserve kept; the engine's plan and yours are judged on the wires and on load the plants "
                "can actually supply (nothing shed).\n"
                if pl
                else "\n"
            )
            + "\nRULES: use only site ids and line ids from this message; sites must be distinct; list EVERY campus (the engine's you keep and yours) "
            "in the order they connect and EVERY raise with its FINAL rating (keep the engine's raises you still need); ratings come in 50 MVA steps "
            f"and at most 5x the original; claim \"more\" lists exactly {more_n} sites, claim \"less\" exactly {self.bar_n}. The engine re-solves your whole plan at once: every line must "
            "carry its flow within its rating. It sizes each raise by its own rule (the flow plus a 10 % margin, rounded up to 50 MVA; a line the "
            "engine's plan raised may keep that plan's rating) and prices it itself; add up your cost from the price list before answering."
            + (f"\n\n{feedback}" if feedback else "")
            + '\n\nAnswer only as JSON: {"claim": "more" or "less", "sites": [site ids in order], "raises": [{"branch_id": 123, "to_mva": 450}], '
            '"reason": "one plain sentence on which sites and lines change and why, with no cost figures"}'
        )

    # ------------------------------------------------------------------ clean
    def clean(self, raw) -> dict:
        """Gemini's answer as the engine will run it: known distinct sites, real lines, ratings snapped up to 50 MVA
        steps and capped at 5x (every change noted)."""
        g = self.g
        raw = raw if isinstance(raw, dict) else {}
        notes: list[str] = []
        sites: list[int] = []
        for x in raw.get("sites") or []:
            try:
                sid = int(x)
            except (TypeError, ValueError):
                continue
            j = self.ids.get(sid)
            if j is None:
                notes.append(f"site {sid} is not a candidate site: left out")
                continue
            if j in sites:
                notes.append(f"site {sid} listed twice: counted once")
                continue
            sites.append(j)
        limit = self.bar_n + EXTRA_MAX
        if len(sites) > limit:
            notes.append(f"{len(sites)} sites listed: the first {limit} kept")
            sites = sites[:limit]
        raises: dict[int, float] = {}
        for u in (raw.get("raises") or [])[: RAISES_MAX * 2]:
            try:
                bid, to = int(u["branch_id"]), float(u["to_mva"])
            except (KeyError, TypeError, ValueError):
                continue
            if bid not in g.br_index or not math.isfinite(to):
                notes.append(f"line {bid} is not in the model: left out")
                continue
            i = g.br_index[bid]
            orig = float(g.rate[i])
            if to <= orig + 1e-6:
                continue  # not a raise
            level = math.ceil(to / STEP - 1e-9) * STEP
            if level > self.cap[i] + 1e-6:
                notes.append(f"line {bid} to {to:,.0f} MVA is past 5x its rating: capped at {self.cap[i]:,.0f}")
                level = float(self.cap[i])
            elif abs(level - to) > 0.05:
                notes.append(f"line {bid} to {to:,.0f} MVA rounded up to {level:,.0f} (50 MVA steps)")
            raises[i] = max(raises.get(i, 0.0), level)
            if len(raises) >= RAISES_MAX:
                break
        claim = raw.get("claim") if raw.get("claim") in ("more", "less") else ("more" if len(sites) > self.bar_n else "less")
        reason = " ".join(str(raw.get("reason") or "").split())
        if len(reason) > REASON_MAX:  # cut at a word, never mid-word
            reason = reason[:REASON_MAX].rsplit(" ", 1)[0].rstrip(",;:") + "…"
        return {"sites": sites, "raises": raises, "claim": claim, "reason": reason, "notes": notes[:6]}

    # ------------------------------------------------------------------ verify
    def check(self, p: dict) -> dict:
        """One exact solve with every campus and raise, priced by the engine; a plan that holds also runs the cascade."""
        g, mw = self.g, self.mw
        t0 = time.perf_counter()
        js = p["sites"]
        n = len(js)
        rate = g.rate.copy()
        for i, L in p["raises"].items():
            rate[i] = max(rate[i], L)
        extra = np.zeros(g.n)
        for j in js:
            extra[self.cands[j][1]] += mw
        levels = dict(p["raises"])

        def priced(lv: dict[int, float]) -> dict:
            return {"low": round(sum(self.price(i, L)[0] for i, L in lv.items())), "high": round(sum(self.price(i, L)[1] for i, L in lv.items()))}

        out = {"campuses": n, "cost": priced(levels), "levels": levels, "sized": [], "over": [], "lost_mw": 0.0, "holds": False, "calm": None, "tripped": None}
        if not n:
            out["ms"] = round((time.perf_counter() - t0) * 1000)
            return out
        st = g.solve(g.base.active, extra, rate)
        self.s.solves += 1
        idx = np.flatnonzero((np.abs(st.flow) > rate + 1e-6) & self.fresh)
        # the engine sizes every raise that holds by its own rule (never smaller than Gemini asked): trimming the engine's
        # margin can't count as a better plan
        sized = []
        for i, L in p["raises"].items():
            f = abs(float(st.flow[i]))
            if f > L + 1e-6:
                continue  # over its rating as proposed: reported below
            lv = self.size(i, f)
            if lv > L + 1e-6:
                levels[i] = lv
                sized.append({"branch_id": self.bid(i), "short": self.short(i), "asked_mva": L, "sized_mva": lv})
        out["levels"] = levels
        out["sized"] = sized
        out["cost"] = priced(levels)
        over = []
        for i in idx:
            flow = abs(float(st.flow[i]))
            need = self.size(int(i), flow)
            add = max(self.price(int(i), need)[1] - self.price(int(i), float(rate[i]))[1], 0.0)
            over.append(
                {
                    "branch_id": self.bid(int(i)),
                    "short": self.short(int(i)),
                    "flow_mw": round(flow, 1),
                    "rating_mva": round(float(rate[i]), 1),
                    "pct": round(flow / float(rate[i]) * 100.0, 1),
                    "need_mva": need,
                    "add_high": round(add),
                    "fixable": flow <= float(self.cap[i]) + 1e-6,
                }
            )
        over.sort(key=lambda o: -o["pct"])
        out["over"] = over
        out["lost_mw"] = round(float(st.lost_mw), 1)
        out["holds"] = not over and st.lost_mw <= 0.5
        if out["holds"]:
            ups = {self.bid(i): float(L) for i, L in levels.items()}
            c = g.cascade_case(extra, None, ups)
            self.s.verify_cascades += 1
            out["calm"] = bool(c["total_steps"] == 0 and self.u._hit(c) == 0 and c["lost_mw"] <= 0.5)
            if not out["calm"]:
                first = next((s for s in c.get("steps") or [] if s.get("tripped")), None)
                if first:
                    out["tripped"] = self.short(g.br_index[int(first["tripped"][0])])
        out["ms"] = round((time.perf_counter() - t0) * 1000)
        return out

    # ------------------------------------------------------------------ judge
    def judge(self, p: dict, v: dict) -> str:
        """beat | matched | dearer | fewer | fails | shed | trips | empty"""
        if not v["campuses"]:
            return "empty"
        if v["lost_mw"] > 0.5 and not v["over"]:
            return "shed"
        if v["over"]:
            return "fails"
        if not v["calm"]:
            return "trips"
        n = v["campuses"]
        if n < self.bar_n:
            return "fewer"
        ec = engine_cost(self.m, n)
        if ec is None:
            # more campuses than the engine's plan ever reaches (its search may have stopped on its time limit): a win
            # only for no more than the engine's whole plan costs, which its next step could only add to
            return "beat" if v["cost"]["high"] <= self.last_cost() + 0.5 else "dearer"
        save = ec - v["cost"]["high"]
        tol = max(TOL_SHARE * ec, TOL_MIN)
        return "beat" if save > tol else "matched" if save >= -tol else "dearer"

    def last_cost(self) -> float:
        """What the engine's whole plan costs (high end): its last step's running total."""
        return float(self.steps[-1]["cum_cost"]["high"]) if self.steps else 0.0

    def diff(self, p: dict) -> tuple[list[int], list[int]]:
        """(Gemini's sites the engine's bar doesn't have, the bar's sites Gemini left out)."""
        mine = set(p["sites"])
        eng = set(self.eng_sites)
        return [j for j in p["sites"] if j not in eng], [j for j in self.eng_sites if j not in mine]

    # ------------------------------------------------------------------ words for one attempt
    def what(self, p: dict) -> str:
        """ "an eighth campus at Ocala", "the same seven campuses for less", "seven campuses, one moved to Ocala"."""
        n = len(p["sites"])
        new, gone = self.diff(p)
        towns = [self.area(j) for j in new]
        if n > self.bar_n:
            head = f"{_a_ordinal(n)} campus" if n == self.bar_n + 1 else f"{_campuses(n)} at once"
            if new and len(new) <= n - self.bar_n:
                head += " at " + _join(towns[:3])
            elif new:
                head += f", with {_count(len(new))} new {'site' if len(new) == 1 else 'sites'} ({_join(towns[:3])})"
            return head
        if n == self.bar_n:
            if not new:
                return f"the same {_campuses(n)} for less"
            return f"{_campuses(n)} with {_count(len(gone))} moved to {_join(towns[:3])}"
        return f"only {_campuses(n)}" if n else "no campus the engine could place"

    def phrase(self, p: dict, v: dict, outcome: str) -> str:
        """What happened to one attempt, for the sentence ("took the Orlando 54 transformer to 104 % of its rating")."""
        if outcome == "empty":
            return "named no site the engine could place"
        if outcome == "fails":
            o = v["over"][0]
            k = len(v["over"])
            return f"took the {o['short']} to {o['pct']:.0f} % of its rating" + (f" ({_count(k - 1)} more {'line' if k == 2 else 'lines'} over)" if k > 1 else "")
        if outcome == "shed":
            return "needed more power than the model's plants can make"
        if outcome == "trips":
            return "held in the power flow but tripped " + (f"the {v['tripped']}" if v.get("tripped") else "a line") + " in the full cascade"
        if outcome == "fewer":
            return f"carried only {_campuses(v['campuses'])}"
        if outcome == "dearer":
            ec = engine_cost(self.m, v["campuses"])
            if ec is None:
                return f"cost {_m(v['cost']['high'])} for {_count(v['campuses'])}, more than the engine's whole plan ({_m(self.last_cost())} for {_count(len(self.steps))})"
            return f"cost {_m(v['cost']['high'])} for {_count(v['campuses'])}, more than the engine's {_m(ec)}"
        if outcome == "matched":
            return f"matched the engine: {_campuses(v['campuses'])} for {_m(v['cost']['high'])}"
        return f"beat it: {_campuses(v['campuses'])} for {_m(v['cost']['high'])}"

    # ------------------------------------------------------------------ the findings sent back
    def feedback(self, p: dict, v: dict, outcome: str) -> str:
        bar_hi = self.bar["high"]
        lines = [f"THE ENGINE CHECKED YOUR PLAN ({_campuses(v['campuses'])}: sites {[self.site_id(j) for j in p['sites']]}; {len(p['raises'])} raises):"]
        for o in v["over"][:OVER_SHOWN]:
            fix = f"raising it to {o['need_mva']:,.0f} MVA would add {_m(o['add_high'])}" if o["fixable"] else "more than 5x its rating would be needed: move a campus away from it"
            lines.append(f"- over its rating: {o['branch_id']} {o['short']} carries {o['flow_mw']:,.0f} MW on {o['rating_mva']:,.0f} MVA ({o['pct']:.0f} %); {fix}")
        if len(v["over"]) > OVER_SHOWN:
            lines.append(f"- and {len(v['over']) - OVER_SHOWN} more lines over their rating")
        if v["lost_mw"] > 0.5:
            lines.append(f"- the plants can't supply it: {v['lost_mw']:,.0f} MW of load would be shed")
        if outcome == "trips":
            lines.append(f"- it holds in one solve, but the full cascade trips {v.get('tripped') or 'a line'}")
        for x in v.get("sized") or []:
            lines.append(f"- sized up by the engine's 10 % margin rule: {x['branch_id']} {x['short']} from {x['asked_mva']:,.0f} to {x['sized_mva']:,.0f} MVA")
        lines.append(f"- the engine's price for your raises: {_m(v['cost']['high'])} (high end), against the bar of {_m(bar_hi)} for {self.bar_n}")
        fixable = [o for o in v["over"] if o["fixable"]]
        if v["over"] and len(fixable) == len(v["over"]) and v["lost_mw"] <= 0.5:
            total = v["cost"]["high"] + sum(o["add_high"] for o in fixable)
            lines.append(f"- with those lines raised as the engine sizes them, your plan would cost about {_m(total)}")
        if outcome == "dearer":
            ec = engine_cost(self.m, v["campuses"])
            if ec is None:
                lines.append(f"- it holds, but costs more than the engine's whole plan ({_m(self.last_cost())} for {_count(len(self.steps))}): find something cheaper")
            else:
                lines.append(f"- it holds, but the engine's plan does {_count(v['campuses'])} for {_m(ec)}: find something cheaper")
        elif outcome == "fewer":
            lines.append(f"- it holds, but carries fewer campuses than the bar's {self.bar_n}: list exactly {self.bar_n} sites for \"less\"")
        elif outcome == "matched":
            lines.append("- it holds, but only matches the engine's plan; trimming a rating can't win (the engine sizes every raise), only a change of sites that makes a raise unnecessary can")
        if self.shown and self.shown[0]["est"] > bar_hi * 0.25:
            lines.append(
                f"- remember: one more campus alone adds about {_m(self.shown[0]['est'])} with the engine's {self.bar_n} in place, so {self.bar_n + 1} "
                f"campuses are unlikely to fit {_m(bar_hi)}; the same {self.bar_n} for less is the likelier way to beat it"
            )
        lines.append("- every line you leave out of your raises is back at its original rating")
        for note in p["notes"]:
            lines.append(f"- adjusted: {note}")
        lines.append("Propose ONE revised plan (every campus and every raise with its final rating).")
        return "\n".join(lines)


# ---------------------------------------------------------------------------------- the trace
class _Trace:
    def __init__(self):
        self.rows: list[dict] = []

    def add(self, **row) -> None:
        row["n"] = len(self.rows) + 1
        self.rows.append(row)


def _raises_text(case: _Case, p: dict) -> str:
    rs = sorted(p["raises"].items(), key=lambda kv: -case.price(kv[0], kv[1])[1])
    shown = [f"{case.short(i)} to {L:,.0f} MVA" for i, L in rs[:3]]
    more = len(rs) - 3
    return ("raises " + _join(shown) + (f" and {more} more" if more > 0 else "")) if rs else "no raises"


def _plan(case: _Case, p: dict, v: dict, outcome: str) -> dict:
    """The attempt as the page shows it: placements (the engine's kept sites in its order, then Gemini's new ones)
    and the raises as project records."""
    s, g = case.s, case.g
    eng = case.eng_n
    new, gone = case.diff(p)
    kept = sorted((j for j in p["sites"] if j not in new), key=lambda j: eng.get(case.site_id(j), 10**6))
    order = kept + new
    placements = []
    for k, j in enumerate(order, 1):
        c = case.cands[j]
        sid = case.site_id(j)
        placements.append(
            {
                "n": k,
                "id": sid,
                "area": c[2],
                "lat": round(float(g.sub_lat[c[0]]), 4),
                "lon": round(float(g.sub_lon[c[0]]), 4),
                "engine_n": eng.get(sid) if j in case.eng_sites else None,
                "new": j in new,
            }
        )
    projects = []
    levels = v.get("levels") or p["raises"]  # as the engine sized them
    for i, L in sorted(levels.items(), key=lambda kv: -case.price(kv[0], kv[1])[1]):
        pj = s.project(int(i), float(g.rate[i]), float(L), None, "gemini")
        e = case.eng_raise.get(int(i))
        pj["engine_mva"] = round(e, 1) if e else None  # the engine's plan's rating for this line at the bar (None: it didn't raise it)
        pj["asked_mva"] = round(float(p["raises"].get(i, L)), 1)  # what Gemini asked for (the engine may have sized it up)
        projects.append(pj)
    return {
        "campuses": v["campuses"],
        "cost": v["cost"],
        "placements": placements,
        "dropped": [{"id": case.site_id(j), "area": case.area(j), "engine_n": eng.get(case.site_id(j))} for j in gone],
        "projects": projects,
    }


def _empty(status: str, sentence: str, **extra) -> dict:
    return {
        "status": status,
        "claim": None,
        "campuses": None,
        "cost": None,
        "placements": [],
        "dropped": [],
        "projects": [],
        "reason": "",
        "rounds": 0,
        "calls": 0,
        "trace": [],
        "attempts": [],
        "verified": False,
        "sentence": sentence,
        "rules": RULES,
        "synthetic": True,
        **extra,
    }


def failed() -> dict:
    """The challenge's answer when it raised: the engine's plan stands."""
    return _empty("error", "Gemini's challenge failed on this run; the engine's plan stands.")


def opening(study) -> dict | None:
    """What result["capacity"]["ai"] says before the challenge runs: final when nothing will run (skipped: no paid step
    to challenge; not_configured: no key), else "pending" with the bar, while the challenge runs after the study is
    published (the page never waits for Gemini). None: the study has no capacity section."""
    cap = getattr(study, "capacity", None)
    if cap is None:
        return None
    if study.baseline is not None or not (cap.get("firm") or {}).get("steps"):
        return _empty("skipped", "")
    m = cap["firm"]
    bar_n = bar_of(m)
    if not bar_n:
        return _empty("skipped", "")
    bar = {"campuses": bar_n, "cost": dict(m["steps"][bar_n - 1]["cum_cost"]), "budget": BUDGET, "today": int(m["today"])}
    if not llm.configured():
        return _empty("not_configured", "Gemini isn't set up on this server, so nothing challenged this plan; the engine's plan stands.", bar=bar)
    return _empty(
        "pending",
        f"Gemini is trying to beat this plan ({_campuses(bar_n)} at once for {_m(bar['cost']['high'])}); the engine will re-solve, price and "
        "cascade whatever it proposes.",
        bar=bar,
    )


# ---------------------------------------------------------------------------------- the challenge
_MONEY = re.compile(r"\$\s?\d|\b\d[\d,.]*\s*(?:k|m|million|billion|thousand)\b|\bdollars?\b", re.IGNORECASE)


def _reason_text(reason: str) -> str:
    """Gemini's own words for its plan; any cost figure in them is labeled as its own (the engine prices the plan)."""
    if not reason:
        return ""
    reason = reason if reason.endswith((".", "!", "?", "…")) else reason + "."
    if _MONEY.search(reason):
        return f"In its words: “{reason}” (its own cost figures, not used: the engine prices every plan itself). "
    return f"In its words: “{reason}” "


async def challenge(study) -> dict:
    """Gemini proposes a plan that beats the engine's capacity plan at the default budget; the engine verifies,
    prices and judges it; one revision with the engine's findings. Always returns a dict (see the module docstring).
    It reports no progress: the study is already published (with opening()'s "pending") while this runs."""
    import unlock

    t_start = time.perf_counter()
    first = opening(study)
    if first is None or first["status"] != "pending":
        return first or _empty("skipped", "")
    bar = first["bar"]
    m = study.capacity["firm"]
    bar_n = bar["campuses"]

    def build():
        with unlock._compute_lock:
            return _Case(study, m, bar_n)

    case = await run_in_threadpool(build)
    tr = _Trace()
    bar_hi = bar["cost"]["high"]
    nxt = engine_cost(m, bar_n + 1)
    tr.add(
        round=1,
        actor="engine",
        kind="ask",
        tone="info",
        title=f"Sent Gemini the engine's plan: {_campuses(bar_n)} at once for {_m(bar_hi)}",
        detail=(
            f"Asked for one more campus for no more than that, or the same {_count(bar_n)} for less"
            + (f" (the engine's plan needs {_m(nxt)} for {_count(bar_n + 1)})" if nxt is not None else "")
            + f". Also sent: what each of its campuses and {len(case.shown)} other candidate sites put on the lines that matter (a distribution-factor table), "
            "the raises a moved campus could save, each line's price, the weak points and the plants' room."
        ),
    )
    attempts: list[dict] = []
    feedback = ""
    calls = 0
    cached_calls = 0
    rnd = 0
    offline_at = None
    for rnd in range(ROUNDS):
        prompt = case.prompt(feedback)
        cached = False
        try:
            cached = llm._cache_get(llm._cache_key(prompt, SYSTEM, True, SCHEMA, llm.AGENT_MODEL)) is not None
        except Exception:  # noqa: BLE001 — only labels the trace row
            cached = False
        tg = time.perf_counter()
        raw, offline = await llm.complete_json(
            prompt, system=SYSTEM, fallback={}, timeout=TIMEOUT_S, schema=SCHEMA, surface="unlock", model=llm.AGENT_MODEL, thinking=llm.AGENT_THINKING
        )
        calls += 1
        g_ms = round((time.perf_counter() - tg) * 1000)
        cached = cached and not offline
        cached_calls += int(cached)
        if offline:
            offline_at = rnd + 1
            tr.add(
                round=rnd + 1,
                actor="gemini",
                kind="offline",
                tone="muted",
                title="Gemini did not answer" if rnd == 0 else "Gemini did not answer the revision",
                detail="Unavailable or too slow: the engine's plan stands.",
            )
            break
        p = case.clean(raw)
        revised = rnd > 0
        new, gone = case.diff(p)
        moves = []
        if new:
            moves.append("new: " + _join([case.area(j) for j in new][:4]))
        if gone:
            moves.append("dropped: " + _join([case.area(j) for j in gone][:4]))
        tr.add(
            round=rnd + 1,
            actor="gemini",
            kind="revise" if revised else "propose",
            tone="info",
            claim=p["claim"],
            sites=[case.site_id(j) for j in p["sites"]],
            raises=[{"branch_id": case.bid(i), "to_mva": L} for i, L in p["raises"].items()],
            ms=g_ms,
            cached=cached,
            title=("Revised: " if revised else "Proposed: ") + case.what(p),
            detail=_reason_text(p["reason"])
            + f"{_campuses(len(p['sites'])).capitalize()}" + (f" ({'; '.join(moves)})" if moves else "") + f"; {_raises_text(case, p)}."
            + (" Adjusted by the engine: " + "; ".join(p["notes"][:3]) + "." if p["notes"] else "")
            + (" A saved answer: Gemini was asked this exact case before." if cached else ""),
        )

        def run(p=p):
            with unlock._compute_lock:
                return case.check(p)

        v = await run_in_threadpool(run)
        outcome = case.judge(p, v)
        llm.note_check("unlock", outcome in ("beat", "matched"), "a capacity plan the engine re-solved: " + {"empty": "no site it could place", "shed": "the plants can't supply it", "fails": "a line over its rating", "trips": "the full cascade trips a line", "fewer": "fewer campuses than the bar", "dearer": "it holds but costs more than the engine's plan"}.get(outcome, outcome), f"{v['over'][0]['pct']:g}%" if outcome == "fails" and v.get("over") else None)
        attempts.append({"round": rnd + 1, "p": p, "v": v, "outcome": outcome})
        _verdict_row(tr, case, rnd + 1, p, v, outcome)
        if outcome == "beat":
            break
        if rnd + 1 < ROUNDS:
            feedback = case.feedback(p, v, outcome)
            k = len(v["over"])
            tr.add(
                round=rnd + 2,
                actor="engine",
                kind="feedback",
                tone="info",
                title="Sent the engine's findings back to Gemini",
                detail=(
                    (f"The {_count(k)} {'line' if k == 1 else 'lines'} over {'its' if k == 1 else 'their'} rating and by how much, the raise each needs and its price; " if k else "")
                    + f"what the plan really costs ({_m(v['cost']['high'])}) against the bar. Asked for one revised plan."
                ),
            )
    ms = round((time.perf_counter() - t_start) * 1000)
    return _finish(case, tr, attempts, bar, calls, ms, offline_at, min(rnd + 1, ROUNDS), cached_calls)


def _verdict_row(tr: _Trace, case: _Case, rnd: int, p: dict, v: dict, outcome: str) -> None:
    n = v["campuses"]
    price = f"Priced by the engine: {_m(v['cost']['high'])} (high end; Gemini's own numbers are not used)."
    if v.get("sized"):
        k = len(v["sized"])
        price += " Sized up by the engine's 10 % margin rule: " + "; ".join(f"{x['short']} {x['asked_mva']:,.0f} to {x['sized_mva']:,.0f} MVA" for x in v["sized"][:2]) + (f" (+{k - 2} more)" if k > 2 else "") + "."
    if outcome == "empty":
        tr.add(round=rnd, actor="engine", kind="verify", tone="muted", ms=v.get("ms"), title="Nothing to run", detail="It named no candidate site the engine could place.")
        return
    if outcome in ("fails", "shed"):
        k = len(v["over"])
        if k:
            title = f"Engine solved all {_count(n)} campuses at once: {_count(k)} {'line' if k == 1 else 'lines'} over {'its' if k == 1 else 'their'} rating"
            detail = "Over: " + "; ".join(f"{o['short']} at {o['pct']:.0f} % of {o['rating_mva']:,.0f} MVA" for o in v["over"][:2]) + (f" (+{k - 2} more)" if k > 2 else "") + ". "
        else:
            title = f"Engine solved all {_count(n)} campuses at once: the plants can't supply them"
            detail = f"{v['lost_mw']:,.0f} MW of load would be shed. "
        tr.add(round=rnd, actor="engine", kind="verify", tone="over", holds=False, over=v["over"][:5], ms=v.get("ms"), title=title, detail=detail + price)
        return
    if outcome == "trips":
        tr.add(round=rnd, actor="engine", kind="verify", tone="over", holds=False, ms=v.get("ms"),
               title="Holds in one solve, but the full cascade trips a line", detail=f"First to trip: {v.get('tripped') or 'a line'}. " + price)
        return
    ec = engine_cost(case.m, n)
    compare = f"The engine's plan needs {_m(ec)} for {_count(n)}." if ec is not None else f"The engine's plan never reaches {_count(n)}."
    tone = "holds" if outcome in ("beat", "matched") else "muted"
    title = f"Engine solved all {_count(n)} campuses at once: it holds"
    tr.add(round=rnd, actor="engine", kind="verify", tone=tone, holds=True, ms=v.get("ms"), title=title,
           detail=f"{price} Full cascade with every line in service (N-0): nothing trips. {compare}")


def _sentence(case: _Case, best: dict, status: str, attempts: list[dict], offline_at: int | None) -> str:
    bar_n, bar_hi = case.bar_n, case.bar["high"]
    if status in ("beat", "matched"):
        p, v = best["p"], best["v"]
        n = v["campuses"]
        cost = v["cost"]["high"]
        ec = engine_cost(case.m, n)
        what = case.what(p)
        if status == "matched":
            what = what.replace(" for less", "")
            return f"Gemini tried to beat this plan and matched it: {what} for {_m(cost)}, the same as the engine's {_m(ec)}."
        if n > bar_n:
            if _m(cost) == _m(bar_hi):
                money = f"for the same {_m(bar_hi)}"
            elif cost < bar_hi:
                money = f"for {_m(cost)}, within the engine's {_m(bar_hi)} for {_count(bar_n)}"
            elif ec is not None:
                money = f"for {_m(cost)}; the engine's plan needs {_m(ec)} for {_count(n)}"
            else:
                money = f"for {_m(cost)}; the engine's plan never reaches {_count(n)}"
            return f"Gemini tried to beat this plan: {what} {money}."
        what = what.replace(" for less", "")
        return f"Gemini tried to beat this plan: {what} for {_m(cost)}, {_m(ec - cost)} less than the engine's {_m(ec)}."
    if status == "lost":
        ph = [case.phrase(a["p"], a["v"], a["outcome"]) for a in attempts]
        if len(attempts) == 2 and all(a["outcome"] == "fails" for a in attempts) and attempts[0]["v"]["over"][0]["branch_id"] == attempts[1]["v"]["over"][0]["branch_id"]:
            o = attempts[1]["v"]["over"]
            ph[1] = f"also took it to {o[0]['pct']:.0f} %" + (f" ({_count(len(o) - 1)} more {'line' if len(o) == 2 else 'lines'} over)" if len(o) > 1 else "")
        tail = "; Gemini didn't answer the revision" if offline_at == 2 else ""
        if len(ph) == 1:
            return f"Gemini's proposal didn't beat the engine's plan: it {ph[0]}{tail}."
        return f"Gemini's {_count(len(ph))} proposals didn't beat the engine's plan: one {ph[0]}; the other {ph[1]}{tail}."
    if status == "offline":
        return "Gemini didn't answer in time, so nothing challenged this plan; the engine's plan stands."
    return ""


def _tally(rounds: int, calls: int, cached_calls: int, ms: int) -> str:
    """ "2 rounds, 2 Gemini calls, 11.3 s." — or, when Gemini's answers came from the saved answers (the same case
    asked before), says so instead of a time that would read as Gemini answering in a blink."""
    head = f"{rounds} {'round' if rounds == 1 else 'rounds'}, {calls} Gemini {'call' if calls == 1 else 'calls'}"
    if calls and cached_calls >= calls:
        return head + (", its answer saved from an earlier run of this exact case." if calls == 1 else ", both answers saved from an earlier run of this exact case." if calls == 2 else ", every answer saved from an earlier run of this exact case.")
    if cached_calls:
        return head + f" ({_count(cached_calls)} saved from an earlier run), {ms / 1000:.1f} s."
    return head + f", {ms / 1000:.1f} s."


def _finish(case: _Case, tr: _Trace, attempts: list[dict], bar: dict, calls: int, ms: int, offline_at: int | None, rounds: int, cached_calls: int = 0) -> dict:
    beats = [a for a in attempts if a["outcome"] == "beat"]
    matched = [a for a in attempts if a["outcome"] == "matched"]
    if beats:
        best = max(beats, key=lambda a: (a["v"]["campuses"], -a["v"]["cost"]["high"]))
        status = "beat"
    elif matched:
        best, status = matched[-1], "matched"
    elif attempts:
        best, status = attempts[-1], "lost"
    else:
        best, status = None, "offline"
    sentence = _sentence(case, best, status, attempts, offline_at)
    tally = _tally(rounds, calls, cached_calls, ms)
    if status == "beat":
        v = best["v"]
        tr.add(final=True, round=rounds, actor="engine", kind="result", tone="holds",
               title=f"Gemini beat the engine's plan: {_campuses(v['campuses'])} at once for {_m(v['cost']['high'])}",
               detail=f"Verified by the engine: one solve with every campus and raise, then the full cascade with every line in service (N-0: nothing trips). {tally}")
    elif status == "matched":
        tr.add(final=True, round=rounds, actor="engine", kind="result", tone="info", title="Gemini matched the engine's plan, but didn't beat it",
               detail=f"Verified by the engine; the same cost within {TOL_SHARE * 100:.0f} %. {tally}")
    elif status == "lost":
        tr.add(final=True, round=rounds, actor="engine", kind="result", tone="muted", title="The engine's plan stands: Gemini didn't beat it",
               detail=f"Only a plan the engine re-solves, prices and runs through the full cascade can count as a win. {tally}")
    out = _empty(status, sentence, bar=bar)
    verified = status in ("beat", "matched")
    same_n = None
    if best is not None:
        p, v = best["p"], best["v"]
        out.update(_plan(case, p, v, best["outcome"]))
        out["claim"] = p["claim"]
        out["reason"] = p["reason"]
        n = v["campuses"]
        ec = engine_cost(case.m, n)
        same_n = {"campuses": n, "cost_high": round(ec) if ec is not None else None}
        # the comparisons only for a plan the engine verified: a plan that overloads lines has no price worth comparing
        out["engine_same_money"] = {"campuses": engine_within(case.m, v["cost"]["high"]), "cost_high": v["cost"]["high"]} if verified else None
        out["savings_high"] = round(ec - v["cost"]["high"]) if verified and ec is not None else None
    out["engine_same_campuses"] = same_n
    out["verified"] = verified
    out["cached_calls"] = cached_calls
    out["attempts"] = [
        {
            "round": a["round"],
            "claim": a["p"]["claim"],
            "campuses": a["v"]["campuses"],
            "cost_high": a["v"]["cost"]["high"],
            "holds": a["v"]["holds"],
            "calm": a["v"]["calm"],
            "over": a["v"]["over"][:5],
            "lost_mw": a["v"]["lost_mw"],
            "outcome": a["outcome"],
            "phrase": case.phrase(a["p"], a["v"], a["outcome"]),
        }
        for a in attempts
    ]
    out["rounds"] = rounds
    out["calls"] = calls
    out["trace"] = tr.rows
    out["ms"] = ms
    out["model"] = llm.AGENT_MODEL
    return out
