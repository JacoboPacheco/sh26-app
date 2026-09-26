"""Stage 3, locate: match every endpoint name from the filings to an OpenStreetMap feature.

The index is every named power=substation / power=plant in SC and GA (osm.py's cached bulk queries).
Names on both sides go through the same key function (normalize.endpoint_key), then:

  exact    the keys are equal                      'EVANS PRIMARY' = 'Evans Primary Substation'
  variant  equal after dropping generic words      'THURMOND DAM' ~ 'Thurmond Substation',
           (PRIMARY, PLANT, DAM, STATION, ...)      'STEVENS CREEK' ~ 'Stevens Creek Power Plant'
           and spelling variants (-BOROUGH/-BORO)   'QUEENSBORO' ~ 'Queensborough Substation'
  fuzzy    close spelling (difflib >= 0.88)          'TALLBOT' ~ 'Talbot ...'

Features of the same name within 2 km are one site (a plant and its switchyard). When a name matches
several sites, the pair of sites that makes the line shortest wins, then the operator, then the planning
zone's median location (learned from the confident matches in a first pass).

Confidence, recorded on every endpoint with the reason:
  high    exact/variant name, operator consistent with the filer, only one site with that name
  medium  exact/variant name, but no or another operator, or several sites and one had to be chosen
  low     fuzzy name, or no OSM feature by that name: Nominatim found the place and the nearest unnamed
          transmission substation (or, failing that, the place itself) is used
  null    not located
"""

from __future__ import annotations

import difflib
import re
import statistics

import geo
import osm
from normalize import endpoint_key

OPERATORS = {
    "DESC": re.compile(r"DOMINION|SCE&G|SOUTH CAROLINA ELECTRIC|SOUTH CAROLINA GAS|SCANA", re.I),
    "GPC": re.compile(r"GEORGIA POWER|SOUTHERN COMPANY|SAVANNAH ELECTRIC", re.I),
    "GTC": re.compile(r"GEORGIA TRANSMISSION|\bGTC\b", re.I),
    "MEAG": re.compile(r"\bMEAG\b|MUNICIPAL ELECTRIC AUTHORITY", re.I),
    "DU": re.compile(r"DALTON UTILITIES", re.I),
}
ITS = ("GPC", "GTC", "MEAG", "DU")  # the Georgia Integrated Transmission System members plan together
HOME = {"DESC": "SC", "GPC": "GA", "GTC": "GA", "MEAG": "GA", "DU": "GA"}
STATE_NAMES = {"SC": "South Carolina", "GA": "Georgia"}
SITE_KM = 2.0
GENERIC = [
    "COMBINED CYCLE FACILITY", "ENERGY FACILITY", "ENERGY CENTER", "ELECTRIC GENERATING PLANT", "GENERATING PLANT",
    "GENERATING STATION", "POWER PLANT", "POWER STATION", "STEAM PLANT", "NUCLEAR PLANT", "NUCLEAR STATION",
    "HYDROELECTRIC STATION", "HYDROELECTRIC PLANT", "HYDROELECTRIC", "HYDRO", "PLANT", "STATION", "DAM", "TIE",
    "TAP", "SWITCHING", "PRIMARY", "NEW", "TRANSMISSION", "DISTRIBUTION", "SUBSTATION", "GEORGIA", "SOUTH CAROLINA",
]
_GEN = sorted(GENERIC, key=len, reverse=True)


def core(key: str) -> str:
    """The key without generic words at either end, and with spelling variants folded."""
    s = f" {key} "
    s = re.sub(r"BOROUGH\b", "BORO", s)
    changed = True
    while changed:
        changed = False
        for g in _GEN:
            for pat in (rf"^ {g} ", rf" {g} $"):
                t = re.sub(pat, " ", s)
                if t != s and t.strip():
                    s, changed = t, True
    return s.strip()


