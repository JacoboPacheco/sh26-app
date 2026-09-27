"""Hurricane mode: turn a storm track drawn on the map into the lines it knocks out.

POST /api/hurricane/track takes the storm's path (2-40 [lon, lat] points) and a Saffir-Simpson
category (1-5; the panel's control), and returns every line between two different substations that
a simplified wind field takes down, ordered by where along the path the storm first reaches it -- the
UI flashes them in that order as the eye passes, then runs the regular cascade with them as the
case's `trip` list (grid.py).

The wind model (deliberately simple, not a real hurricane forecast tool):
  - a Holland-type radial profile: near-zero at the calm eye, peaks at the category's radius of
    maximum wind (RMAX_KM), decays outward -- so "how hard a line is hit" is its wind at closest
    approach to the track, not just its distance;
  - asymmetric: the right-hand side of the track (the forward-motion side, Northern Hemisphere) is
    stronger than the left, by a fixed multiplier;
  - weakens after an approximate landfall point, exponentially with distance travelled inland;
  - each line fails once that wind exceeds its own fragility threshold: a fixed baseline, tougher for
    higher-kV (taller, sturdier) structures, with a small deterministic jitter seeded by the line's own
    id (numpy, no per-request randomness) so the same storm always knocks out the same lines.

Geometry is an equirectangular km approximation around 27.7 deg N (the same projection the map
uses), vectorized over every branch with numpy: a bounding-box prefilter first (most of the state's
~2,150 branches are nowhere near the track and never enter the distance math), then one pass of
segment-to-segment distances gives both the hit test and -- via argmin -- which path segment is the
closest approach, so "how far along the path" is an analytic projection onto that segment. No
per-line time-stepping and no sampling walk along the path: nothing here scales with the path's
length or holds an array bigger than the (small, prefiltered) candidate set, so a request's memory
stays flat regardless of track length or category.

The three named presets are baked offline (scripts/bake_hurricane.py -> backend/demo/hurricane_bake.json,
fingerprinted against this module's constants and the grid's own branch ids/kv) and served straight from
that file with no live computation; a stale bake (a tuning change, a grid rebuild) is detected and
ignored, falling back to a live solve. A hand-drawn path always computes live -- cheaply, per above.

GET /api/hurricane/presets: a few clearly hypothetical tracks, never named after a real storm.
"""

import hashlib
import json
import math
from pathlib import Path

import numpy as np
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from grid import GRID, LAT_MAX, LAT_MIN, LON_MAX, LON_MIN, MAX_TRIPS
from limiter import limiter

router = APIRouter(tags=["hurricane"])

KM_PER_DEG = 111.19
COS = math.cos(math.radians(27.7))
MIN_POINTS, MAX_POINTS = 2, 40
MIN_TRACK_KM = 1.0
MAX_HITS = MAX_TRIPS  # the cascade accepts at most this many knocked-out lines

# ---------------------------------------------------------------------------------------- the wind model
# A model version: bump it whenever a constant below changes so a bake made under the old numbers is
# recognized as stale (fingerprinted in scripts/bake_hurricane.py) rather than silently served wrong.
MODEL_VERSION = "wind-v1"

CATEGORIES = {
    1: {"label": "Category 1", "vmax_mph": 85, "blurb": "sustained winds near 85 mph -- roofs and trees take damage"},
    2: {"label": "Category 2", "vmax_mph": 100, "blurb": "sustained winds near 100 mph -- extensive roof and tree damage, power loss"},
    3: {"label": "Category 3", "vmax_mph": 120, "blurb": "major hurricane -- sustained winds near 120 mph, structural damage likely"},
    4: {"label": "Category 4", "vmax_mph": 145, "blurb": "sustained winds near 145 mph -- severe structural damage, long outages"},
    5: {"label": "Category 5", "vmax_mph": 165, "blurb": "sustained winds near 165 mph -- catastrophic, most structures fail"},
}
CATEGORY_DEFAULT = 3
RMAX_KM = {1: 55.0, 2: 50.0, 3: 44.0, 4: 38.0, 5: 32.0}  # radius of maximum wind: shrinks as storms tighten
CATEGORY_REACH_KM = {1: 62.0, 2: 72.0, 3: 82.0, 4: 92.0, 5: 102.0}  # beyond this, wind is negligible
HOLLAND_B = 1.3
RIGHT_MULT, LEFT_MULT = 1.18, 0.84  # the forward-motion (right, Northern Hemisphere) side is stronger
LANDFALL_HALF_KM = 160.0  # wind halves for every this many km travelled past landfall
LANDFALL_FRAC_DEFAULT = 0.15  # a hand-drawn path's landfall, absent other information: an early guess

