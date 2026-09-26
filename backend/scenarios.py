"""The Library: saved scenarios, their versions, and read-only share links.

A scenario is a named *case* — exactly what the grid endpoints solve (grid.CaseIn: region, the main
campus, more campuses, the load level, lines knocked out, upgrades, firm) — plus a summary of what
that case does (`result`), computed here, server-side, whenever the case is saved: the verdict
(holds / lines trip / people lose power), people without power (an estimate), cascade steps, lines
over limit, and the areas hit hardest. The list shows these numbers without re-solving; opening a
scenario re-solves it live (and GET /api/scenarios/{id} refreshes the stored numbers).

Per user (the shared demo account in practice), owner-only: another user's scenario is a plain 404.
Everyone shares the demo account, so the built-in examples (their note carries SEED_MARK) can't be
edited, unshared or deleted (403) — copy one with "save as a version" instead. Only the server makes
examples: POST /api/scenarios/examples writes the canonical set below into the caller's library
(idempotent; seed.py calls it), and a user's own note can't carry the mark.

Versions: POST /api/scenarios/{id}/version makes a child. Every version points at the original
(`parent_id` = the root), so a library is one level deep: originals, each with its versions in order.

Sharing: POST /api/scenarios/{id}/share hands out a random url-safe slug; GET /api/share/{slug} is
public and read-only — the name, note, case and result, never the owner or the row id.

The old body {name, lat, lon, mw, note} still works: a Florida campus at the summer peak.
"""

import json
import logging
import re
import secrets
from datetime import datetime, timezone
from pathlib import Path as FilePath
from typing import Annotated

import numpy as np
from fastapi import APIRouter, Depends, HTTPException, Path, Request
from pydantic import BaseModel, Field, StringConstraints, field_validator
from sqlalchemy.orm import Session

from auth import get_current_user
from database import get_db
from grid import (
    REGIONS,
    CaseIn,
    SiteIn,
    _case_header,
    case_firm_buses,
    check_case,
    check_site,
    people_fields,
    region_code,
)
from limiter import limiter
from models import Scenario, User
from powerflow import area_of

router = APIRouter(tags=["scenarios"])
log = logging.getLogger("uvicorn.error")

SEED_MARK = "(demo scenario)"  # frontend/src/store.jsx and seed.py use the same mark
RESULT_V = 1  # bump when the shape of `result` changes
MAX_SCENARIOS = 400  # per user: every visitor shares the demo account, so the library can't grow forever
MAX_VERSIONS = 40  # per original
AREAS_TOP = 5

# strip_whitespace runs before min_length, so "   " is rejected, not saved as "".
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
Note = Annotated[str, StringConstraints(strip_whitespace=True, max_length=280)]
Id = Annotated[int, Path(ge=1, le=2**31 - 1)]  # bounded: a huge id overflows the database driver
Slug = Annotated[str, Path(min_length=8, max_length=32)]
SLUG_RE = re.compile(r"^[A-Za-z0-9_-]{8,32}$")
KEY_RE = r"^[a-z0-9][a-z0-9-]{0,79}$"


def _finite_number(v):
    # A 400-digit JSON integer would overflow float() later; NaN/inf reach check_site's readable 422.
    # true/false would quietly become 1/0 (a "1 MW campus") — reject them.
    if isinstance(v, bool):
        raise ValueError("expected a number, not true/false")
    if isinstance(v, int) and abs(v) > 10**9:
        raise ValueError("number out of range")
    return v


class CaseSite(SiteIn):
    @field_validator("lat", "lon", "mw", mode="before")
    @classmethod
    def _no_huge_ints(cls, v):
        return _finite_number(v)


class StormMeta(BaseModel):
    """Which storm knocked the case's lines out — display only; the lines themselves are `trip`.
    Only a hypothetical preset from hurricane.py is kept (by id; its name comes from the server)."""

    preset: Annotated[str, StringConstraints(pattern=KEY_RE)] | None = None
    name: str | None = None  # ignored: the preset's own name is used


