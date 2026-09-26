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
from dataclasses import dataclass, field

import numpy as np
import scipy.sparse as sp
from scipy.sparse.csgraph import connected_components
from scipy.sparse.linalg import splu

BASE_MVA = 100.0
HOMES_PER_MW = 700  # ~1.4 kW average household load; always labeled an estimate in the UI
MAX_STEPS = 30
HOT_PCT = 80.0
OVER_PCT = 100.0
_EPS = 1e-9


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

    # ------------------------------------------------------------------ loading
    @classmethod
    def from_file(cls, path: str) -> "Grid":
        with open(path, encoding="utf-8") as fh:
            return cls(json.load(fh))

    def variant(self, load_factor: float) -> "Grid":
        """The same network at another load level (shares the parsed JSON, not the arrays)."""
        return Grid(self._data, load_factor)

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
    def _balance(self, active: np.ndarray, extra_load: np.ndarray):
        """Dispatch generation island by island so every component balances.

        Returns (P per bus in per-unit, served_load, gen, tie_eff, lost_mw, dark_bus, comp, ref_buses, weights)."""
        adj = sp.coo_matrix(
            (np.ones(int(active.sum())), (self.f[active], self.t[active])), shape=(self.n, self.n)
        )
        ncomp, comp = connected_components(adj, directed=False)
        load = self.pd + extra_load
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
                served[idx] *= (Gmax + T) / L
                lost += need - Gmax
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
        lost_bus = self.pd * (1.0 - frac)
        lost_existing = float(lost_bus.sum())
        lost_extra = float((extra_load * (1.0 - frac)).sum())
        return P, served, gen, tie, lost, dark, comp, np.array(refs, dtype=int), weights, lost_existing, lost_extra, lost_bus

    def solve(self, active: np.ndarray, extra_load: np.ndarray | None = None, rate: np.ndarray | None = None) -> State:
        if extra_load is None:
            extra_load = np.zeros(self.n)
        if rate is None:
            rate = self.rate
        P, served, gen, tie, lost, dark, comp, refs, weights, lost_existing, lost_extra, lost_bus = self._balance(
            active, extra_load
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
    def _sensitivity(self, buses: np.ndarray) -> np.ndarray:
        """Change in branch flow (MW) per MW of load added at each bus in `buses`, balanced by
        the base-case slack weights of that bus's island. Shape (m, len(buses))."""
        st = self.base
        d = np.zeros((self.n, len(buses)))
        for j, k in enumerate(buses):
            comp_mask = st.comp == st.comp[k]
            d[comp_mask, j] = st.weights[comp_mask]  # generators pick the MW up ...
            d[k, j] -= 1.0  # ... the load takes it
        theta = np.zeros((self.n, len(buses)))
        if st.lu is not None and len(st.keep):
            theta[st.keep] = st.lu.solve(d[st.keep] / BASE_MVA)
        return (theta[self.f] - theta[self.t]) / self.x[:, None] * BASE_MVA

    def headroom_for_buses(self, buses: np.ndarray) -> np.ndarray:
        """MW that can be added at each bus before the first branch reaches its rating."""
        dF = self._sensitivity(buses)
        f0 = self.base.flow[:, None]
        rate = self.rate[:, None]
        with np.errstate(divide="ignore", invalid="ignore"):
            t_pos = (rate - f0) / dF  # flow rising toward +rate
            t_neg = (-rate - f0) / dF  # flow falling toward -rate
        t = np.where(dF > _EPS, t_pos, np.where(dF < -_EPS, t_neg, np.inf))
        t = np.where(np.abs(f0) >= rate, np.inf, t)  # already over in the base case: not a *new* overload
        t = np.where(t < 0, np.inf, t)
        active = self.base.active[:, None]
        t = np.where(active, t, np.inf)
        return np.min(t, axis=0)

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
    def lost_by_sub(self, state: State, min_mw: float = 0.1) -> dict[int, float]:
        """Existing load lost per substation (MW), only where it's at least `min_mw`."""
        per = np.zeros(len(self.sub_ids))
        np.add.at(per, self.bus_sub_idx, state.lost_bus if state.lost_bus is not None else 0.0)
        return {int(self.sub_ids[i]): round(float(per[i]), 1) for i in np.flatnonzero(per >= min_mw)}

    def cascade(self, bus: int | None, mw: float, trip: list[int] | None = None) -> dict:
        """One data center (or none); see cascade_case."""
        return self.cascade_case(self.extra_load([(bus, mw)] if bus is not None else []), trip)

    def cascade_case(
        self, extra: np.ndarray, trip: list[int] | None = None, upgrades: dict[int, float] | None = None
    ) -> dict:
        """Trip the most overloaded branch, re-solve, repeat (SPEC.md M2). `trip` knocks branches out
        before step 1 (a hurricane) and is reported as step 0; `upgrades` raises ratings (Fix it).

        Each step lists the substations that newly lose load (`newly_affected`: [[sub id, MW lost]]),
        so the UI can name towns as they go dark."""
        rate = self.rates_with(upgrades)
        active = np.ones(self.m, dtype=bool)
        for bid in trip or []:
            active[self.br_index[int(bid)]] = False
        steps = []
        state = self.solve(active, extra, rate)
        prev_dark = np.zeros(self.n, dtype=bool)
        seen_affected: set[int] = set()
        capped = False
        n = 0
        tripped_ids = [int(b) for b in (trip or [])]
        while True:
            over = np.flatnonzero(state.active & (state.loading_pct > OVER_PCT + 1e-6))
            newly_dark = state.dark_bus & ~prev_dark
            if n > 0 or len(trip or []):
                lost = self.lost_by_sub(state)
                fresh = [[sid, mw_] for sid, mw_ in lost.items() if sid not in seen_affected]
                seen_affected.update(lost)
                steps.append(
                    {
                        "n": n,
                        "tripped": tripped_ids,
                        "dark_subs": sorted({int(self.sub_ids[self.bus_sub_idx[i]]) for i in np.flatnonzero(newly_dark)}),
                        "newly_affected": sorted(fresh, key=lambda x: -x[1]),
                        "hot": [self.branch_info(i, state) for i in np.flatnonzero(state.active & (state.loading_pct > HOT_PCT))],
                        "lost_mw": round(state.lost_existing_mw, 1),
                        "homes": int(round(state.lost_existing_mw * HOMES_PER_MW)),
                        "site_dark_mw": round(state.lost_extra_mw, 1),
                    }
                )
            prev_dark = prev_dark | state.dark_bus
            if not len(over):
                break
            if n >= MAX_STEPS:
                capped = True
                break
            worst = int(over[np.argmax(state.loading_pct[over])])
            active = state.active.copy()
            active[worst] = False
            tripped_ids = [int(self.br_ids[worst])]
            n += 1
            state = self.solve(active, extra, rate)
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
            "site_dark_mw": round(state.lost_extra_mw, 1),
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
