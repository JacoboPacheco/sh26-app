"""Hurricane mode: turn a storm track drawn on the map into the lines it knocks out.

POST /api/hurricane/track takes the storm's path (2–40 [lon, lat] points) and a radius, and
returns every line between two different substations that passes within that radius of the path,
ordered by where along the path the storm first reaches it — the UI flashes them in that order as
the eye passes, then runs the regular cascade with them as the case's `trip` list (grid.py).

Geometry is an equirectangular km approximation around 27.7°N (the same projection the map
uses), vectorized over every branch with numpy: segment-to-segment distances for the hit test,
then a sampled walk along the path for the "first reached" distance. No power flow here.

GET /api/hurricane/presets: a few clearly hypothetical tracks — never named after a real storm.
"""

import math

import numpy as np
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from grid import GRID, LAT_MAX, LAT_MIN, LON_MAX, LON_MIN, MAX_TRIPS
from limiter import limiter

router = APIRouter(tags=["hurricane"])

KM_PER_DEG = 111.19
COS = math.cos(math.radians(27.7))
MIN_POINTS, MAX_POINTS = 2, 40
RADIUS_MIN, RADIUS_MAX, RADIUS_DEFAULT = 10.0, 120.0, 40.0
MIN_TRACK_KM = 1.0
MAX_HITS = MAX_TRIPS  # the cascade accepts at most this many knocked-out lines

# Branch geometry at substation level, computed once. Transformers (both ends in one substation)
# have no length on the map and are left out.
_fs = GRID.bus_sub_idx[GRID.f]
_ts = GRID.bus_sub_idx[GRID.t]
_between = np.flatnonzero(_fs != _ts)
BR_IDS = GRID.br_ids[_between]
BR_KV = GRID.br_kv[_between]
_P0 = np.column_stack([GRID.sub_lon[_fs[_between]] * COS * KM_PER_DEG, GRID.sub_lat[_fs[_between]] * KM_PER_DEG])
_P1 = np.column_stack([GRID.sub_lon[_ts[_between]] * COS * KM_PER_DEG, GRID.sub_lat[_ts[_between]] * KM_PER_DEG])


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


def track_hits(points: list[list[float]], radius_km: float) -> dict:
    """The lines a storm on this path takes out, first-reached first."""
    q = to_km(points)
    seg_len = np.hypot(*(q[1:] - q[:-1]).T)
    cum = np.concatenate([[0.0], np.cumsum(seg_len)])
    total = float(cum[-1])

    # 1. hit test: distance from every line to the whole path (chunked to keep memory flat)
    dist = np.empty(len(BR_IDS))
    for s in range(0, len(BR_IDS), 1024):
        e = min(s + 1024, len(BR_IDS))
        dist[s:e] = seg_seg_dist(_P0[s:e], _P1[s:e], q[:-1], q[1:]).min(axis=1)
    hit = np.flatnonzero(dist <= radius_km)
    capped = len(hit) > MAX_HITS
    if capped:  # too many: keep the ones closest to the eye's path, biggest lines first on ties
        order = np.lexsort((-BR_KV[hit], dist[hit]))
        hit = hit[order[:MAX_HITS]]

    # 2. where along the path the storm first reaches each hit line: walk the path in small steps
    step = min(1.0, radius_km / 10)
    n_samples = max(2, int(math.ceil(total / step)) + 1)
    s_km = np.linspace(0.0, total, n_samples)
    seg = np.clip(np.searchsorted(cum, s_km, side="right") - 1, 0, len(seg_len) - 1)
    with np.errstate(divide="ignore", invalid="ignore"):
        frac = np.where(seg_len[seg] > 0, (s_km - cum[seg]) / seg_len[seg], 0.0)
    samples = q[seg] + frac[:, None] * (q[seg + 1] - q[seg])
    along = np.empty(len(hit))
    for s in range(0, len(hit), 128):
        idx = hit[s : s + 128]
        d = _point_seg_dist(samples[None, :, :], _P0[idx][:, None, :], _P1[idx][:, None, :])  # (h, samples)
        within = d <= radius_km + step  # a sample lands within half a step of the true closest approach
        first = np.where(within.any(axis=1), within.argmax(axis=1), d.argmin(axis=1))
        along[s : s + len(idx)] = s_km[first]

    order = np.lexsort((dist[hit], along))
    hit, along = hit[order], along[order]
    return {
        "trip": [int(b) for b in BR_IDS[hit]],
        "count": int(len(hit)),
        "along_km": [round(float(a), 1) for a in along],
        "total_km": round(total, 1),
        "radius_km": radius_km,
        "capped": capped,
    }


class TrackIn(BaseModel):
    points: list[list[float]]
    radius_km: float = RADIUS_DEFAULT


def check_track(body: TrackIn) -> tuple[list[list[float]], float]:
    """422 with a sentence the UI can show as-is."""
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
    r = body.radius_km
    if not (math.isfinite(r) and RADIUS_MIN <= r <= RADIUS_MAX):
        raise HTTPException(
            status_code=422, detail=f"The storm's radius must be between {RADIUS_MIN:.0f} and {RADIUS_MAX:.0f} km"
        )
    q = to_km(pts)
    if float(np.hypot(*(q[1:] - q[:-1]).T).sum()) < MIN_TRACK_KM:
        raise HTTPException(status_code=422, detail="Drag farther — the storm's path is too short")
    return [[float(lon), float(lat)] for lon, lat in pts], float(r)


@router.post("/api/hurricane/track")
@limiter.limit("60/minute")
def track(request: Request, body: TrackIn):
    points, radius = check_track(body)
    return track_hits(points, radius)


# Hypothetical tracks only — never a real storm's name or path.
# Radii tuned so each lands well under the 400-line cap and leaves the cascade room to spread
# (scratch/hurricane_tune*.py): Fort Myers 150 lines → 6 more steps, Atlantic 161 → 8, Tampa 95 → 14.
PRESETS = [
    {
        "id": "gulf-fort-myers",
        "name": "Gulf landfall near Fort Myers",
        "description": "Comes ashore from the Gulf near Fort Myers and heads northeast across the state.",
        "radius_km": 20,
        "points": [[-82.9, 25.95], [-82.25, 26.35], [-81.85, 26.7], [-81.4, 27.25], [-80.95, 27.85], [-80.5, 28.45]],
    },
    {
        "id": "atlantic-coast",
        "name": "Up the Atlantic coast",
        "description": "Rides north just off the Atlantic coast from Miami toward Jacksonville.",
        "radius_km": 20,
        "points": [[-79.75, 25.6], [-79.8, 26.4], [-80.0, 27.2], [-80.35, 28.0], [-80.7, 28.8], [-81.0, 29.6], [-81.2, 30.4]],
    },
    {
        "id": "tampa-bay",
        "name": "Across Tampa Bay",
        "description": "A tight storm crosses Tampa Bay from the Gulf and heads inland past Lakeland.",
        "radius_km": 10,
        "points": [[-83.2, 27.55], [-82.7, 27.7], [-82.45, 27.8], [-82.1, 27.85], [-81.75, 27.9], [-81.3, 28.0]],
    },
]


@router.get("/api/hurricane/presets")
def presets():
    return {"presets": PRESETS, "radius_km": {"min": RADIUS_MIN, "max": RADIUS_MAX, "default": RADIUS_DEFAULT}}