FRAGILITY_BASE_MPH = 70.0
FRAGILITY_JITTER_MPH = 14.0  # +/- range, deterministic per line id -- never random per request

RADIUS_MIN, RADIUS_MAX = 20.0, 150.0
RADIUS_DEFAULT = CATEGORY_REACH_KM[CATEGORY_DEFAULT]

# Branch geometry at substation level, computed once. Transformers (both ends in one substation)
# have no length on the map and are left out.
_fs = GRID.bus_sub_idx[GRID.f]
_ts = GRID.bus_sub_idx[GRID.t]
_between = np.flatnonzero(_fs != _ts)
BR_IDS = GRID.br_ids[_between]
BR_KV = GRID.br_kv[_between]
_P0 = np.column_stack([GRID.sub_lon[_fs[_between]] * COS * KM_PER_DEG, GRID.sub_lat[_fs[_between]] * KM_PER_DEG])
_P1 = np.column_stack([GRID.sub_lon[_ts[_between]] * COS * KM_PER_DEG, GRID.sub_lat[_ts[_between]] * KM_PER_DEG])
_BX0, _BX1 = np.minimum(_P0[:, 0], _P1[:, 0]), np.maximum(_P0[:, 0], _P1[:, 0])
_BY0, _BY1 = np.minimum(_P0[:, 1], _P1[:, 1]), np.maximum(_P0[:, 1], _P1[:, 1])

# Each line's own wind-fragility threshold (mph): a fixed baseline, tougher for higher kV, with a
# small jitter seeded by the line's own id -- computed once here, not per request, and always the
# same for a given line (numpy only, no RNG object, no per-request state).
_KV_BREAKS = np.array([100.0, 138.0, 345.0, 765.0])
_KV_BONUS_MPH = np.array([0.0, 6.0, 18.0, 32.0])
_kv_bonus = np.interp(BR_KV, _KV_BREAKS, _KV_BONUS_MPH)
_h = (BR_IDS.astype(np.int64) * np.int64(2654435761)) & np.int64(0xFFFFFFFF)
_jitter = (_h % np.int64(2 * FRAGILITY_JITTER_MPH + 1)).astype(float) - FRAGILITY_JITTER_MPH
FRAGILITY_MPH = FRAGILITY_BASE_MPH + _kv_bonus + _jitter


def to_km(points: list[list[float]]) -> np.ndarray:
    """[[lon, lat], ...] -> (n, 2) km on the equirectangular plane."""
    a = np.asarray(points, dtype=float)
    return np.column_stack([a[:, 0] * COS * KM_PER_DEG, a[:, 1] * KM_PER_DEG])