class ScenarioCase(CaseIn):
    """grid.CaseIn plus two display-only labels. Unknown keys are ignored."""

    sites: list[CaseSite] = Field(default_factory=list)
    storm: StormMeta | None = None
    catalog_id: Annotated[str, StringConstraints(pattern=KEY_RE)] | None = None  # a datacenters_us.json entry

    @field_validator("lat", "lon", "mw", "load_factor", mode="before")
    @classmethod
    def _no_huge_ints(cls, v):
        return _finite_number(v)


class ScenarioIn(BaseModel):
    """A new scenario: `case`, or the original body's lat/lon/mw (a Florida campus)."""

    name: Name
    note: Note = ""
    case: ScenarioCase | None = None
    lat: float | None = None
    lon: float | None = None
    mw: float | None = None  # float, not int: check_site turns NaN/huge values into a readable 422

    @field_validator("mw", "lat", "lon", mode="before")
    @classmethod
    def _no_huge_ints(cls, v):
        return _finite_number(v)


class ScenarioUpdate(BaseModel):
    name: Name | None = None
    note: Note | None = None
    case: ScenarioCase | None = None


class VersionIn(BaseModel):
    name: Name | None = None  # default: "<original> · v<n>"
    note: Note | None = None  # default: the source's note
    case: ScenarioCase | None = None  # default: the source's case (a plain copy)


# ------------------------------------------------------------------ helpers
def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat(timespec="seconds") + "Z" if dt else None


def is_example(sc: Scenario) -> bool:
    return SEED_MARK in (sc.note or "")


def _check_note(note: str | None) -> None:
    if note and SEED_MARK in note:
        raise HTTPException(status_code=422, detail=f'A note can\'t contain "{SEED_MARK}" — it marks the built-in examples')


def _catalog_ids() -> set[str]:
    path = FilePath(__file__).parent / "demo" / "datacenters_us.json"
    try:
        return {str(e["id"]) for e in json.loads(path.read_text(encoding="utf-8"))["entries"]}
    except (OSError, ValueError, KeyError, TypeError):
        return set()


def _preset_name(preset_id: str) -> str | None:
    try:
        import hurricane

        return next((p["name"] for p in getattr(hurricane, "PRESETS", []) if p.get("id") == preset_id), None)
    except Exception:  # noqa: BLE001 — no hurricane module, no storm label
        return None


def _legacy_case(sc: Scenario) -> dict:
    # a row saved before the Library: one Florida campus at the summer peak
    return {"region": "FL", "lat": sc.lat, "lon": sc.lon, "mw": float(sc.mw), "sites": [], "load_factor": 1.0, "trip": [], "upgrades": {}, "firm": False}


def case_of(sc: Scenario) -> dict:
    return sc.case_json or _legacy_case(sc)


def normalize_case(c: ScenarioCase) -> dict:
    """Validate a case the way the grid endpoints do (422 with a sentence) and return the JSON to
    store: trip de-duplicated, the region's code upper-case, labels only when given."""
    if c.catalog_id and c.catalog_id not in _catalog_ids():
        raise HTTPException(status_code=422, detail=f"Unknown catalog entry {c.catalog_id!r}")
    _, sites, trip, upgrades = check_case(c)
    if not sites and not trip and round(float(c.load_factor), 2) == 1.0:
        raise HTTPException(status_code=422, detail="Nothing to save yet — add a data center, a storm or a load level first")
    d = {
        "region": region_code(c.region),
        "sites": [{"lat": s.lat, "lon": s.lon, "mw": s.mw} for s in c.sites],
        "load_factor": round(float(c.load_factor), 2),
        "trip": trip,
        "upgrades": {str(k): float(v) for k, v in upgrades.items()},
        "firm": bool(c.firm),
    }
    if c.lat is not None:
        d.update(lat=c.lat, lon=c.lon, mw=c.mw)
    storm = _preset_name(c.storm.preset) if c.storm and c.storm.preset and trip else None
    if storm:  # only the server's own hypothetical presets are named — never a name sent by a client
        d["storm"] = {"preset": c.storm.preset, "name": storm}
    if c.catalog_id:
        d["catalog_id"] = c.catalog_id
    return d


def _to_case(d: dict) -> ScenarioCase:
    return ScenarioCase(**{k: v for k, v in d.items() if k != "upgrades"}, upgrades={int(k): v for k, v in (d.get("upgrades") or {}).items()})


