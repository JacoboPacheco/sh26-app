"""Build backend/demo/hospitals_us.json — every named hospital in the contiguous U.S. from OpenStreetMap.

    backend/venv/Scripts/python backend/demo/build_hospitals.py            # fetch what isn't cached, then build
    backend/venv/Scripts/python backend/demo/build_hospitals.py --offline  # build from the cache only
    backend/venv/Scripts/python backend/demo/build_hospitals.py --only FL,GA

Source: OpenStreetMap (amenity=hospital: nodes, ways and relations, with each way's or relation's
center), © OpenStreetMap contributors, ODbL 1.0 — credited on screen wherever a hospital is shown.
Fetched ONCE from the Overpass API (one query per state, a pause between queries, one retry) and
cached in backend/demo/raw/hospitals_osm/<ST>.json (gitignored, like the other raw sources); the
built file is committed and nothing is fetched at runtime.

Cleaning: unnamed features and parts of a hospital (an entrance, a parking garage, a helipad) are
dropped; the same hospital mapped several times (a campus node plus its building outlines, or a
unit named after it) is kept once: the same normalized name, or its name plus more words, within
1.5 km. `emergency` is kept when
OSM says emergency=yes, `beds` when OSM has a plain number.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
RAW = os.path.join(HERE, "raw", "hospitals_osm")
OUT = os.path.join(HERE, "hospitals_us.json")
INDEX = os.path.join(HERE, "grids", "index.json")

OVERPASS = "https://overpass-api.de/api/interpreter"
USER_AGENT = "Overload/1.0 (ShellHacks 2026 hackathon demo; one-time fetch of U.S. hospitals, credited ODbL)"
PAUSE_S = 4.0  # between queries
RETRY_WAIT_S = 45.0  # before the one retry
QUERY_TIMEOUT_S = 180
SOURCE = "© OpenStreetMap contributors, ODbL"
LICENSE_URL = "https://www.openstreetmap.org/copyright"
MAX_HOSPITALS = 8000
DEDUP_KM = 1.5
# features tagged amenity=hospital that are a part of one, not a hospital of their own
NOT_A_HOSPITAL = re.compile(r"\b(entrance|parking|garage|helipad|heliport|loading dock)\b", re.I)


def states() -> list[str]:
    """The lower 48 (grids/index.json) plus DC, whose hospitals the Maryland model covers."""
    with open(INDEX, encoding="utf-8") as fh:
        codes = sorted(json.load(fh)["regions"])
    return codes + ["DC"]


def query(code: str) -> str:
    return (
        f"[out:json][timeout:{QUERY_TIMEOUT_S}];"
        f'area["ISO3166-2"="US-{code}"]["boundary"="administrative"]->.s;'
        'nwr["amenity"="hospital"](area.s);'
        "out center tags;"
    )


def fetch(code: str) -> dict:
    data = urllib.parse.urlencode({"data": query(code)}).encode()
    req = urllib.request.Request(OVERPASS, data=data, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=QUERY_TIMEOUT_S + 30) as resp:
        payload = json.loads(resp.read())
    if payload.get("remark") and "error" in payload["remark"].lower():
        raise RuntimeError(payload["remark"])
    return payload


def cached(code: str, offline: bool) -> dict | None:
    """The raw Overpass answer for a state: from the cache, else fetched (one retry) and cached."""
    path = os.path.join(RAW, f"{code}.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    if offline:
        return None
    for attempt in (1, 2):
        try:
            payload = fetch(code)
            break
        except (urllib.error.URLError, OSError, RuntimeError, ValueError) as exc:
            print(f"  {code}: attempt {attempt} failed: {exc}", file=sys.stderr)
            if attempt == 2:
                return None
            time.sleep(RETRY_WAIT_S)
    os.makedirs(RAW, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh)
    time.sleep(PAUSE_S)
    return payload


def _km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(a))


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()


def _beds(v) -> int | None:
    s = str(v or "").strip()
    return int(s) if s.isdigit() and 0 < int(s) < 5000 else None


def rows(code: str, payload: dict) -> list[dict]:
    """One row per named hospital in a state's answer, duplicates merged."""
    out: list[dict] = []
    for el in payload.get("elements", []):
        tags = el.get("tags") or {}
        # OSM joins several names with ";" ("Destin ER;HCA Florida Destin Emergency"): keep the first
        name = " ".join(str(tags.get("name") or "").split(";")[0].split())
        if not name:
            continue
        if el.get("type") == "node":
            lat, lon = el.get("lat"), el.get("lon")
        else:
            c = el.get("center") or {}
            lat, lon = c.get("lat"), c.get("lon")
        if lat is None or lon is None:
            continue
        row = {"name": name[:120], "lat": round(float(lat), 4), "lon": round(float(lon), 4), "state": code}
        if str(tags.get("emergency", "")).lower() == "yes":
            row["emergency"] = True
        beds = _beds(tags.get("beds"))
        if beds:
            row["beds"] = beds
        if NOT_A_HOSPITAL.search(name):
            continue
        out.append(row)
    # the same hospital mapped more than once: the same name, or a unit of it ("Gulf Coast Medical
    # Center Skilled Nursing Unit" next to "Gulf Coast Medical Center"), within DEDUP_KM. Shortest
    # name first, so every unit folds into its hospital.
    kept: list[dict] = []
    for row in sorted(out, key=lambda r: (len(_norm(r["name"])), r["name"].lower(), r["lat"])):
        key = _norm(row["name"])
        dup = next(
            (
                r
                for r in kept
                if (key == _norm(r["name"]) or key.startswith(_norm(r["name"]) + " "))
                and _km(r["lat"], r["lon"], row["lat"], row["lon"]) <= DEDUP_KM
            ),
            None,
        )
        if dup is None:
            kept.append(row)
            continue
        if row.get("emergency"):
            dup["emergency"] = True
        if row.get("beds") and not dup.get("beds"):
            dup["beds"] = row["beds"]
    kept.sort(key=lambda r: (r["name"].lower(), r["lat"]))
    return kept


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true", help="build from the cache only, fetch nothing")
    ap.add_argument("--only", default="", help="comma-separated state codes to fetch (the build still uses every cached state)")
    args = ap.parse_args()
    codes = states()
    only = {c.strip().upper() for c in args.only.split(",") if c.strip()}
    hospitals: list[dict] = []
    missing: list[str] = []
    for code in codes:
        payload = cached(code, args.offline or bool(only and code not in only))
        if payload is None:
            missing.append(code)
            continue
        got = rows(code, payload)
        print(f"{code}: {len(got):4d} named hospitals ({len(payload.get('elements', []))} features)")
        hospitals += got
    if len(hospitals) > MAX_HOSPITALS:
        # keep the ones OSM says more about (an emergency department, a bed count) first
        hospitals.sort(key=lambda r: (not r.get("emergency"), not r.get("beds")))
        hospitals = sorted(hospitals[:MAX_HOSPITALS], key=lambda r: (r["state"], r["name"].lower()))
    else:
        hospitals.sort(key=lambda r: (r["state"], r["name"].lower()))
    doc = {
        "source": SOURCE,
        "license_url": LICENSE_URL,
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "query": 'amenity=hospital (nodes, ways, relations; out center), one Overpass query per state',
        "count": len(hospitals),
        "states_missing": missing,
        "hospitals": hospitals,
    }
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, separators=(",", ":"), ensure_ascii=False)
    size = os.path.getsize(OUT)
    print(f"wrote {OUT}: {len(hospitals)} hospitals, {size / 1e6:.2f} MB; missing states: {missing or 'none'}")
    return 0 if not missing else 1


if __name__ == "__main__":
    sys.exit(main())