def _point_seg_dist(p: np.ndarray, a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Distance from points p to segments a->b; all broadcast over leading axes, last axis = 2."""
    ab = b - a
    denom = np.einsum("...i,...i->...", ab, ab)
    with np.errstate(divide="ignore", invalid="ignore"):
        t = np.where(denom > 1e-12, np.einsum("...i,...i->...", p - a, ab) / denom, 0.0)
    t = np.clip(t, 0.0, 1.0)
    closest = a + t[..., None] * ab
    return np.hypot(*np.moveaxis(p - closest, -1, 0))


def _cross(o: np.ndarray, a: np.ndarray, b: np.ndarray) -> np.ndarray:
    return (a[..., 0] - o[..., 0]) * (b[..., 1] - o[..., 1]) - (a[..., 1] - o[..., 1]) * (b[..., 0] - o[..., 0])


def seg_seg_dist(p0: np.ndarray, p1: np.ndarray, q0: np.ndarray, q1: np.ndarray) -> np.ndarray:
    """Distance between segments p0->p1 (M, 2) and q0->q1 (K, 2), shape (M, K)."""
    P0, P1 = p0[:, None, :], p1[:, None, :]
    Q0, Q1 = q0[None, :, :], q1[None, :, :]
    d = np.minimum.reduce(
        [
            _point_seg_dist(P0, Q0, Q1),
            _point_seg_dist(P1, Q0, Q1),
            _point_seg_dist(Q0, P0, P1),
            _point_seg_dist(Q1, P0, P1),
        ]
    )
    # a proper crossing has distance 0 though no endpoint is close
    d1, d2 = _cross(Q0, Q1, P0), _cross(Q0, Q1, P1)
    d3, d4 = _cross(P0, P1, Q0), _cross(P0, P1, Q1)
    crosses = (d1 * d2 < 0) & (d3 * d4 < 0)
    return np.where(crosses, 0.0, d)


def track_hits(points: list[list[float]], radius_km: float, category: int | None = None, landfall_km: float | None = None) -> dict:
    """The lines a storm on this path takes out, first-reached first. `category` and `landfall_km`
    default the same way the HTTP endpoint's TrackIn does (category 3, an early heuristic landfall),
    so a bare `track_hits(points, radius_km)` call (harden.py, older callers) agrees with a POST that
    gives the same points and radius_km and no category of its own."""
    if category is None:
        category = CATEGORY_DEFAULT
    vmax = CATEGORIES[category]["vmax_mph"]
    rmax = RMAX_KM[category]

    q = to_km(points)
    seg_len = np.hypot(*(q[1:] - q[:-1]).T)
    cum = np.concatenate([[0.0], np.cumsum(seg_len)])
    total = float(cum[-1])
    lf_km = landfall_km if landfall_km is not None else LANDFALL_FRAC_DEFAULT * total

    # 0. bounding-box prefilter: most branches are nowhere near the track and skip the distance math entirely
    pad = radius_km
    tx0, tx1 = float(q[:, 0].min()) - pad, float(q[:, 0].max()) + pad
    ty0, ty1 = float(q[:, 1].min()) - pad, float(q[:, 1].max()) + pad
    cand = np.flatnonzero((_BX1 >= tx0) & (_BX0 <= tx1) & (_BY1 >= ty0) & (_BY0 <= ty1))

    # 1. hit test + which path segment is closest, one pass (chunked to keep memory flat), analytic --
    # no sampling walk along the path
    dist = np.empty(len(cand))
    seg_idx = np.empty(len(cand), dtype=np.int64)
    for s in range(0, len(cand), 1024):
        e = min(s + 1024, len(cand))
        idx = cand[s:e]
        D = seg_seg_dist(_P0[idx], _P1[idx], q[:-1], q[1:])  # (chunk, K)
        j = D.argmin(axis=1)
        dist[s:e] = D[np.arange(e - s), j]
        seg_idx[s:e] = j
    near_local = np.flatnonzero(dist <= radius_km)
    near, d_near, seg_near = cand[near_local], dist[near_local], seg_idx[near_local]

    if len(near):
        # 2. where along the path: project the branch's midpoint onto its closest path segment (analytic)
        mid = 0.5 * (_P0[near] + _P1[near])
        a, b = q[seg_near], q[seg_near + 1]
        ab = b - a
        denom = np.einsum("ij,ij->i", ab, ab)
        with np.errstate(divide="ignore", invalid="ignore"):
            t = np.where(denom > 1e-12, np.einsum("ij,ij->i", mid - a, ab) / denom, 0.0)
        t = np.clip(t, 0.0, 1.0)
        along_near = cum[seg_near] + t * seg_len[seg_near]

        # 3. which side of the track: the forward-motion (right) side runs stronger, Northern Hemisphere
        cross = ab[:, 0] * (mid - a)[:, 1] - ab[:, 1] * (mid - a)[:, 0]
        right = cross < 0

        # 4. a simplified Holland wind profile at each line's closest approach (its peak wind along the
        # pass), weakened past landfall and boosted on the right-hand side
        r = np.maximum(d_near, 0.5)
        ratio = (rmax / r) ** HOLLAND_B
        sym = vmax * np.sqrt(np.clip(ratio * np.exp(1.0 - ratio), 0.0, None))
        weaken = np.where(along_near > lf_km, 0.5 ** ((along_near - lf_km) / LANDFALL_HALF_KM), 1.0)
        wind = sym * weaken * np.where(right, RIGHT_MULT, LEFT_MULT)

        fail_local = np.flatnonzero(wind >= FRAGILITY_MPH[near])
        capped = len(fail_local) > MAX_HITS
        if capped:  # keep the widest margin over threshold first, biggest lines on ties
            margin = wind[fail_local] - FRAGILITY_MPH[near[fail_local]]
            order = np.lexsort((-BR_KV[near[fail_local]], -margin))
            fail_local = fail_local[order[:MAX_HITS]]
        hit, along_hit, d_hit = near[fail_local], along_near[fail_local], d_near[fail_local]
        order = np.lexsort((d_hit, along_hit))
        hit, along_hit = hit[order], along_hit[order]
    else:
        hit, along_hit, capped = np.array([], dtype=int), np.array([]), False

    return {
        "trip": [int(b) for b in BR_IDS[hit]],
        "count": int(len(hit)),
        "along_km": [round(float(a), 1) for a in along_hit],
        "total_km": round(total, 1),
        "radius_km": round(float(radius_km), 1),
        "capped": capped,
        "category": int(category),
        "vmax_mph": vmax,
    }


class TrackIn(BaseModel):
    points: list[list[float]]
    category: int = CATEGORY_DEFAULT
    radius_km: float | None = None  # an explicit override (km); omitted, it's derived from category
    landfall_km: float | None = None  # an explicit override; omitted, an early heuristic fraction of the path


def check_track(body: TrackIn) -> tuple[list[list[float]], float, int]:
    """422 with a sentence the UI can show as-is. Returns (points, radius_km, category)."""
    pts = body.points
    if len(pts) < MIN_POINTS:
        raise HTTPException(status_code=422, detail="Draw a longer path — the storm needs at least two points")
    if len(pts) > MAX_POINTS:
        raise HTTPException(status_code=422, detail=f"A storm's path can have at most {MAX_POINTS} points")
    for p in pts:
        if len(p) != 2 or not all(math.isfinite(v) for v in p):
            raise HTTPException(status_code=422, detail="Each point of the path must be [longitude, latitude]")
        lon, lat = p
        if not (LON_MIN <= lon <= LON_MAX and LAT_MIN <= lat <= LAT_MAX):
            raise HTTPException(status_code=422, detail="The storm's path must stay on the map of Florida")
    category = body.category
    if not (isinstance(category, int) and category in CATEGORIES):
        raise HTTPException(status_code=422, detail="The storm's category must be 1 to 5")
    r = body.radius_km if body.radius_km is not None else CATEGORY_REACH_KM[category]
    if not (math.isfinite(r) and RADIUS_MIN <= r <= RADIUS_MAX):
        raise HTTPException(
            status_code=422, detail=f"The storm's radius must be between {RADIUS_MIN:.0f} and {RADIUS_MAX:.0f} km"
        )
    q = to_km(pts)
    if float(np.hypot(*(q[1:] - q[:-1]).T).sum()) < MIN_TRACK_KM:
        raise HTTPException(status_code=422, detail="Drag farther — the storm's path is too short")
    return [[float(lon), float(lat)] for lon, lat in pts], float(r), category


@router.post("/api/hurricane/track")
@limiter.limit("60/minute")
def track(request: Request, body: TrackIn):
    points, radius, category = check_track(body)
    return track_hits(points, radius, category, body.landfall_km)


# Hypothetical tracks only — never a real storm's name or path. Each is baked offline
# (scripts/bake_hurricane.py) into backend/demo/hurricane_bake.json; a stale bake (this module's
# constants or the grid's branches changed since) is ignored and the request runs live instead.
# landfall_km: an approximate, hand-placed point along each path (Fort Myers and Tampa Bay come
# ashore quickly; the Atlantic track runs just offshore the whole way and never really weakens).
PRESETS = [
    {
        "id": "gulf-fort-myers",
        "name": "Gulf landfall near Fort Myers",
        "description": "A major hurricane comes ashore from the Gulf near Fort Myers and heads northeast across the state.",
        "category": 5,
        "landfall_km": 65.0,
        "radius_km": 120.0,
        "points": [[-82.9, 25.95], [-82.25, 26.35], [-81.85, 26.7], [-81.4, 27.25], [-80.95, 27.85], [-80.5, 28.45]],
    },
    {
        "id": "atlantic-coast",
        "name": "Up the Atlantic coast",
        "description": "Rides north just off the Atlantic coast from Miami toward Jacksonville, its eyewall grazing the shore.",
        "category": 2,
        "landfall_km": 10_000.0,  # never truly makes landfall on this track: stays offshore the whole way
        "radius_km": 40.0,  # a long, dense coastline: a tighter reach keeps this in scale with the other two
        "points": [[-79.75, 25.6], [-79.8, 26.4], [-80.0, 27.2], [-80.35, 28.0], [-80.7, 28.8], [-81.0, 29.6], [-81.2, 30.4]],
    },
    {
        "id": "tampa-bay",
        "name": "Across Tampa Bay",
        "description": "A tight, powerful storm crosses Tampa Bay from the Gulf and heads inland past Lakeland.",
        "category": 3,
        "landfall_km": 20.0,
        "radius_km": 50.0,
        "points": [[-83.2, 27.55], [-82.7, 27.7], [-82.45, 27.8], [-82.1, 27.85], [-81.75, 27.9], [-81.3, 28.0]],
    },
]


# ---------------------------------------------------------------------------------------- the baked presets
_BAKE_PATH = Path(__file__).parent / "demo" / "hurricane_bake.json"


def _fingerprint(preset: dict) -> str:
    """Everything a bake depends on: this model's version, the preset's own inputs, and the grid's
    branches (so a rebuilt grid invalidates old bakes instead of serving stale hits)."""
    h = hashlib.sha256()
    h.update(MODEL_VERSION.encode())
    h.update(json.dumps({"points": preset["points"], "category": preset["category"], "landfall_km": preset["landfall_km"],
                          "radius_km": preset.get("radius_km")}, sort_keys=True).encode())
    h.update(BR_IDS.tobytes())
    h.update(BR_KV.tobytes())
    return h.hexdigest()


def _load_bake() -> dict:
    """id -> baked track_hits() result, only for presets whose fingerprint still matches. Loaded once
    at import; a stale or missing file just means those presets compute live on first request."""
    out = {}
    try:
        raw = json.loads(_BAKE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return out
    by_id = {p["id"]: p for p in PRESETS}
    for pid, entry in raw.items():
        p = by_id.get(pid)
        if p and entry.get("fingerprint") == _fingerprint(p):
            out[pid] = entry["hits"]
    return out


_BAKED = _load_bake()


@router.get("/api/hurricane/presets")
def presets():
    out = []
    for p in PRESETS:
        row = {"id": p["id"], "name": p["name"], "description": p["description"], "category": p["category"],
               "points": p["points"], "radius_km": p.get("radius_km", CATEGORY_REACH_KM[p["category"]]),
               "landfall_km": p["landfall_km"]}
        hits = _BAKED.get(p["id"])
        if hits is not None:
            row["hits"] = hits  # baked: the frontend can make landfall with no /track round-trip at all
        out.append(row)
    return {"presets": out, "categories": CATEGORIES, "category_default": CATEGORY_DEFAULT,
            "radius_km": {"min": RADIUS_MIN, "max": RADIUS_MAX, "default": RADIUS_DEFAULT}}