def _main_site(case: dict) -> dict | None:
    """The main campus, else the first extra one (the old list's lat/lon/mw), else None."""
    if case.get("lat") is not None:
        return {"lat": case["lat"], "lon": case["lon"], "mw": case["mw"]}
    return (case.get("sites") or [None])[0]


def _fmt(n: float) -> str:
    return f"{round(n):,}"


def _join(names: list[str]) -> str:
    if len(names) <= 1:
        return "".join(names)
    return ", ".join(names[:-1]) + " and " + names[-1]


def _level(lf: float) -> str:
    pct = round(lf * 100)
    if pct == 100:
        return "at the summer peak"
    if pct > 100:
        return f"in a heat wave ({pct} % of the summer peak)"
    return f"at {pct} % of the summer peak"


WORDS = ["No", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten", "Eleven", "Twelve"]


def sentence(case: dict, header: dict, region_name: str) -> str:
    """The case in one plain sentence, e.g. "A 1,500 MW campus at Fort Myers, Florida, at the summer peak." """
    sites = header.get("sites") or []
    level = _level(float(case.get("load_factor", 1.0)))
    trip = case.get("trip") or []
    lines = f"{_fmt(len(trip))} {'line' if len(trip) == 1 else 'lines'}"
    storm = (case.get("storm") or {}).get("name")
    knock = f"a storm ({storm}) knocks out {lines}" if storm else f"{lines} are knocked out"
    if len(sites) == 1:
        text = f"A {_fmt(sites[0]['mw'])} MW campus at {sites[0]['sub_area']}, {region_name}"
    elif sites:
        areas = list(dict.fromkeys(s["sub_area"] for s in sites))
        where = _join(areas if len(areas) <= 5 else areas[:3] + [f"{len(areas) - 3} more places"])
        n = WORDS[len(sites)] if len(sites) < len(WORDS) else str(len(sites))
        same = len({round(s["mw"]) for s in sites}) == 1
        size = f"{n} {_fmt(sites[0]['mw'])} MW campuses" if same else f"{n} campuses"
        text = f"{size} ({_fmt(sum(s['mw'] for s in sites))} MW in all) at {where}, {region_name}"
    elif trip:  # a storm alone
        text = f"{knock[0].upper()}{knock[1:]} in {region_name} {level}"
        level = None
    else:  # a load level alone
        text = f"{region_name}'s grid {level}"
        level = None
    if sites and trip:
        text += f", while {knock}"
    if level:
        text += f", {level}"
    if case.get("firm") and sites:
        text += ", on firm service"
    n_up = len(case.get("upgrades") or {})
    if n_up:
        text += f", with {n_up} line {'upgrade' if n_up == 1 else 'upgrades'}"
    return text + "."


def compute_result(case: dict) -> dict:
    """What the case does, on the synthetic model: the what-if, then the cascade (< 0.3 s)."""
    c = _to_case(case)
    g, sites, trip, upgrades = check_case(c)
    extra, header = _case_header(g, sites, trip, upgrades)
    active = np.ones(g.m, dtype=bool)
    for bid in trip:
        active[g.br_index[bid]] = False
    state = g.solve(active, extra, g.rates_with(upgrades))
    overloaded = len(g.overloaded(state))
    before = people_fields(g, state.lost_existing_mw)["people"]
    cas = g.cascade_case(extra, trip, upgrades, firm_buses=case_firm_buses(g, sites, c.firm))
    people = int(cas["people"])
    peak = max([before, people] + [int(s.get("people", 0)) for s in cas["steps"]])
    # the areas hit hardest: lost MW summed by the town each substation is named after
    by_area: dict[str, float] = {}
    for sid, mw in cas["affected"].items():
        i = g.sub_index.get(int(sid))
        if i is not None:
            name = area_of(g.sub_name[i])
            by_area[name] = by_area.get(name, 0.0) + float(mw)
    top = sorted(by_area.items(), key=lambda kv: -kv[1])[:AREAS_TOP]
    hit = sum(by_area.values()) or 1.0
    code = region_code(c.region)
    if people > 0 or peak > 0:
        verdict = "blackout"  # people lose power (at the end, or on the way there)
    elif overloaded or cas["total_steps"]:
        verdict = "trips"  # lines go over limit and trip, but every light stays on
    else:
        verdict = "holds"
    return {
        "v": RESULT_V,
        "verdict": verdict,
        "people": people,  # at the end of the cascade (an estimate)
        "people_peak": peak,  # the most at any step (an estimate)
        "steps": int(cas["total_steps"]),
        "overloaded": overloaded,  # lines over limit before the cascade
        "lost_mw": float(cas["lost_mw"]),
        "headroom_mw": header.get("headroom_mw"),  # the main campus's site alone
        "site_cut_off": bool(cas.get("site_cut_off")),
        "firm": bool(c.firm),
        "firm_held": cas.get("firm_held"),
        "shed_mw": float(cas.get("shed_mw") or 0.0),
        # each area's share of the final people number, so the parts never add up to more than it
        "areas_top": [{"area": a, "people": int(round(people * mw / hit)), "lost_mw": round(mw, 1)} for a, mw in top],
        "region": code,
        "region_name": REGIONS[code]["name"],
        "sub_area": header.get("sub_area"),
        "campuses": len(sites),
        "mw_total": float(header["mw"]),
        "load_factor": float(g.load_factor),
        "storm_lines": len(trip),
        "upgrades": len(upgrades),
        "sentence": sentence(case, header, REGIONS[code]["name"]),
        "people_per_mw": round(g.people_per_mw, 2),
        "computed_at": _iso(_now()),
    }


def _summary(result: dict) -> dict:
    # the old list's tooltip ("7 over limit · 217 MW headroom")
    return {"overloaded": result["overloaded"], "headroom_mw": result["headroom_mw"]}


def _apply_case(sc: Scenario, case: dict, result: dict | None = None) -> None:
    """Store a normalized case + its result on the row, and the old columns (main or first campus)."""
    result = result or compute_result(case)
    main = _main_site(case)
    if main:
        sc.lat, sc.lon, sc.mw = float(main["lat"]), float(main["lon"]), int(round(main["mw"]))
    else:  # no campus (a storm or a heat wave alone): the columns can't be NULL; the API says null
        lon0, lat0, lon1, lat1 = REGIONS[case["region"]]["bbox"]
        sc.lat, sc.lon, sc.mw = (lat0 + lat1) / 2, (lon0 + lon1) / 2, 0
    sc.case_json = case
    sc.result = result
    sc.summary = _summary(result)


def serialize(sc: Scenario, version: int | None = None) -> dict:
    # Columns added mid-event arrive as NULL on old rows — always read them with a default.
    case = case_of(sc)
    main = _main_site(case)
    return {
        "id": sc.id,
        "name": sc.name,
        # the main (or first) campus — what the original list replays; null when the case has none
        "lat": main["lat"] if main else None,
        "lon": main["lon"] if main else None,
        "mw": (sc.mw if sc.case_json is None else main["mw"]) if main else None,
        "region": case.get("region", "FL"),
        "note": sc.note or "",
        "summary": sc.summary or None,
        "case": case,
        "result": sc.result if (sc.result or {}).get("v") == RESULT_V else None,
        "parent_id": sc.parent_id,
        "version": version,
        "example": is_example(sc),
        "shared": bool(sc.share_slug),
        "share_slug": sc.share_slug,
        "updated_at": _iso(sc.updated_at),
    }


def _versions_of(rows: list[Scenario]) -> dict[int, int]:
    """id -> version number: an original is 1, its versions 2, 3, … in the order they were made."""
    out: dict[int, int] = {}
    kids: dict[int, list[int]] = {}
    for sc in rows:
        if sc.parent_id is None:
            out[sc.id] = 1
        else:
            kids.setdefault(sc.parent_id, []).append(sc.id)
    for pid, ids in kids.items():
        for n, i in enumerate(sorted(ids), start=2 if pid in out else 1):
            out[i] = n
    return out


def _own(db: Session, user: User, scenario_id: int) -> Scenario:
    # Filtering by owner turns "someone else's scenario" into a plain 404 — no information leak.
    sc = db.query(Scenario).filter(Scenario.id == scenario_id, Scenario.user_id == user.id).first()
    if sc is None:
        raise HTTPException(status_code=404, detail="Not found")
    return sc


def _writable(sc: Scenario) -> None:
    if is_example(sc):
        raise HTTPException(status_code=403, detail="Built-in examples can't be changed — save a version of it instead")


def _room(db: Session, user: User) -> None:
    if db.query(Scenario).filter(Scenario.user_id == user.id).count() >= MAX_SCENARIOS:
        raise HTTPException(status_code=422, detail=f"The library is full ({MAX_SCENARIOS} scenarios) — delete some first")


def _delete(db: Session, sc: Scenario) -> None:
    """Delete a row; its versions stay, the oldest one becomes the original of the rest."""
    kids = db.query(Scenario).filter(Scenario.parent_id == sc.id).order_by(Scenario.id).all()
    if kids:
        kids[0].parent_id = None
        for k in kids[1:]:
            k.parent_id = kids[0].id
    db.delete(sc)


# ------------------------------------------------------------------ routes
@router.get("/api/scenarios")
def list_scenarios(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    rows = db.query(Scenario).filter(Scenario.user_id == user.id).order_by(Scenario.id).all()
    versions = _versions_of(rows)
    return [serialize(sc, versions.get(sc.id)) for sc in rows]


@router.post("/api/scenarios")
@limiter.limit("60/minute")
def create_scenario(request: Request, body: ScenarioIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    _check_note(body.note)
    if body.case is not None:
        case = normalize_case(body.case)
    else:
        if body.lat is None or body.lon is None or body.mw is None:
            raise HTTPException(status_code=422, detail="Send a case, or lat, lon and mw together")
        check_site(body.lat, body.lon, body.mw)  # the original body: a Florida campus
        case = normalize_case(ScenarioCase(lat=body.lat, lon=body.lon, mw=body.mw))
    _room(db, user)
    sc = Scenario(name=body.name, note=body.note, user_id=user.id, updated_at=_now())
    _apply_case(sc, case)
    db.add(sc)
    db.commit()
    db.refresh(sc)
    return serialize(sc, 1)


@router.post("/api/scenarios/examples")
@limiter.limit("30/minute")
def sync_examples(request: Request, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Write the built-in examples into the caller's library: create the missing ones, bring the
    rest back to their canonical case (numbers recomputed), remove retired ones. Idempotent."""
    wanted = examples()
    rows = db.query(Scenario).filter(Scenario.user_id == user.id).order_by(Scenario.id).all()
    mine = [sc for sc in rows if is_example(sc)]
    by_name: dict[str, Scenario] = {}
    removed = []
    for sc in mine:
        if sc.name in wanted and sc.name not in by_name:
            by_name[sc.name] = sc
        else:  # retired, or a duplicate of one kept
            removed.append(sc.name)
            _delete(db, sc)
    created, updated = [], []
    for name, ex in wanted.items():
        case = normalize_case(ScenarioCase(**ex["case"]))
        sc = by_name.get(name)
        if sc is None:
            sc = Scenario(name=name, note=ex["note"], user_id=user.id, updated_at=_now())
            db.add(sc)
            created.append(name)
        elif sc.note != ex["note"] or sc.case_json != case or sc.parent_id is not None:
            sc.updated_at = _now()
            updated.append(name)
        sc.note = ex["note"]
        sc.parent_id = None
        _apply_case(sc, case)
    db.commit()
    rows = db.query(Scenario).filter(Scenario.user_id == user.id).order_by(Scenario.id).all()
    versions = _versions_of(rows)
    return {
        "examples": [serialize(sc, versions.get(sc.id)) for sc in rows if is_example(sc)],
        "created": created,
        "updated": updated,
        "removed": removed,
    }


@router.get("/api/scenarios/{scenario_id}")
def get_scenario(scenario_id: Id, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """One scenario with fresh numbers (re-solved now), plus its original and versions."""
    sc = _own(db, user, scenario_id)
    case = case_of(sc)
    try:
        fresh = compute_result(case)
    except HTTPException:
        fresh = None  # a case the engine no longer accepts: keep what was stored
    except Exception:  # noqa: BLE001 — an engine error must not stop the scenario from opening
        log.exception("library: re-solving scenario %s failed", sc.id)
        fresh = None
    if fresh is not None:
        same = sc.result and {k: v for k, v in sc.result.items() if k != "computed_at"} == {
            k: v for k, v in fresh.items() if k != "computed_at"
        }
        if not same:
            sc.result, sc.summary = fresh, _summary(fresh)
            if sc.case_json is None:
                sc.case_json = case
            db.commit()
            db.refresh(sc)
    root_id = sc.parent_id or sc.id
    family = (
        db.query(Scenario)
        .filter(Scenario.user_id == user.id, (Scenario.id == root_id) | (Scenario.parent_id == root_id))
        .order_by(Scenario.id)
        .all()
    )
    versions = _versions_of(family)
    out = serialize(sc, versions.get(sc.id))
    out["family"] = [serialize(f, versions.get(f.id)) for f in family]  # the original first, then its versions
    return out


@router.put("/api/scenarios/{scenario_id}")
@limiter.limit("60/minute")
def update_scenario(
    request: Request, scenario_id: Id, body: ScenarioUpdate, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    sc = _own(db, user, scenario_id)
    _writable(sc)
    if body.name is None and body.note is None and body.case is None:
        raise HTTPException(status_code=422, detail="Nothing to change — send a name, a note or a case")
    _check_note(body.note)
    if body.case is not None:
        _apply_case(sc, normalize_case(body.case))
    if body.name is not None:
        sc.name = body.name
    if body.note is not None:
        sc.note = body.note
    sc.updated_at = _now()
    db.commit()
    db.refresh(sc)
    return serialize(sc)


@router.delete("/api/scenarios/{scenario_id}")
def delete_scenario(scenario_id: Id, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    sc = _own(db, user, scenario_id)
    _writable(sc)
    _delete(db, sc)
    db.commit()
    return {"ok": True}


@router.post("/api/scenarios/{scenario_id}/version")
@limiter.limit("60/minute")
def make_version(
    request: Request, scenario_id: Id, body: VersionIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    """A new version of a scenario (examples too): the changed case, or a copy. It belongs to the
    original, so the Library shows it nested there and Compare can line them up."""
    src = _own(db, user, scenario_id)
    root = src
    if src.parent_id:  # a version of a version belongs to the same original
        root = db.query(Scenario).filter(Scenario.id == src.parent_id, Scenario.user_id == user.id).first() or src
    _check_note(body.note)
    case = normalize_case(body.case) if body.case is not None else case_of(src)
    n_kids = db.query(Scenario).filter(Scenario.parent_id == root.id).count()
    if n_kids >= MAX_VERSIONS:
        raise HTTPException(status_code=422, detail=f"At most {MAX_VERSIONS} versions of one scenario")
    _room(db, user)
    note = body.note if body.note is not None else (src.note or "").replace(SEED_MARK, "").strip()
    name = body.name or f"{root.name} · v{n_kids + 2}"[:80]
    sc = Scenario(name=name, note=note, user_id=user.id, parent_id=root.id, updated_at=_now())
    _apply_case(sc, case)
    db.add(sc)
    db.commit()
    db.refresh(sc)
    return serialize(sc, n_kids + 2)


@router.post("/api/scenarios/{scenario_id}/share")
@limiter.limit("60/minute")
def share(request: Request, scenario_id: Id, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """A read-only public link. The same slug every time (examples can be shared too)."""
    sc = _own(db, user, scenario_id)
    if not sc.share_slug:
        for _ in range(5):
            slug = secrets.token_urlsafe(9)  # 12 url-safe characters, 72 random bits
            if not db.query(Scenario.id).filter(Scenario.share_slug == slug).first():
                break
        else:  # pragma: no cover — five collisions in 2^72
            raise HTTPException(status_code=503, detail="Could not make a link — try again")
        sc.share_slug = slug
        db.commit()
    return {"slug": sc.share_slug, "path": f"/share/{sc.share_slug}"}


@router.delete("/api/scenarios/{scenario_id}/share")
def unshare(scenario_id: Id, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Turn the public link off (the old link stops working)."""
    sc = _own(db, user, scenario_id)
    _writable(sc)
    sc.share_slug = None
    db.commit()
    return {"ok": True}


@router.get("/api/share/{slug}")
@limiter.limit("120/minute")
def shared(request: Request, slug: Slug, db: Session = Depends(get_db)):
    """Public, read-only: what a share link shows. Never the owner or the row id."""
    if not SLUG_RE.match(slug):
        raise HTTPException(status_code=404, detail="This link doesn't exist or was turned off")
    sc = db.query(Scenario).filter(Scenario.share_slug == slug).first()
    if sc is None:
        raise HTTPException(status_code=404, detail="This link doesn't exist or was turned off")
    s = serialize(sc)
    if s["result"] is None:
        try:
            s["result"] = compute_result(s["case"])
        except Exception:  # noqa: BLE001 — a public page shows the case without numbers rather than a 500
            log.exception("library: solving a shared scenario failed")
            s["result"] = None
    keep = ("name", "note", "region", "case", "result", "example", "updated_at", "lat", "lon", "mw")
    return {**{k: s[k] for k in keep}, "note": s["note"].replace(SEED_MARK, "").strip(), "slug": slug}


# ------------------------------------------------------------------ the built-in examples
# Measured on the synthetic models (scratch/library_probe.py, Sat 03:00): Fort Myers 1,500 MW → 2 over,
# 9 steps, ~784k people (the campus is cut off); 500 MW holds (room 560 MW); 500 MW in the heat wave
# (×1.04) → 14 steps, ~1.04M; the Gulf storm alone → 150 lines out, 6 steps, ~2.1M; five 1 GW campuses
# → 71 over, 30 steps, ~1.06M; Abilene, Texas 1,200 MW → 13 over, 2 steps, ~43k. All estimates.
FMY = {"lat": 26.64, "lon": -81.87}  # the hero site (expected_whatif.json → hero)
BOOM = [(30.3064, -81.666), (28.5603, -81.3734), (27.9404, -82.4302), (26.5765, -81.8802), (25.7995, -80.3041)]  # features/boom PRESET
HEAT_WAVE = 1.04  # features/heat/presets.js


def _storm_case() -> dict | None:
    """The first hypothetical hurricane preset, as a case (hurricane.py); None if presets are gone."""
    try:
        import hurricane

        presets = list(getattr(hurricane, "PRESETS", []) or [])
        # the example's name says "near Fort Myers": use that preset, whatever order the list is in
        p = next((x for x in presets if x.get("id") == "gulf-fort-myers"), None)
        if not p:
            return None
        hits = hurricane.track_hits(p["points"], p["radius_km"])
        return {"trip": hits["trip"], "storm": {"preset": p["id"], "name": p["name"]}}
    except Exception:  # noqa: BLE001 — the example is optional; the rest of the library still seeds
        log.exception("library: the hurricane example could not be built")
        return None


def examples() -> dict[str, dict]:
    """name -> {note, case}, in shelf order. The hero's name is what demo_path.py clicks."""
    out = {
        "Fort Myers · 1,500 MW": {
            "note": f"The hero: a 1,500 MW campus at Fort Myers on a summer afternoon. {SEED_MARK}",
            "case": {**FMY, "mw": 1500},
        },
        "Fort Myers · 500 MW": {
            "note": f"The same spot at a third of the size: the grid holds. {SEED_MARK}",
            "case": {**FMY, "mw": 500},
        },
        "Fort Myers · 500 MW in a heat wave": {
            "note": f"The size that held, on a hotter day (every load at 104 % of the summer peak). {SEED_MARK}",
            "case": {**FMY, "mw": 500, "load_factor": HEAT_WAVE},
        },
    }
    storm = _storm_case()
    if storm:
        out["Gulf storm near Fort Myers"] = {
            "note": f"A hypothetical hurricane track, no data center: the storm alone. {SEED_MARK}",
            "case": storm,
        }
    out["AI boom · five 1 GW campuses"] = {
        "note": f"Five 1,000 MW campuses near Florida's biggest metros at once. {SEED_MARK}",
        "case": {"sites": [{"lat": a, "lon": b, "mw": 1000} for a, b in BOOM]},
    }
    abilene = {"region": "TX", "lat": 32.45, "lon": -99.73, "mw": 1200}
    if "crusoe-abilene" in _catalog_ids():
        abilene["catalog_id"] = "crusoe-abilene"
    out["Abilene, Texas · 1,200 MW"] = {
        "note": (
            "A campus of the size reported for the AI campus at Abilene, tested on a synthetic model of "
            f"the Texas grid: not a prediction about the real project or utility. {SEED_MARK}"
        ),
        "case": abilene,
    }
    return out