def max_edits(name: str) -> int:
    """Typos tolerated in a fuzzy match: none for short names ('GRADY' is not 'GRAY'), 1 up to 12 letters
    ('ADAMSVILLE' is not 'ADAIRSVILLE'), else 2."""
    n = len(name.replace(" ", ""))
    return 0 if n <= 5 else 1 if n <= 12 else 2


def edit_distance(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def op_consistent(utility: str, operator: str | None) -> bool:
    if not operator:
        return False
    if OPERATORS[utility].search(operator):
        return True
    return utility in ITS and any(OPERATORS[u].search(operator) for u in ITS)


def _kv_list(v: str | None) -> list[int]:
    out = []
    for part in re.split(r"[;,]", v or ""):
        part = part.strip()
        if part.isdigit() and int(part) >= 1000:
            out.append(int(part) // 1000)
    return out


class Index:
    """Named and unnamed OSM power features of SC + GA."""

    def __init__(self, refresh: bool = False):
        self.features: list[dict] = []
        for st in ("SC", "GA"):
            for e in osm.overpass(f"substations_{st}", refresh=refresh)["elements"]:
                t = e.get("tags", {})
                c = e.get("center") or ({"lat": e["lat"], "lon": e["lon"]} if "lat" in e else None)
                if not c:
                    continue
                f = {
                    "type": e["type"], "id": e["id"], "name": t.get("name"), "operator": t.get("operator"),
                    "power": t.get("power"), "substation": t.get("substation"), "kv": _kv_list(t.get("voltage")),
                    "lat": c["lat"], "lon": c["lon"], "state": st,
                }
                if f["name"]:
                    f["key"] = endpoint_key(f["name"])
                    f["core"] = core(f["key"])
                self.features.append(f)
        self.named = [f for f in self.features if f.get("key")]
        self.by_key: dict[str, list[dict]] = {}
        self.by_core: dict[str, list[dict]] = {}
        for f in self.named:
            self.by_key.setdefault(f["key"], []).append(f)
            self.by_core.setdefault(f["core"], []).append(f)

    # ------------------------------------------------------------------ candidates

    def candidates(self, key: str) -> tuple[str | None, list[dict]]:
        """(tier, features) for the best tier that matches anything."""
        if key in self.by_key:
            exact = list(self.by_key[key])
            # variant matches at the same site count as the same place (a plant beside its substation)
            extra = [f for f in self.by_core.get(core(key), []) if f not in exact]
            return "exact", exact + [f for f in extra if any(geo.haversine_km((f["lat"], f["lon"]), (x["lat"], x["lon"])) < SITE_KM for x in exact)]
        c = core(key)
        if c and len(c) >= 3 and c in self.by_core:
            return "variant", list(self.by_core[c])
        allowed = max_edits(c)
        if not allowed:
            return None, []
        hits = []
        for f in self.named:
            fc = f["core"]
            if not fc or fc[0] != c[0] or abs(len(fc) - len(c)) > allowed:
                continue
            if difflib.SequenceMatcher(None, c, fc).quick_ratio() < 0.8:
                continue
            if edit_distance(c, fc) <= allowed:
                hits.append(f)
        return ("fuzzy", hits) if hits else (None, [])

    @staticmethod
    def sites(features: list[dict]) -> list[list[dict]]:
        """Group features within SITE_KM of each other."""
        sites: list[list[dict]] = []
        for f in features:
            for s in sites:
                if any(geo.haversine_km((f["lat"], f["lon"]), (x["lat"], x["lon"])) < SITE_KM for x in s):
                    s.append(f)
                    break
            else:
                sites.append([f])
        return sites

    def nearby_unnamed(self, lat: float, lon: float, key: str, kv: list[int], within_km: float = 6.0) -> tuple[dict, float] | None:
        """The nearest transmission-level substation that is unnamed or carries this name."""
        best = None
        need = min(kv) if kv else 69
        for f in self.features:
            if f["power"] != "substation":
                continue
            if f.get("key") and core(key) not in f["key"]:
                continue
            tx = (f["kv"] and max(f["kv"]) >= need) or f["substation"] in ("transmission", "switching")
            if not tx:
                continue
            d = geo.haversine_km((lat, lon), (f["lat"], f["lon"]))
            if d <= within_km and (best is None or d < best[1]):
                best = (f, d)
        return best


def _site_summary(site: list[dict], tier: str, utility: str) -> dict:
    """The site's representative feature: exact name first, then operator-consistent, then substation, then way."""
    def rank(f):
        return (
            0 if tier != "exact" or f.get("_exact") else 1,
            0 if op_consistent(utility, f["operator"]) else 1,
            0 if f["power"] == "substation" else 1,
            0 if f["type"] != "node" else 1,
        )
    rep = sorted(site, key=rank)[0]
    return {"rep": rep, "features": site, "op_ok": any(op_consistent(utility, f["operator"]) for f in site), "tier": tier}


HOME_REACH_KM = 30  # a filer's endpoint may sit just across its state line (Stevens Creek, Thurmond, Purrysburg), no farther

# names the filings abbreviate beyond what the key function can undo; every use is written into the match reason
ALIASES = {
    "VCS1": ("VIRGIL C SUMMER NUCLEAR", "read 'VCS1' as the V.C. Summer nuclear station site (DESC's abbreviation; an assumption)"),
    "VCS2": ("VIRGIL C SUMMER NUCLEAR", "read 'VCS2' as the V.C. Summer nuclear station site (DESC's abbreviation; an assumption)"),
    "JACK MCDONOUGH": ("PLANT MCDONOUGH ATKINSON", "read 'Jack McDonough' as OSM's 'Plant McDonough-Atkinson' (Plant Jack McDonough; an assumption)"),
    "ATKINSON": ("PLANT MCDONOUGH ATKINSON", "read 'Atkinson' as OSM's 'Plant McDonough-Atkinson' (an assumption)"),
}


def outside_home_km(f: dict, utility: str) -> float:
    home = HOME[utility]
    if f["state"] == home:
        return 0.0
    cache = f.setdefault("_out_km", {})
    if home not in cache:
        cache[home] = geo.km_to_state(f["lat"], f["lon"], home)
    return cache[home]


def endpoint_sites(index: Index, key: str, utility: str) -> list[dict]:
    alias = ALIASES.get(key)
    tier, feats = index.candidates(alias[0] if alias else key)
    feats = [f for f in feats if outside_home_km(f, utility) <= HOME_REACH_KM]
    if not feats:
        return []
    for f in feats:
        f["_exact"] = f.get("key") == key
    sites = [_site_summary(s, tier, utility) for s in Index.sites(feats)]
    for s in sites:
        s["alias"] = alias[1] if alias else None
    return sites


TIER_COST = {"exact": 0.0, "variant": 3.0, "fuzzy": 30.0}


def _site_cost(site: dict, utility: str, zone_center: tuple[float, float] | None) -> float:
    rep = site["rep"]
    cost = TIER_COST[site["tier"]] + (0 if site["op_ok"] else 25.0)
    cost += 0 if rep["state"] == HOME[utility] else 10.0
    if zone_center:
        d = geo.haversine_km(zone_center, (rep["lat"], rep["lon"]))
        cost += max(0.0, d - 60.0)
    return cost


def _describe(rep: dict) -> str:
    kind = "plant" if rep["power"] == "plant" else "substation"
    op = f" ({rep['operator']})" if rep.get("operator") else " (no operator tagged)"
    return f"OSM {kind} '{rep['name']}'{op}, {rep['type']} {rep['id']}"


def _endpoint_result(ep: dict, site: dict | None, n_sites: int, utility: str, why_chosen: str | None) -> dict:
    out = {"name": ep["name"], "raw": ep["raw"], "key": ep["key"], "lat": None, "lon": None, "osm": None, "confidence": None, "match": None, "state": None}
    if not site:
        return out
    rep = site["rep"]
    out.update(lat=round(rep["lat"], 6), lon=round(rep["lon"], 6), state=rep["state"])
    out["osm"] = {"type": rep["type"], "id": rep["id"], "name": rep["name"], "operator": rep.get("operator"),
                  "url": f"https://www.openstreetmap.org/{rep['type']}/{rep['id']}"}
    tier = site["tier"]
    parts = [_describe(rep)]
    if tier == "exact":
        parts.append("same name")
    elif tier == "variant":
        parts.append(f"same name once generic words are dropped ('{ep['key']}' ~ '{rep['key']}')")
    else:
        parts.append(f"similar spelling ('{ep['key']}' ~ '{rep['key']}')")
    if site["op_ok"] and op_consistent(utility, rep.get("operator")):
        parts.append(f"operator consistent with {utility}")
    elif site["op_ok"]:
        buddy = next(f for f in site["features"] if op_consistent(utility, f.get("operator")))
        parts.append(f"operator consistent with {utility}: the adjacent '{buddy['name']}' is tagged {buddy['operator']}")
    else:
        parts.append(f"operator not {utility}" + (" (a tie point or shared site is plausible)" if rep.get("operator") else ""))
    if n_sites > 1:
        parts.append(f"{n_sites} places have this name; {why_chosen}")
    if site.get("alias"):
        parts.append(site["alias"])
    if tier == "fuzzy":
        conf = "low"
    elif site["op_ok"] and n_sites == 1 and not site.get("alias"):
        conf = "high"
    else:
        conf = "medium"
    out["confidence"] = conf
    out["match"] = "; ".join(parts)
    return out


def locate_project(index: Index, p: dict, zone_center: tuple[float, float] | None) -> list[dict]:
    """Endpoints of one normalized project, located. p has utility, endpoints [{name, raw, key}], miles, zone."""
    utility = p["utility"]
    eps = p["endpoints_parsed"]
    options = [endpoint_sites(index, ep["key"], utility) for ep in eps]
    if len(eps) == 2 and options[0] and options[1]:
        best = None
        for a in options[0]:
            for b in options[1]:
                d = geo.haversine_km((a["rep"]["lat"], a["rep"]["lon"]), (b["rep"]["lat"], b["rep"]["lon"]))
                cost = d + _site_cost(a, utility, zone_center) + _site_cost(b, utility, zone_center)
                if p.get("miles"):
                    cost += max(0.0, d - (p["miles"] * 1.609344 * 1.15 + 3))
                if best is None or cost < best[0]:
                    best = (cost, a, b, d)
        _, a, b, d = best
        why = f"chose the pair {d:.1f} km apart" + (f" (the filing says {p['miles']:g} mi of line)" if p.get("miles") else "")
        return [
            _endpoint_result(eps[0], a, len(options[0]), utility, why),
            _endpoint_result(eps[1], b, len(options[1]), utility, why),
        ]
    out = []
    for ep, sites in zip(eps, options):
        if not sites:
            out.append(_endpoint_result(ep, None, 0, utility, None))
            continue
        ranked = sorted(sites, key=lambda s: _site_cost(s, utility, zone_center))
        why = "chose the one " + ("nearest this planning zone's other projects" if zone_center else "with a consistent operator / in the filer's state")
        out.append(_endpoint_result(ep, ranked[0], len(sites), utility, why))
    return out


def zone_centers(projects: list[dict]) -> dict[str, tuple[float, float]]:
    """Median location of each Georgia planning zone, from projects located with high confidence only."""
    pts: dict[str, list[tuple[float, float]]] = {}
    for p in projects:
        located = [e for e in p.get("endpoints", []) if e.get("lat") is not None]
        if not p.get("zone") or not located or any(e["confidence"] != "high" for e in located):
            continue
        lat = sum(e["lat"] for e in located) / len(located)
        lon = sum(e["lon"] for e in located) / len(located)
        pts.setdefault(p["zone"], []).append((lat, lon))
    return {z: (statistics.median(x[0] for x in v), statistics.median(x[1] for x in v)) for z, v in pts.items() if len(v) >= 3}


# ---------------------------------------------------------------------- Nominatim fallback

# islands are left out on purpose: small islands share names with inland substations (Scout, Dawson)
PLACE_TYPES = {"city", "town", "village", "hamlet", "locality", "suburb", "neighbourhood", "isolated_dwelling", "quarter"}
ADDRESS_PLACES = ("hamlet", "village", "town", "city", "suburb", "neighbourhood", "quarter", "locality", "isolated_dwelling")
ROAD_WORDS = r"(ROAD|STREET|DRIVE|AVENUE|LANE|WAY|PARKWAY|HIGHWAY|BOULEVARD|RD|ST|DR|AVE|LN|PKWY|HWY|BLVD)"
WATER_WORDS = r"(CREEK|RESERVOIR|LAKE|RIVER|BRANCH|POND)"
PLACE_SUFFIX = r"\s+(ISLAND|VILLAGE|ESTATES|TERMINAL|COMMUNITY|HEIGHTS|CROSSROADS)$"
FOREIGN = re.compile(r"\((APC|FPL|TVA|DEP|DUKE|LG&E)\)", re.I)  # endpoints in another utility's system


def _name_core(name: str | None) -> str:
    return re.sub(PLACE_SUFFIX, "", endpoint_key(name or ""))


def _nominatim_ok(r: dict, ep_key: str) -> tuple[bool, str]:
    """A result is accepted only when it carries the endpoint's own name and is the right kind of thing."""
    cls, typ = r.get("category") or r.get("class"), r.get("type")
    name_core = _name_core(r.get("name"))
    addr = r.get("address", {})
    in_address = [k for k in ADDRESS_PLACES if _name_core(addr.get(k)) == ep_key]
    is_road = re.search(rf"\b{ROAD_WORDS}$", ep_key) is not None
    is_water = re.search(rf"\b{WATER_WORDS}$", ep_key) is not None
    if cls == "place" and typ in PLACE_TYPES and name_core == ep_key:
        return True, f"the place {r.get('name')} ({typ})"
    if cls == "boundary" and r.get("addresstype") in PLACE_TYPES and name_core == ep_key:
        return True, f"the {r.get('addresstype')} of {r.get('name')}"
    if cls == "highway" and is_road and name_core == ep_key:
        return True, f"the road {r.get('name')}"
    if cls in ("natural", "water", "waterway") and is_water and name_core == ep_key:
        return True, f"the water body {r.get('name')}"
    if cls in ("power", "landuse", "man_made") and (name_core == ep_key or name_core.startswith(ep_key + " ")):
        return True, f"{r.get('name')} ({cls}/{typ})"
    if in_address and cls not in ("highway",):
        return True, f"{r.get('name') or cls} in {addr.get(in_address[0])} ({in_address[0]})"
    return False, ""


def nominatim_candidates(index: Index, geocoder: osm.Nominatim, ep: dict, home: str, need_kv: int | None) -> list[dict]:
    """Located versions of one endpoint name from Nominatim, best first. The same (state, name) always gives
    the same answer, whichever project asks: need_kv is the highest voltage any project gives that name, and the
    nearest unnamed substation at that voltage (10 km for >= 230 kV, 5 km below: lower-voltage stations are dense)
    stands in for the place itself."""
    if FOREIGN.search(ep.get("raw") or ""):
        return []
    if len(ep["key"]) < 4 or ep["key"] in {"NORTHWEST", "NORTHEAST", "SOUTHWEST", "SOUTHEAST", "CENTER", "CENTRAL"}:
        return []
    q = f"{ep['name']}, {STATE_NAMES[home]}"
    out = []
    for r in geocoder.search(q) or []:
        if r.get("address", {}).get("state") != STATE_NAMES[home]:
            continue
        ok, what = _nominatim_ok(r, ep["key"])
        if not ok:
            continue
        lat, lon = float(r["lat"]), float(r["lon"])
        found = {k: ep.get(k) for k in ("name", "raw", "key")}
        where = f"Nominatim found {what} (osm {r.get('osm_type')} {r.get('osm_id')})"
        radius = 10.0 if (need_kv or 0) >= 230 else 5.0
        near = index.nearby_unnamed(lat, lon, ep["key"], [need_kv] if need_kv else [], within_km=radius)
        if near:
            f, d = near
            found.update(lat=round(f["lat"], 6), lon=round(f["lon"], 6), state=f["state"], confidence="low")
            found["osm"] = {"type": f["type"], "id": f["id"], "name": f.get("name"), "operator": f.get("operator"),
                            "url": f"https://www.openstreetmap.org/{f['type']}/{f['id']}"}
            kvs = "/".join(str(k) for k in f["kv"]) + " kV" if f["kv"] else f"{f['substation']}"
            found["match"] = (f"no OSM substation is named '{ep['name']}'; {where}; the nearest {kvs} substation "
                              f"({'unnamed' if not f.get('name') else repr(f['name'])}, {f['type']} {f['id']}) is {d:.1f} km from it"
                              + (f" (the filings give this name up to {need_kv} kV)" if need_kv else ""))
        else:
            found.update(lat=round(lat, 6), lon=round(lon, 6), state=home, confidence="low")
            found["osm"] = {"type": r.get("osm_type"), "id": r.get("osm_id"), "name": r.get("name"), "operator": None,
                            "url": f"https://www.openstreetmap.org/{r.get('osm_type')}/{r.get('osm_id')}"}
            found["match"] = f"no OSM substation is named '{ep['name']}'; {where} and used it directly; the exact site is unknown"
        found["via"] = "nominatim"
        found["_road"] = what.startswith("the road ")
        found["_snapped"] = near is not None
        out.append(found)
    return out


ROAD_ZONE_KM = 40  # a street name alone ('First Avenue') exists in every town: it must sit well inside the zone
GENERIC_STREET = re.compile(
    r"^(FIRST|SECOND|THIRD|FOURTH|FIFTH|SIXTH|SEVENTH|EIGHTH|NINTH|TENTH|\d+(ST|ND|RD|TH)?|MAIN|BROAD|CHURCH|MILL|PARK|"
    r"CENTER|HIGH|MARKET|WASHINGTON|OAK|PINE|ELM|MAPLE|RAILROAD|DEPOT|COLLEGE|SPRING|WATER)\s+" + ROAD_WORDS + r"$"
)


def fits(found: dict, p: dict, other: dict | None, zone_center: tuple[float, float] | None) -> bool:
    """A fallback location must sit near the project's other endpoint and inside its planning zone. A road
    found by name alone, with no other endpoint to anchor it, is only trusted when it's not a name every town
    has and it sits beside a transmission substation, and (Georgia) within ROAD_ZONE_KM of its planning zone's
    median: GA-13166 'First Avenue - North Columbus' otherwise lands on a First Avenue near Atlanta."""
    other_located = bool(other and other.get("lat") is not None)
    if other_located:
        d = geo.haversine_km((found["lat"], found["lon"]), (other["lat"], other["lon"]))
        if d > max(60.0, p["miles"] * 1.609344 * 1.3 + 10 if p.get("miles") else 0):  # filed miles are often a section
            return False
    if found.get("_road") and not other_located:
        if GENERIC_STREET.match(found.get("key") or "") or not found.get("_snapped"):
            return False
        if zone_center and geo.haversine_km((found["lat"], found["lon"]), zone_center) > ROAD_ZONE_KM:
            return False
    if zone_center and geo.haversine_km((found["lat"], found["lon"]), zone_center) > 100:
        return False
    return True
