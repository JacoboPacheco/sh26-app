"""DC power flow, headroom and cascade on the committed grid JSON (backend/demo/florida_grid.json).

Pure math — no FastAPI imports — so backend/demo/validate.py can import it too.

Model (SPEC.md → Data and honesty): a synthetic grid, lossless DC power flow. Every branch
b has reactance x_b (per unit on BASE_MVA) and a rating (MVA). Flow on b is
(theta_f - theta_t) / x_b * BASE_MVA in MW. Loads are met by generators scaled between 0 and
their Pmax; fixed "tie" injections stand in for the lines that crossed the cut.

Memory rule (CLAUDE.md → Gotchas): never build a dense inverse or PTDF. One sparse LU per
network state, one solve per what-if, and headroom sensitivities in column chunks.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field

import numpy as np
import scipy.sparse as sp
from scipy.sparse.csgraph import breadth_first_order, connected_components
from scipy.sparse.linalg import splu

BASE_MVA = 100.0
HOMES_PER_MW = 700  # legacy `homes` field only; the UI shows people (people_zone) and homes_zone
PEOPLE_PER_HOME = 2.5  # Census persons per household (U.S. 2.5, Florida 2.47): homes = people / 2.5
MAX_STEPS = 30
HOT_PCT = 80.0
OVER_PCT = 100.0
_EPS = 1e-9
FIRM_TARGET = 0.995  # a line held in for a firm campus is relieved to this share of its rating
MIN_RELIEF = 0.1  # load is cut only where a MW cut relieves the line by at least this much


def area_of(name: str) -> str:
    """The town a synthetic substation is named after: "NAPLES 12" -> "Naples" (the frontend's
    townOf uses the same rule)."""
    base = re.sub(r"\s+\d+$", "", str(name or "")).strip().lower()
    return re.sub(r"\b\w", lambda m: m.group(0).upper(), base)


@dataclass
class State:
    """One solved network state."""

    active: np.ndarray  # bool per branch
    flow: np.ndarray  # MW per branch (from -> to positive); 0 on inactive branches
    loading_pct: np.ndarray  # |flow| / rate * 100
    served_load: np.ndarray  # MW per bus actually served
    gen: np.ndarray  # MW per bus dispatched
    lost_mw: float  # load shed or dark, total
    dark_bus: np.ndarray  # bool per bus: in an island with no supply at all
    comp: np.ndarray  # component id per bus
    lu: object = field(repr=False, default=None)  # splu factor of the reduced B'
    keep: np.ndarray | None = field(repr=False, default=None)  # bus indices kept in the reduced system
    weights: np.ndarray | None = field(repr=False, default=None)  # slack distribution per bus
    lost_existing_mw: float = 0.0  # of lost_mw, the part that was existing load (homes)
    lost_extra_mw: float = 0.0  # of lost_mw, the part that was the added load (the data center)
    lost_bus: np.ndarray | None = field(repr=False, default=None)  # existing load lost per bus (MW)
    rate: np.ndarray | None = field(repr=False, default=None)  # ratings this state was judged against


class Grid:
    def __init__(self, data: dict, load_factor: float = 1.0):
        """`load_factor` scales every existing load (the heat-wave clock): 1.0 is the dataset's own
        snapshot. Ties stay fixed; generation re-dispatches to balance."""
        self._data = data
        self.load_factor = float(load_factor)
        self.meta = dict(data.get("meta", {}))
        # people without power (an estimate): lost MW x people_per_mw, capped at the population.
        # grid.py sets both from backend/demo/population.json; 0 / None means "not known".
        self.people_per_mw = 0.0
        self.population: int | None = None
        buses = data["buses"]
        self.n = len(buses)
        self.bus_ids = np.array([int(b["id"]) for b in buses])
        self.bus_index = {int(bid): i for i, bid in enumerate(self.bus_ids)}
        self.bus_sub = np.array([int(b["sub"]) for b in buses])
        self.bus_kv = np.array([float(b["kv"]) for b in buses])
        self.pd = np.array([float(b["pd"]) for b in buses]) * self.load_factor
        self.pg = np.array([float(b.get("pg", 0.0)) for b in buses])
        self.pmax = np.array([float(b.get("pmax", 0.0)) for b in buses])
        self.pmax = np.maximum(self.pmax, self.pg)
        self.tie = np.zeros(self.n)
        for t in data.get("ties", []):
            self.tie[self.bus_index[int(t["bus"])]] += float(t["mw"])

        subs = data["subs"]
        self.sub_ids = np.array([int(s["id"]) for s in subs])
        self.sub_index = {int(sid): i for i, sid in enumerate(self.sub_ids)}
        self.sub_name = [str(s.get("name", s["id"])) for s in subs]
        self.sub_lat = np.array([float(s["lat"]) for s in subs])
        self.sub_lon = np.array([float(s["lon"]) for s in subs])
        self.bus_sub_idx = np.array([self.sub_index[s] for s in self.bus_sub])

        branches = data["branches"]
        self.m = len(branches)
        self.br_ids = np.array([int(b["id"]) for b in branches])
        self.br_index = {int(bid): i for i, bid in enumerate(self.br_ids)}
        self.f = np.array([self.bus_index[int(b["f"])] for b in branches])
        self.t = np.array([self.bus_index[int(b["t"])] for b in branches])
        self.x = np.array([float(b["x"]) for b in branches])
        self.x = np.where(np.abs(self.x) < 1e-6, 1e-6, self.x)
        self.rate = np.array([float(b["rate"]) for b in branches])
        self.br_kv = np.array([float(b.get("kv", 0.0)) for b in branches])
        self.pf_ref = np.array([float(b.get("pf_ref", 0.0)) for b in branches])

        self.base = self.solve(np.ones(self.m, dtype=bool))
        self._headroom_bus: np.ndarray | None = None
        self._marginal: tuple[np.ndarray, np.ndarray, np.ndarray] | None = None
        self._adj: list[list[tuple[int, int]]] | None = None  # substation graph (waves)
        # full existing load per substation at this load level (people in the blackout zone)
        self.sub_load = np.zeros(len(self.sub_ids))
        np.add.at(self.sub_load, self.bus_sub_idx, self.pd)

    # ------------------------------------------------------------------ loading
    @classmethod
    def from_file(cls, path: str) -> "Grid":
        with open(path, encoding="utf-8") as fh:
            return cls(json.load(fh))

    def variant(self, load_factor: float) -> "Grid":
        """The same network at another load level (shares the parsed JSON, not the arrays). People
        per MW stays the base level's (a MW lost stands for the same people at every level)."""
        g = Grid(self._data, load_factor)
        g.people_per_mw, g.population = self.people_per_mw, self.population
        return g

    def people(self, mw: float) -> int:
        """People without power for `mw` of existing load lost (an estimate)."""
        n = int(round(max(float(mw), 0.0) * self.people_per_mw))
        return min(n, self.population) if self.population else n

    def extra_load(self, sites: list[tuple[int, float]]) -> np.ndarray:
        """Per-bus added load from [(bus index, MW), ...] — several data centers at once."""
        extra = np.zeros(self.n)
        for bus, mw in sites:
            extra[bus] += mw
        return extra

    def rates_with(self, upgrades: dict[int, float] | None) -> np.ndarray:
        """Ratings after upgrades {branch id: new rating MVA}; never lowers a rating."""
        rate = self.rate.copy()
        for bid, new in (upgrades or {}).items():
            i = self.br_index[int(bid)]
            rate[i] = max(rate[i], float(new))
        return rate

    # ------------------------------------------------------------------ geometry
    def nearest_sub(self, lat: float, lon: float) -> int:
        """Index of the nearest substation (haversine)."""
        lat1, lon1 = math.radians(lat), math.radians(lon)
        lat2, lon2 = np.radians(self.sub_lat), np.radians(self.sub_lon)
        a = np.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
        return int(np.argmin(a))

    def connect_bus(self, sub_idx: int) -> int:
        """The bus a new load connects to at a substation: its highest-voltage bus at or above
        100 kV, else its highest-voltage bus."""
        cand = np.flatnonzero(self.bus_sub_idx == sub_idx)
        hv = cand[self.bus_kv[cand] >= 100.0]
        pool = hv if len(hv) else cand
        return int(pool[np.argmax(self.bus_kv[pool])])

    def site_bus(self, lat: float, lon: float) -> int:
        return self.connect_bus(self.nearest_sub(lat, lon))

    # ------------------------------------------------------------------ solving
    def _balance(self, active: np.ndarray, extra_load: np.ndarray, shed: np.ndarray | None = None):
        """Dispatch generation island by island so every component balances. `shed` is existing
        load (MW per bus) the operator has already cut to keep a firm campus connected.

        Returns (P per bus in per-unit, served_load, gen, tie_eff, lost_mw, dark_bus, comp, ref_buses, weights)."""
        adj = sp.coo_matrix(
            (np.ones(int(active.sum())), (self.f[active], self.t[active])), shape=(self.n, self.n)
        )
        ncomp, comp = connected_components(adj, directed=False)
        pd = self.pd if shed is None else self.pd - shed
        load = pd + extra_load
        served = load.copy()
        gen = np.zeros(self.n)
        tie = self.tie.copy()
        dark = np.zeros(self.n, dtype=bool)
        weights = np.zeros(self.n)
        refs = []
        lost = 0.0
        for c in range(ncomp):
            idx = np.flatnonzero(comp == c)
            L = float(load[idx].sum())
            T = float(tie[idx].sum())
            gmax = self.pmax[idx]
            Gmax = float(gmax.sum())
            need = L - T
            if need > Gmax and T < 0:
                # short of supply: the island stops exporting before it cuts its own customers
                tie[idx] = np.maximum(tie[idx], 0.0)
                T = float(tie[idx].sum())
                need = L - T
            if Gmax <= _EPS:
                # no generator here: ties are the only supply
                if need <= 0:
                    if T > _EPS:
                        tie[idx] *= L / T  # export can't exceed what the island has
                elif T > _EPS:
                    served[idx] *= T / L  # serve what the import covers, shed the rest
                    lost += need
                else:
                    served[idx] = 0.0  # nothing supplies this island
                    tie[idx] = 0.0  # an export-only tie can't be honored either
                    dark[idx] = load[idx] > _EPS
                    lost += L
            elif need < 0:
                if T > _EPS:
                    tie[idx] *= L / T
            elif need > Gmax:
                gen[idx] = gmax
                if Gmax + T >= 0:
                    served[idx] *= (Gmax + T) / L
                    lost += need - Gmax
                else:
                    # an export tie bigger than the island's generation (storms can cut an island
                    # like this): serve nothing and export only what the generators make
                    served[idx] = 0.0
                    tie[idx] *= Gmax / -T
                    dark[idx] = load[idx] > _EPS
                    lost += L
            else:
                pg = self.pg[idx]
                base = float(pg.sum())
                if need >= base:
                    head = gmax - pg
                    w = head / head.sum() if head.sum() > _EPS else np.full(len(idx), 1.0 / len(idx))
                    gen[idx] = pg + (need - base) * w
                else:
                    gen[idx] = pg * (need / base) if base > _EPS else 0.0
            # reference bus and slack weights for the sensitivity solves
            refs.append(int(idx[np.argmax(gmax)]))
            head = gmax - gen[idx]
            if head.sum() > _EPS:
                weights[idx] = head / head.sum()
            elif Gmax > _EPS:
                weights[idx] = gmax / Gmax
            else:
                weights[idx] = 1.0 / len(idx)
        P = (gen + tie - served) / BASE_MVA
        # Split the shed load into existing load (homes) and the added load (the data center itself).
        with np.errstate(divide="ignore", invalid="ignore"):
            frac = np.where(load > _EPS, served / load, 1.0)
        lost_bus = np.maximum(self.pd - pd * frac, 0.0)  # shed load counts as lost; rounding never makes it negative
        lost_existing = float(lost_bus.sum())
        if shed is not None:
            lost += float(shed.sum())
        lost_extra = float((extra_load * (1.0 - frac)).sum())
        return P, served, gen, tie, lost, dark, comp, np.array(refs, dtype=int), weights, lost_existing, lost_extra, lost_bus

    def solve(
        self, active: np.ndarray, extra_load: np.ndarray | None = None, rate: np.ndarray | None = None, shed: np.ndarray | None = None
    ) -> State:
        if extra_load is None:
            extra_load = np.zeros(self.n)
        if rate is None:
            rate = self.rate
        P, served, gen, tie, lost, dark, comp, refs, weights, lost_existing, lost_extra, lost_bus = self._balance(
            active, extra_load, shed
        )
        b = 1.0 / self.x[active]
        fa, ta = self.f[active], self.t[active]
        rows = np.concatenate([fa, ta, fa, ta])
        cols = np.concatenate([fa, ta, ta, fa])
        vals = np.concatenate([b, b, -b, -b])
        B = sp.coo_matrix((vals, (rows, cols)), shape=(self.n, self.n)).tocsc()
        keep = np.setdiff1d(np.arange(self.n), refs)
        Bred = B[keep][:, keep].tocsc()
        theta = np.zeros(self.n)
        lu = None
        if len(keep):
            lu = splu(Bred)
            theta[keep] = lu.solve(P[keep])
        flow = np.zeros(self.m)
        flow[active] = (theta[fa] - theta[ta]) / self.x[active] * BASE_MVA
        loading = np.abs(flow) / rate * 100.0
        return State(
            active=active.copy(),
            flow=flow,
            loading_pct=loading,
            served_load=served,
            gen=gen,
            lost_mw=float(lost),
            dark_bus=dark,
            comp=comp,
            lu=lu,
            keep=keep,
            weights=weights,
            lost_existing_mw=lost_existing,
            lost_extra_mw=lost_extra,
            lost_bus=lost_bus,
            rate=rate,
        )

    # ------------------------------------------------------------------ what-if
    def whatif(self, bus: int, mw: float) -> State:
        extra = np.zeros(self.n)
        extra[bus] = mw
        return self.solve(np.ones(self.m, dtype=bool), extra)

    def overloaded(self, state: State) -> list[dict]:
        idx = np.flatnonzero(state.active & (state.loading_pct > OVER_PCT + 1e-6))
        idx = idx[np.argsort(-state.loading_pct[idx])]
        return [self.branch_info(i, state) for i in idx]

    def branch_info(self, i: int, state: State) -> dict:
        return {
            "id": int(self.br_ids[i]),
            "pct": round(float(state.loading_pct[i]), 1),
            "from": int(self.sub_ids[self.bus_sub_idx[self.f[i]]]),
            "to": int(self.sub_ids[self.bus_sub_idx[self.t[i]]]),
            "kv": float(self.br_kv[i]),
            "rate": float((state.rate if state.rate is not None else self.rate)[i]),
        }

    # ------------------------------------------------------------------ headroom
    def _marginal_weights(self) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """How _balance dispatches the next MW of load, per bus (each island's value): (weights now,
        MW until that changes, weights after). While an island needs less than its generators' sum(pg),
        _balance scales pg down proportionally, so the next MW comes pro rata to pg; once need reaches
        sum(pg) it fills the headroom (pmax - pg). The base state's `weights` describe only the second
        regime; Florida's base case is in the first (need 50,228 MW < sum pg 51,279 MW)."""
        if self._marginal is None:
            st = self.base
            w1, w2, room = st.weights.copy(), st.weights.copy(), np.full(self.n, np.inf)
            for c in np.unique(st.comp):
                idx = np.flatnonzero(st.comp == c)
                need = float(self.pd[idx].sum() - self.tie[idx].sum())
                pg, gmax = self.pg[idx], self.pmax[idx]
                base = float(pg.sum())
                if gmax.sum() <= _EPS or base <= _EPS or not (0 <= need < base):
                    continue
                w1[idx] = pg / base
                room[idx] = base - need
                head = gmax - pg
                w2[idx] = head / head.sum() if head.sum() > _EPS else 1.0 / len(idx)
            self._marginal = (w1, w2, room)
        return self._marginal

    def _sensitivity(self, buses: np.ndarray, weights: np.ndarray | None = None) -> np.ndarray:
        """Change in branch flow (MW) per MW of load added at each bus in `buses`, picked up by that
        bus's island with `weights` (default: the base-case slack weights). Shape (m, len(buses))."""
        st = self.base
        w = st.weights if weights is None else weights
        d = np.zeros((self.n, len(buses)))
        for j, k in enumerate(buses):
            comp_mask = st.comp == st.comp[k]
            d[comp_mask, j] = w[comp_mask]  # generators pick the MW up ...
            d[k, j] -= 1.0  # ... the load takes it
        theta = np.zeros((self.n, len(buses)))
        if st.lu is not None and len(st.keep):
            theta[st.keep] = st.lu.solve(d[st.keep] / BASE_MVA)
        return (theta[self.f] - theta[self.t]) / self.x[:, None] * BASE_MVA

    def _first_limit(self, f: np.ndarray, dF: np.ndarray) -> np.ndarray:
        """MW (per column) until the first branch reaches its rating, from flows `f` moving by `dF` per MW.
        Branches already over in the base case don't count (not a *new* overload)."""
        rate = self.rate[:, None]
        with np.errstate(divide="ignore", invalid="ignore"):
            t_pos = (rate - f) / dF  # flow rising toward +rate
            t_neg = (-rate - f) / dF  # flow falling toward -rate
        t = np.where(dF > _EPS, t_pos, np.where(dF < -_EPS, t_neg, np.inf))
        t = np.where(np.abs(self.base.flow[:, None]) >= rate, np.inf, t)
        t = np.where(t < 0, 0.0, t)  # only reachable in the second segment, from rounding at its start
        t = np.where(self.base.active[:, None], t, np.inf)
        return np.min(t, axis=0)

    def headroom_for_buses(self, buses: np.ndarray) -> np.ndarray:
        """MW that can be added at each bus before the first branch reaches its rating — piecewise, with
        the same dispatch the what-if uses (see _marginal_weights), so it matches a bisection on whatif."""
        buses = np.asarray(buses)
        w1, w2, room = self._marginal_weights()
        dF1 = self._sensitivity(buses, w1)
        h = self._first_limit(self.base.flow[:, None], dF1)
        r = room[buses]
        more = np.flatnonzero((h > r) & np.isfinite(r))  # still room when the dispatch regime switches
        if len(more):
            f1 = self.base.flow[:, None] + r[more][None, :] * dF1[:, more]
            h[more] = r[more] + self._first_limit(f1, self._sensitivity(buses[more], w2))
        return h

    def headroom_all(self, chunk: int = 256) -> np.ndarray:
        if self._headroom_bus is None:
            out = np.empty(self.n)
            for s in range(0, self.n, chunk):
                idx = np.arange(s, min(s + chunk, self.n))
                out[idx] = self.headroom_for_buses(idx)
            out = np.where(np.isfinite(out), out, 1e6)
            self._headroom_bus = out
        return self._headroom_bus

    def headroom_by_sub(self) -> dict[int, float]:
        """MW each substation can take at the bus a dropped load connects to (connect_bus), so the
        heatmap and a what-if at the same substation always agree."""
        hb = self.headroom_all()
        return {int(sid): float(min(hb[self.connect_bus(i)], 1e6)) for i, sid in enumerate(self.sub_ids)}

    def headroom_bus(self, bus: int) -> float:
        return float(min(self.headroom_for_buses(np.array([bus]))[0], 1e6))

    # ------------------------------------------------------------------ cascade
    def _hottest(self, state: State, limit: int = 60) -> list[dict]:
        """Branches above HOT_PCT, hottest first, capped so a 30-step cascade stays a small payload."""
        idx = np.flatnonzero(state.active & (state.loading_pct > HOT_PCT))
        idx = idx[np.argsort(-state.loading_pct[idx])][:limit]
        return [{"id": int(self.br_ids[i]), "pct": round(float(state.loading_pct[i]), 1)} for i in idx]

    def _carried(self, state: State, idx: list[int]) -> list[dict]:
        """Power each branch carried in `state` (MW) and the people that much power serves (an estimate)."""
        out = []
        for k in idx:
            mw = abs(float(state.flow[k]))
            out.append({"id": int(self.br_ids[k]), "mw": round(mw, 1), "people": self.people(mw)})
        return out

    def lost_by_sub(self, state: State, min_mw: float = 0.1) -> dict[int, float]:
        """Existing load lost per substation (MW), only where it's at least `min_mw`."""
        per = np.zeros(len(self.sub_ids))
        np.add.at(per, self.bus_sub_idx, state.lost_bus if state.lost_bus is not None else 0.0)
        return {int(self.sub_ids[i]): round(float(per[i]), 1) for i in np.flatnonzero(per >= min_mw)}

    def zone(self, sub_ids) -> dict:
        """Everyone served by these substations (the ones that lost any power): the high-end estimate.
        In a shortage the operator rotates outages across the whole area, so each of them loses power
        at some point — not only the share of MW that was cut. Capped at the population."""
        idx = [self.sub_index[s] for s in set(sub_ids) if s in self.sub_index]
        mw = float(self.sub_load[idx].sum()) if idx else 0.0
        people = self.people(mw)
        return {"zone_mw": round(mw, 1), "people_zone": people, "homes_zone": int(round(people / PEOPLE_PER_HOME))}

    # -- how a step's darkness spreads (the map animates this, wave by wave)
    MAX_WAVES = 24

    def _sub_graph(self) -> list[list[tuple[int, int]]]:
        """Substation adjacency over branches between different substations: [sub idx] -> [(neighbor idx, branch idx)]."""
        if self._adj is None:
            fs, ts = self.bus_sub_idx[self.f], self.bus_sub_idx[self.t]
            adj: list[list[tuple[int, int]]] = [[] for _ in range(len(self.sub_ids))]
            for k in range(self.m):
                a, b = int(fs[k]), int(ts[k])
                if a != b:
                    adj[a].append((b, k))
                    adj[b].append((a, k))
            self._adj = adj
        return self._adj

    def _nearest(self, cands, targets) -> tuple[int, int]:
        """(candidate, target) pair of sub indices closest on the map (degrees, cos-corrected)."""
        c, t = np.array(sorted(cands)), np.array(sorted(targets))
        cos = math.cos(math.radians(float(self.sub_lat[c].mean())))
        d = (self.sub_lat[c][:, None] - self.sub_lat[t][None, :]) ** 2 + ((self.sub_lon[c][:, None] - self.sub_lon[t][None, :]) * cos) ** 2
        i, j = np.unravel_index(int(np.argmin(d)), d.shape)
        return int(c[i]), int(t[j])

    def waves(self, pocket_ids, origin_bids, active: np.ndarray, before: set[int]) -> list[dict]:
        """The substations that lost power this step (`pocket_ids`), in the order the darkness reaches
        them: it starts at the failed line(s) (`origin_bids`) and runs outward along the lines still in
        service (`active`), passing through switching stations (no load), one level at a time. A part
        of the pocket that no line reaches is joined from the nearest dark point (a jump). Each wave:
        {subs, paths (sub ids from where the darkness comes to each sub), people (added), people_zone
        (everyone in the areas hit so far, as in the step's people_zone)}."""
        P = {self.sub_index[s] for s in pocket_ids if s in self.sub_index}
        if not P:
            return []
        adj = self._sub_graph()
        switching = self.sub_load < 0.5
        ends: list[int] = []
        for bid in origin_bids:
            k = self.br_index.get(int(bid))
            if k is not None:
                ends += [int(self.bus_sub_idx[self.f[k]]), int(self.bus_sub_idx[self.t[k]])]
        ends = list(dict.fromkeys(ends))
        seen: set[int] = set()

        def expand(frm: list[int]) -> dict[int, list[int]]:
            """Pocket subs one level out from `frm`, each with the path taken (through switching stations)."""
            found: dict[int, list[int]] = {}
            for x in frm:
                stack, via = [(x, [x])], {x}
                while stack:
                    y, path = stack.pop()
                    for z, k in adj[y]:
                        if not active[k] or z in via or z in seen or z in found:
                            continue
                        if z in P:
                            found[z] = path + [z]
                        elif switching[z]:
                            via.add(z)
                            stack.append((z, path + [z]))
            return found

        level = {e: [e] for e in ends if e in P}
        level.update({z: p for z, p in expand(ends).items() if z not in level})
        if not level:  # the pocket isn't wired to the failed line: jump from its nearest end
            a, b = self._nearest(P, ends or list(P))
            level = {a: [b, a] if b != a else [a]}
        out: list[dict] = []
        hit = {self.sub_index[s] for s in before if s in self.sub_index}
        prev_people = self.zone(before)["people_zone"]
        while level:
            seen.update(level)
            hit.update(level)
            z = self.zone(int(self.sub_ids[i]) for i in hit)
            out.append({
                "subs": [int(self.sub_ids[i]) for i in sorted(level)],
                "paths": [[int(self.sub_ids[i]) for i in level[j]] for j in sorted(level)],
                "people": z["people_zone"] - prev_people,
                "people_zone": z["people_zone"],
            })
            prev_people = z["people_zone"]
            nxt = expand(sorted(level))
            if not nxt and len(seen) < len(P):
                a, b = self._nearest(P - seen, seen)
                nxt = {a: [b, a]}
            level = nxt
        if len(out) > self.MAX_WAVES:  # a long tail joins the last wave
            head, tail = out[: self.MAX_WAVES - 1], out[self.MAX_WAVES - 1:]
            head.append({
                "subs": [s for w in tail for s in w["subs"]],
                "paths": [p for w in tail for p in w["paths"]],
                "people": sum(w["people"] for w in tail),
                "people_zone": tail[-1]["people_zone"],
            })
            out = head
        return out

    # -- people hit: the bomb counter
    MAX_HIT_GROUPS = 12

    def downstream_subs(self, state: State, branches: list[int], min_mw: float = 1.0) -> set[int]:
        """Substations (ids, with load) that the power on `branches` (indices) was flowing on to in
        `state`: follow the flow direction downhill from each branch's receiving end (|flow| >= min_mw)."""
        fl = state.flow
        use = np.flatnonzero(state.active & (np.abs(fl) >= min_mw))
        fwd = fl[use] > 0
        src = np.where(fwd, self.f[use], self.t[use])
        dst = np.where(fwd, self.t[use], self.f[use])
        adj = sp.csr_matrix((np.ones(len(use)), (src, dst)), shape=(self.n, self.n))
        reached: set[int] = set()
        for k in branches:
            if abs(fl[k]) < _EPS:
                continue
            start = int(self.t[k] if fl[k] > 0 else self.f[k])
            reached.update(int(b) for b in breadth_first_order(adj, start, directed=True, return_predecessors=False))
        if not reached:
            return set()
        subs = np.unique(self.bus_sub_idx[np.fromiter(reached, dtype=int)])
        return {int(self.sub_ids[i]) for i in subs if self.sub_load[i] > 0.5}

    def hits(self, new_subs: set[int], origin: list[int], hit: set[int]) -> list[dict]:
        """`new_subs` joined to `hit` (mutated) town by town, nearest to the failure (`origin`: branch
        indices) first: [{area, subs, km, people (added), people_hit (so far)}]."""
        new_subs = {s for s in new_subs if s in self.sub_index and s not in hit}
        if not new_subs:
            return []
        ends = [int(self.bus_sub_idx[x]) for k in origin for x in (self.f[k], self.t[k])]
        lat0 = float(self.sub_lat[ends].mean()) if ends else float(np.mean([self.sub_lat[self.sub_index[s]] for s in new_subs]))
        lon0 = float(self.sub_lon[ends].mean()) if ends else float(np.mean([self.sub_lon[self.sub_index[s]] for s in new_subs]))
        cos = math.cos(math.radians(lat0))
        by_area: dict[str, list[tuple[float, int]]] = {}
        for s in new_subs:
            i = self.sub_index[s]
            d = 111.19 * math.hypot(float(self.sub_lat[i]) - lat0, (float(self.sub_lon[i]) - lon0) * cos)
            by_area.setdefault(area_of(self.sub_name[i]), []).append((d, s))
        groups = sorted(by_area.items(), key=lambda kv: min(d for d, _ in kv[1]))
        if len(groups) > self.MAX_HIT_GROUPS:  # the far tail lands together
            head, tail = groups[: self.MAX_HIT_GROUPS - 1], groups[self.MAX_HIT_GROUPS - 1:]
            groups = head + [(f"{len(tail)} more towns", [x for _, lst in tail for x in lst])]
        out, prev = [], self.zone(hit)["people_zone"]
        for area, lst in groups:
            hit.update(s for _, s in lst)
            z = self.zone(hit)["people_zone"]
            out.append({"area": area, "subs": sorted(s for _, s in lst), "km": round(min(d for d, _ in lst), 1), "people": z - prev, "people_hit": z})
            prev = z
        return out

    def cascade(self, bus: int | None, mw: float, trip: list[int] | None = None, firm: bool = False) -> dict:
        """One data center (or none); see cascade_case."""
        extra = self.extra_load([(bus, mw)] if bus is not None else [])
        return self.cascade_case(extra, trip, firm_buses=[bus] if (firm and bus is not None) else None)

    # -- firm campuses: which outages would cut a campus off from the supply it needs
    def _components(self, active: np.ndarray) -> np.ndarray:
        adj = sp.coo_matrix((np.ones(int(active.sum())), (self.f[active], self.t[active])), shape=(self.n, self.n))
        return connected_components(adj, directed=False)[1]

    def _short(self, comp: np.ndarray, load: np.ndarray, buses: np.ndarray) -> np.ndarray:
        """Per bus in `buses`: is its island short of supply (load beyond its generators' Pmax plus
        tie imports), so that load there would be shed?"""
        k = int(comp.max()) + 1
        need = np.bincount(comp, weights=load - self.tie, minlength=k)
        gmax = np.bincount(comp, weights=self.pmax, minlength=k)
        return (need > gmax + 0.5)[comp[buses]]

    def _strands(self, active: np.ndarray, branch: int, firm: np.ndarray, load: np.ndarray) -> bool:
        """Would tripping `branch` leave a firm campus (that has supply now) in an island short of it?"""
        comp_now = self._components(active)
        live = firm[~self._short(comp_now, load, firm)]
        if not len(live):
            return False
        trial = active.copy()
        trial[branch] = False
        return bool(self._short(self._components(trial), load, live).any())

    def _line_sens(self, state: State, branch: int) -> np.ndarray:
        """MW change on `branch` per MW of load added at each bus (picked up by that island's
        generators with the state's slack weights). One sparse solve on the state's factor."""
        e = np.zeros(self.n)
        e[self.f[branch]] += 1.0
        e[self.t[branch]] -= 1.0
        y = np.zeros(self.n)
        if state.lu is not None and len(state.keep):
            y[state.keep] = state.lu.solve(e[state.keep])  # B' is symmetric
        y /= self.x[branch]  # MW on the branch per MW injected at each bus (reference: 0)
        comp = state.comp
        ybar = np.bincount(comp, weights=state.weights * y, minlength=int(comp.max()) + 1)[comp]
        return -(y - ybar)

    def _firm_line(self, state: State, branch: int, extra: np.ndarray, shed: np.ndarray, firm: np.ndarray, rate: np.ndarray) -> bool:
        """Does protecting the firm campuses mean holding `branch` in (shedding instead of tripping)?"""
        flow = float(state.flow[branch])
        excess = abs(flow) - float(rate[branch])
        sens = self._line_sens(state, branch)
        push = float(np.sign(flow) * (sens[firm] * extra[firm]).sum())  # MW the campuses add to it
        if push >= excess:
            return True
        return self._strands(state.active, branch, firm, self.pd - shed + extra)

    def _relief(self, state: State, branch: int, shed: np.ndarray, firm: np.ndarray, rate: np.ndarray) -> np.ndarray | None:
        """Existing load to cut (MW per bus) that brings `branch` back to FIRM_TARGET of its rating,
        taken where a MW cut helps it most; None when no load can do it."""
        flow = float(state.flow[branch])
        excess = abs(flow) - FIRM_TARGET * float(rate[branch])
        eff = np.sign(flow) * self._line_sens(state, branch)  # MW of relief per MW cut at each bus
        avail = np.where(state.comp == state.comp[self.f[branch]], self.pd - shed, 0.0)
        cand = np.flatnonzero((eff > MIN_RELIEF) & (avail > _EPS))
        cand = cand[np.argsort(-eff[cand])]
        cut = np.zeros(self.n)
        got = 0.0
        for k in cand:
            take = min(float(avail[k]), (excess - got) / float(eff[k]))
            cut[k] = take
            got += take * float(eff[k])
            if got >= excess - 1e-6:
                return cut
        return None

    def cascade_case(
        self,
        extra: np.ndarray,
        trip: list[int] | None = None,
        upgrades: dict[int, float] | None = None,
        firm_buses: list[int] | None = None,
    ) -> dict:
        """Trip the most overloaded branch, re-solve, repeat (SPEC.md M2). `trip` knocks branches out
        before step 1 (a hurricane) and is reported as step 0; `upgrades` raises ratings (Fix it).

        Each step lists the substations that newly lose load (`newly_affected`: [[sub id, MW lost]]),
        so the UI can name towns as they go dark.

        `firm_buses` (bus indices) are campuses on firm service: the grid operator keeps them on and
        protects them by cutting other customers instead. When the hottest overloaded line is one the
        campuses push over its rating (it would be within rating without them) or one whose loss
        would strand a campus short of supply, the operator sheds existing load where it relieves
        that line most (a "shed" step, `held_line`) instead of letting it trip. If no load nearby
        can relieve it enough, it trips after all. So a firm campus's size sets how many people are
        cut. Flexible (no firm buses) is the plain cascade: the campus's own connection may trip,
        cutting the campus off along with its neighbors."""
        rate = self.rates_with(upgrades)
        active = np.ones(self.m, dtype=bool)
        for bid in trip or []:
            active[self.br_index[int(bid)]] = False
        firm = np.unique(np.asarray(firm_buses or [], dtype=int))
        shed = np.zeros(self.n)
        steps = []
        carried: list[dict] = []
        hit: set[int] = set()  # substations whose people were hit (each person once)
        origin: list[int] = []  # the branches that failed (or were held) this step
        down: set[int] = set()  # where their power was flowing
        if trip:
            pre = self.solve(np.ones(self.m, dtype=bool), extra, rate)
            carried = self._carried(pre, [self.br_index[int(b)] for b in trip if int(b) in self.br_index])
            origin = [self.br_index[int(b)] for b in trip if int(b) in self.br_index]
            down = self.downstream_subs(pre, origin)
        state = self.solve(active, extra, rate)
        prev_dark = np.zeros(self.n, dtype=bool)
        seen_affected: set[int] = set()
        capped = False
        n = 0
        tripped_ids = [int(b) for b in (trip or [])]
        action, held_line = "storm", None
        while True:
            over = np.flatnonzero(state.active & (state.loading_pct > OVER_PCT + 1e-6))
            newly_dark = state.dark_bus & ~prev_dark
            if n > 0 or len(trip or []):
                lost = self.lost_by_sub(state)
                fresh = [[sid, mw_] for sid, mw_ in lost.items() if sid not in seen_affected]
                before = set(seen_affected)
                seen_affected.update(lost)
                steps.append(
                    {
                        "n": n,
                        "action": action,  # "storm" (step 0: lines knocked out first), "trip" or "shed"
                        "tripped": tripped_ids,
                        "held_line": held_line,  # a shed step: the line held in by cutting load
                        "dark_subs": sorted({int(self.sub_ids[self.bus_sub_idx[i]]) for i in np.flatnonzero(newly_dark)}),
                        "newly_affected": sorted(fresh, key=lambda x: -x[1]),
                        "hot": self._hottest(state),
                        "lost_mw": round(state.lost_existing_mw, 1),
                        "homes": int(round(state.lost_existing_mw * HOMES_PER_MW)),
                        "people": self.people(state.lost_existing_mw),
                        **self.zone(seen_affected),  # people_zone: everyone in the areas hit so far (never goes down)
                        "shed_mw": round(float(shed.sum()), 1),
                        "site_dark_mw": round(state.lost_extra_mw, 1),
                        "carried": carried,  # the power the failed (or held) line carried just before, and the people it serves
                        "hits": self.hits(down, origin, hit),  # the failed line's power went on to these towns: everyone there is hit
                        "waves": self.waves([f[0] for f in fresh], tripped_ids or ([held_line] if held_line is not None else []), state.active, before),
                    }
                )
                for w in steps[-1]["waves"]:  # the blackout reaches anyone not hit yet
                    hit.update(w["subs"])
                    w["people_hit"] = self.zone(hit)["people_zone"]
                hit.update(seen_affected)
                steps[-1]["people_hit"] = self.zone(hit)["people_zone"]
            prev_dark = prev_dark | state.dark_bus
            if not len(over):
                break
            if n >= MAX_STEPS:
                capped = True
                break
            worst = int(over[np.argmax(state.loading_pct[over])])
            carried = self._carried(state, [worst])
            origin = [worst]
            down = self.downstream_subs(state, [worst])
            active = state.active.copy()
            held_line = None
            action = "trip"
            if len(firm) and self._firm_line(state, worst, extra, shed, firm, rate):
                cut = self._relief(state, worst, shed, firm, rate)
                if cut is not None:
                    shed += cut
                    action, held_line = "shed", int(self.br_ids[worst])
            if action == "trip":
                active[worst] = False
                tripped_ids = [int(self.br_ids[worst])]
            else:
                tripped_ids = []
            n += 1
            state = self.solve(active, extra, rate, shed if shed.any() else None)
        return {
            "steps": steps,
            "final_loading_pct": np.round(state.loading_pct, 1).tolist(),
            "final_flow_mw": np.round(state.flow).astype(int).tolist(),
            "final_active": state.active.tolist(),
            "outcome": "islanded" if state.lost_mw > 0.5 else "settled",
            "capped": capped,
            "total_steps": n,
            "lost_mw": round(state.lost_existing_mw, 1),
            "homes": int(round(state.lost_existing_mw * HOMES_PER_MW)),
            "people": self.people(state.lost_existing_mw),
            **self.zone(seen_affected),
            "people_hit": self.zone(hit | seen_affected)["people_zone"],  # the bomb counter: everyone hit, each once
            "people_per_home": PEOPLE_PER_HOME,
            "people_per_mw": round(self.people_per_mw, 2),
            "population": self.population,
            "site_dark_mw": round(state.lost_extra_mw, 1),
            "site_cut_off": bool(state.lost_extra_mw > 0.5),  # the campus itself lost its supply
            "firm": bool(len(firm)),
            "firm_held": bool(state.lost_extra_mw <= 0.5) if len(firm) else None,  # firm: the campus stayed on
            "shed_mw": round(float(shed.sum()), 1),
            "affected": self.lost_by_sub(state),
        }

    # ------------------------------------------------------------------ payloads
    def drawable(self) -> dict:
        """The /api/grid payload: substations and branches at substation level."""
        load_by_sub = np.zeros(len(self.sub_ids))
        np.add.at(load_by_sub, self.bus_sub_idx, self.pd)
        kv_by_sub = np.zeros(len(self.sub_ids))
        np.maximum.at(kv_by_sub, self.bus_sub_idx, self.bus_kv)
        subs = [
            {
                "id": int(self.sub_ids[i]),
                "name": self.sub_name[i],
                "area": area_of(self.sub_name[i]),  # the town it's named after, e.g. "Naples"
                "lat": round(float(self.sub_lat[i]), 4),
                "lon": round(float(self.sub_lon[i]), 4),
                "load_mw": round(float(load_by_sub[i]), 1),
                "kv_max": float(kv_by_sub[i]),
            }
            for i in range(len(self.sub_ids))
        ]
        branches = [
            {
                "id": int(self.br_ids[i]),
                "from_sub": int(self.sub_ids[self.bus_sub_idx[self.f[i]]]),
                "to_sub": int(self.sub_ids[self.bus_sub_idx[self.t[i]]]),
                "kv": float(self.br_kv[i]),
                "rate_mva": float(self.rate[i]),
                "base_pct": round(float(self.base.loading_pct[i]), 1),
                "base_flow": int(round(float(self.base.flow[i]))),  # signed MW, from -> to positive
            }
            for i in range(self.m)
        ]
        meta = dict(self.meta)
        meta.update({"synthetic": True, "bus_count": self.n, "branch_count": self.m, "sub_count": len(self.sub_ids)})
        return {"meta": meta, "subs": subs, "branches": branches}
