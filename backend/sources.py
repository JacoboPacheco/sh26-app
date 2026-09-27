"""Sources and licenses: every dataset Overload uses and every reference its estimates cite, in one list
(GET /api/sources, shown on the Data page's "Sources" tab). Nothing is fetched here: the datasets are the
committed files built offline, and the references are the ones the cost, estimate and planning modules
already cite, read from those modules so this list can't drift from what the numbers actually use.
"""

import json
from pathlib import Path

from fastapi import APIRouter, Request

from limiter import limiter

router = APIRouter(tags=["sources"])
DEMO = Path(__file__).parent / "demo"

# The datasets behind the map, the engine and the pages. `license` is the data's own license; `used_for`
# says where it shows up, so a reader can check any number back to its source.
DATASETS = [
    {
        "id": "grid",
        "name": "Breakthrough Energy Sciences U.S. Test System (PowerSimData usa_tamu), from Texas A&M's ACTIVSg synthetic grids",
        "license": "CC BY 4.0",
        "url": "https://zenodo.org/records/3530898",
        "also": "https://github.com/Breakthrough-Energy/PowerSimData",
        "used_for": "Every grid on every page: the 48 state models' buses, lines, ratings, loads and power plants. SYNTHETIC: it is shaped like the real U.S. grid but represents no utility's actual network.",
    },
    {
        "id": "census_pop",
        "name": "U.S. Census Bureau, Vintage 2024 population estimates (NST-EST2024)",
        "license": "Public domain (U.S. government work)",
        "url": "https://www.census.gov/data/tables/time-series/demo/popest/2020s-state-total.html",
        "used_for": "People without power and people hit (estimates): each state model's load stands for its residents.",
    },
    {
        "id": "census_outline",
        "name": "U.S. Census Bureau, cartographic boundary file cb_2024_us_state_20m",
        "license": "Public domain (U.S. government work)",
        "url": "https://www2.census.gov/geo/tiger/GENZ2024/shp/cb_2024_us_state_20m.zip",
        "used_for": "The state and national outlines on every map.",
    },
    {
        "id": "osm_hospitals",
        "name": "OpenStreetMap hospitals (via the Overpass API)",
        "license": "ODbL 1.0 (© OpenStreetMap contributors)",
        "url": "https://www.openstreetmap.org/copyright",
        "used_for": "Hospitals inside a blackout, on the map and in the briefing.",
    },
    {
        "id": "osm_gridlock",
        "name": "OpenStreetMap substations and lines (Georgia and South Carolina)",
        "license": "ODbL 1.0 (© OpenStreetMap contributors)",
        "url": "https://www.openstreetmap.org/copyright",
        "used_for": "Placing each planned project from the utility filings on the Build together map, with a confidence for each.",
    },
    {
        "id": "compute_atlas",
        "name": "Compute Atlas (Kubiak, E.), U.S. data center sites",
        "license": "CC BY 4.0 (doi 10.5281/zenodo.22284476)",
        "url": "https://www.compute-atlas.com",
        "used_for": "The Data page's map and list of U.S. data center sites, merged with the catalog below (a site listed by two sources appears once).",
    },
    {
        "id": "epoch",
        "name": "Epoch AI, AI data centers",
        "license": "CC BY",
        "url": "https://epoch.ai/data/ai-data-centers",
        "used_for": "Frontier AI campuses on the Data page. Its column naming each campus's users is never read (tenants a company hasn't disclosed are never named).",
    },
    {
        "id": "catalog",
        "name": "Overload's catalog of announced U.S. AI data centers",
        "license": "Compiled for this project from public reporting; each entry links its own news or company sources",
        "url": None,
        "used_for": "The real proposals you can test (every one as reported, with its sources), and the Proposed data centers pages.",
    },
    {
        "id": "civic",
        "name": "State utility commissions, local decision bodies and dockets (researched Sat 26 Sep 2026)",
        "license": "Public records; each entry links its official source",
        "url": None,
        "used_for": "Where the decision stands and where to comment, on the Proposed data centers pages.",
    },
]

NOTES = [
    "Every grid is a synthetic test system, never a real utility's network. A real data center tested here is a campus of its reported size at its reported location on a synthetic model, not a prediction about the real project or utility.",
    "People, costs and outage times are estimates, each shown with its formula and sources.",
    "AI: Google Gemini writes and proposes; the power-flow engine checks every number (the How AI is used panel lists each use). Voice: ElevenLabs.",
]


def _refs() -> list[dict]:
    """The references the estimates cite, read from the modules that use them."""
    out: list[dict] = []
    try:
        import costs

        for k, s in costs.SOURCES.items():
            out.append({"id": f"cost.{k}", "name": s["name"], "url": s.get("url"), "used_for": "Cost estimates (blackout, upgrades, the campus's bill, who pays)"})
    except Exception:  # noqa: BLE001 — a module that fails to import just leaves its references out
        pass
    try:
        import gridlock

        for k, s in gridlock.SOURCES.items():
            out.append({"id": f"gridlock.{k}", "name": s["title"], "url": s.get("url"), "used_for": "Build together: what two projects building together could share (estimates)"})
    except Exception:  # noqa: BLE001
        pass
    return out


def _filings() -> list[dict]:
    try:
        doc = json.loads((DEMO / "gridlock" / "data" / "projects.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [{"id": f"filing.{s.get('id')}", "name": s.get("title"), "url": s.get("url"), "used_for": "Build together: the utilities' planned projects, as filed (public versions only)"}
            for s in doc.get("sources") or [] if isinstance(s, dict) and s.get("title")] + _desc_2026()


def _desc_2026() -> list[dict]:
    try:
        s = json.loads((DEMO / "gridlock" / "data" / "desc_2026_2030.json").read_text(encoding="utf-8")).get("source") or {}
    except (OSError, ValueError, AttributeError):
        return []
    if not s.get("title"):
        return []
    return [{"id": f"filing.{s.get('id') or 'desc_2026'}", "name": s["title"], "url": s.get("url"), "used_for": "Build together: the utilities' planned projects, as filed (public versions only)"}]


def _questions() -> list[dict]:
    try:
        doc = json.loads((DEMO / "questions.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    rows = doc.get("questions") if isinstance(doc, dict) else doc
    seen, out = set(), []
    for q in rows or []:
        for s in q.get("sources") or []:
            if isinstance(s, dict) and s.get("url") and s["url"] not in seen:
                seen.add(s["url"])
                out.append({"id": f"question.{len(out)}", "name": s.get("title") or s["url"], "url": s["url"], "used_for": "The questions to ask before a vote (why each matters)"})
    return out


@router.get("/api/sources")
@limiter.limit("60/minute")
def get_sources(request: Request):
    return {"datasets": DATASETS, "filings": _filings(), "references": _refs() + _questions(), "notes": NOTES}
