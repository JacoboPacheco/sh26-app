"""OpenStreetMap inputs for the locate stage: a few bulk Overpass queries, cached in raw/osm/ (gitignored).

Three queries in total, never one per project (the Overpass API is a shared, volunteer-run service):

    substations_SC / substations_GA   every power=substation and power=plant in the state (tags + center)
    lines_SC_GA                       every power=line with a voltage tag in SC + GA (tags + geometry)

Responses are cached as-is; `refresh=True` (build.py --refresh-osm) re-downloads them. Nominatim is only a
fallback for names that match nothing in OSM's power features: at most 60 distinct queries in total (the cache
in raw/osm/nominatim.json counts them), 1 per second; a re-run never repeats a call and never exceeds the cap.

OSM data (c) OpenStreetMap contributors, ODbL 1.0: https://www.openstreetmap.org/copyright
"""

from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from pathlib import Path

RAW = Path(__file__).parent / "raw" / "osm"
UA = "Overload-ShellHacks2026/1.0 (hackathon project)"
OVERPASS = "https://overpass-api.de/api/interpreter"
NOMINATIM = "https://nominatim.openstreetmap.org/search"
NOMINATIM_MAX_CALLS = 60

QUERIES = {
    "substations_SC": """[out:json][timeout:240];
area["ISO3166-2"="US-SC"]["admin_level"="4"]->.a;
(nwr["power"="substation"](area.a);nwr["power"="plant"](area.a););
out center tags;""",
    "substations_GA": """[out:json][timeout:240];
area["ISO3166-2"="US-GA"]["admin_level"="4"]->.a;
(nwr["power"="substation"](area.a);nwr["power"="plant"](area.a););
out center tags;""",
    "lines_SC_GA": """[out:json][timeout:300][maxsize:536870912];
(area["ISO3166-2"="US-SC"]["admin_level"="4"];area["ISO3166-2"="US-GA"]["admin_level"="4"];)->.a;
way["power"="line"]["voltage"](area.a);
out tags geom;""",
}


def _post(query: str) -> bytes:
    req = urllib.request.Request(
        OVERPASS,
        data=urllib.parse.urlencode({"data": query}).encode(),
        headers={"User-Agent": UA, "Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req, timeout=360) as r:
        return r.read()


def overpass(name: str, refresh: bool = False) -> dict:
    """The cached response of one of QUERIES (downloads it on the first run or with refresh)."""
    path = RAW / f"{name}.json"
    if path.exists() and not refresh:
        return json.loads(path.read_text(encoding="utf-8"))
    RAW.mkdir(parents=True, exist_ok=True)
    last_err = None
    for attempt in range(3):
        try:
            body = _post(QUERIES[name])
            data = json.loads(body)
            data["_fetched_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            data["_query"] = QUERIES[name]
            path.write_text(json.dumps(data), encoding="utf-8")
            return data
        except Exception as e:  # rate limits (429) and gateway timeouts (504) are common: back off politely
            last_err = e
            time.sleep(30 * (attempt + 1))
    raise RuntimeError(f"Overpass query {name} failed after 3 attempts: {last_err}")


def fetched_at(name: str) -> str | None:
    path = RAW / f"{name}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8")).get("_fetched_at")


class Nominatim:
    """Rate-limited (1/s), capped, cached geocoder for leftovers. Only searches inside the US."""

    def __init__(self, allow_network: bool = True):
        self.path = RAW / "nominatim.json"
        self.cache: dict = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}
        self.calls = 0
        self.allow_network = allow_network
        self._last = 0.0

    def search(self, q: str) -> list[dict] | None:
        """Cached results for q, or None when the call budget is spent / network is off."""
        if q in self.cache:
            return self.cache[q]
        # the cap is on distinct questions ever asked (the cache), so reruns never add up past it
        if not self.allow_network or self.calls >= NOMINATIM_MAX_CALLS or len(self.cache) >= NOMINATIM_MAX_CALLS:
            return None
        wait = 1.1 - (time.time() - self._last)
        if wait > 0:
            time.sleep(wait)
        url = NOMINATIM + "?" + urllib.parse.urlencode({"q": q, "format": "jsonv2", "countrycodes": "us", "limit": 5, "addressdetails": 1})
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        self.calls += 1
        self._last = time.time()
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                res = json.loads(r.read())
        except Exception:
            return None
        self.cache[q] = res
        RAW.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.cache, indent=1), encoding="utf-8")
        return res
