"""
Quick pass/fail check against a running backend (default http://localhost:8000).
Run this after starting the server to confirm health, auth, and upload
validation all actually work — don't just eyeball it.

Usage:
  venv/Scripts/python smoke_test.py                              # local server on :8000
  venv/Scripts/python smoke_test.py https://yourapp.onrender.com                          # the deployed backend
  venv/Scripts/python smoke_test.py https://yourapp.onrender.com https://yourapp.vercel.app # + CORS and build-URL checks
  (SMOKE_BASE_URL / SMOKE_ORIGIN env vars also work)
"""

import base64
import json
import math
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

# smallest possible valid PNG (1x1 transparent pixel)
TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)

BASE = (sys.argv[1] if len(sys.argv) > 1 else os.getenv("SMOKE_BASE_URL", "http://localhost:8000")).rstrip("/")
# Second argument (or SMOKE_ORIGIN): the deployed frontend's origin, e.g. https://yourapp.vercel.app.
# When given, also verifies CORS and that the deployed frontend was built with THIS backend's URL.
ORIGIN = (sys.argv[2] if len(sys.argv) > 2 else os.getenv("SMOKE_ORIGIN", "")).rstrip("/") or None
TIMEOUT = 30
failures = []


def check(name, fn):
    try:
        fn()
        print(f"PASS  {name}")
    except Exception as e:
        print(f"FAIL  {name}: {e}")
        failures.append(name)


def send(req, expect):
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            status, raw, hdrs = resp.status, resp.read(), resp.headers
    except urllib.error.HTTPError as e:
        status, raw, hdrs = e.code, e.read(), e.headers
    except (urllib.error.URLError, OSError) as e:
        raise AssertionError(f"could not reach {BASE}: {e}")
    try:
        payload = json.loads(raw) if raw else None
    except ValueError:
        payload = raw[:200].decode(errors="replace")
    if status != expect:
        raise AssertionError(f"expected {expect}, got {status}: {payload}")
    return payload, hdrs


def request(method, path, data=None, headers=None, expect=200):
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(BASE + path, data=body, method=method, headers=headers or {})
    if data is not None:
        req.add_header("Content-Type", "application/json")
    payload, _ = send(req, expect)
    return payload


def upload(filename, content_type, data_bytes, token=None, expect=200):
    boundary = uuid.uuid4().hex
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: {content_type}\r\n\r\n"
    ).encode() + data_bytes + f"\r\n--{boundary}--\r\n".encode()
    headers = {"Content-Type": f"multipart/form-data; boundary={boundary}"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(BASE + "/api/upload", data=body, method="POST", headers=headers)
    payload, _ = send(req, expect)
    return payload


email = f"smoketest-{uuid.uuid4().hex[:8]}@example.com"
token = {}

# One reachability probe up front, so a down server fails in seconds, not once per check.
try:
    urllib.request.urlopen(urllib.request.Request(BASE + "/api/health"), timeout=TIMEOUT).read()
except urllib.error.HTTPError:
    pass  # it answered; the health check below will judge the status
except (urllib.error.URLError, OSError) as e:
    print(f"FAIL  backend at {BASE} is not reachable: {e}")
    sys.exit(1)


def test_health():
    payload = request("GET", "/api/health")
    assert payload == {"status": "ok"}


def test_cors_for_frontend_origin():
    if not ORIGIN:
        return  # only meaningful against a deployed backend; set SMOKE_ORIGIN to enable
    req = urllib.request.Request(BASE + "/api/health", headers={"Origin": ORIGIN})
    _, hdrs = send(req, 200)
    allowed = hdrs.get("access-control-allow-origin")
    assert allowed == ORIGIN, f"CORS: expected Access-Control-Allow-Origin {ORIGIN}, got {allowed!r} — fix ALLOWED_ORIGINS on the backend"


def fetch_text(url):
    with urllib.request.urlopen(urllib.request.Request(url), timeout=TIMEOUT) as resp:
        return resp.read().decode(errors="replace")


def test_frontend_points_at_this_backend():
    if not ORIGIN:
        return
    # the built bundle must contain this backend's URL, i.e. VITE_API_URL was set at build time
    html = fetch_text(ORIGIN + "/")
    scripts = re.findall(r'<script[^>]+src="([^"]+\.js)"', html)
    assert scripts, f"no <script> tags found at {ORIGIN} — is that the frontend?"
    bundle = "".join(fetch_text(urllib.parse.urljoin(ORIGIN + "/", s)) for s in scripts)
    if "VITE_API_URL is not set" in bundle and BASE not in bundle:
        raise AssertionError(f"the frontend at {ORIGIN} was built WITHOUT VITE_API_URL — set it in Vercel to {BASE} and redeploy")
    assert BASE in bundle, f"the frontend at {ORIGIN} was built for a different backend (expected {BASE} in its bundle)"


def test_signup():
    payload = request("POST", "/api/auth/signup", {"email": email, "password": "smoketest123"})
    assert "access_token" in payload
    token["value"] = payload["access_token"]


def test_me_authenticated():
    payload = request("GET", "/api/auth/me", headers={"Authorization": f"Bearer {token['value']}"})
    assert payload["email"] == email


def test_me_unauthenticated():
    request("GET", "/api/auth/me", expect=401)


def test_signup_duplicate_rejected():
    request("POST", "/api/auth/signup", {"email": email, "password": "smoketest123"}, expect=400)


def test_signup_rejects_short_password():
    request("POST", "/api/auth/signup", {"email": f"x{email}", "password": "short"}, expect=422)


def test_signup_rejects_bad_email():
    request("POST", "/api/auth/signup", {"email": "not-an-email", "password": "longenough123"}, expect=422)


def test_login_email_case_insensitive():
    body = urllib.parse.urlencode({"username": f"  {email.upper()} ", "password": "smoketest123"}).encode()
    req = urllib.request.Request(BASE + "/api/auth/login", data=body, method="POST")
    with urllib.request.urlopen(req) as resp:
        assert "access_token" in json.loads(resp.read())


def test_upload_requires_auth():
    upload("test.png", "image/png", TINY_PNG, expect=401)


def test_upload_valid_image():
    payload = upload("test.png", "image/png", TINY_PNG, token=token["value"])
    assert payload["filename"].endswith(".png")
    # the file must be served back with the right type and identical bytes
    req = urllib.request.Request(BASE + payload["url"])
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        assert resp.headers.get("content-type", "").startswith("image/png"), resp.headers.get("content-type")
        assert resp.read() == TINY_PNG, "served bytes differ from the upload"


def test_upload_wrong_type_rejected():
    upload("test.txt", "text/plain", b"hello world", token=token["value"], expect=400)


check("health check", test_health)
check("CORS allows the frontend origin (when SMOKE_ORIGIN is set)", test_cors_for_frontend_origin)
check("deployed frontend was built for this backend (when SMOKE_ORIGIN is set)", test_frontend_points_at_this_backend)
check("signup returns token", test_signup)
check("authenticated /me returns correct user", test_me_authenticated)
check("unauthenticated /me is rejected", test_me_unauthenticated)
check("duplicate signup is rejected", test_signup_duplicate_rejected)
check("short password is rejected", test_signup_rejects_short_password)
check("malformed email is rejected", test_signup_rejects_bad_email)
check("login ignores email case/whitespace", test_login_email_case_insensitive)
check("upload requires auth", test_upload_requires_auth)
check("valid image upload accepted", test_upload_valid_image)
check("non-image upload rejected", test_upload_wrong_type_rejected)


def test_ai_status():
    payload = request("GET", "/api/ai/status")
    assert isinstance(payload["configured"], bool)
    return payload["configured"]


def test_ai_ask_requires_auth():
    request("POST", "/api/ai/ask", {"prompt": "hi"}, expect=401)


def test_ai_ask_path():
    configured = request("GET", "/api/ai/status")["configured"]
    payload = request(
        "POST", "/api/ai/ask", {"prompt": "Reply with the single word OK."},
        headers={"Authorization": f"Bearer {token['value']}"},
        expect=200 if configured else 503,
    )
    if configured:
        assert payload["text"].strip()
    else:
        assert "GEMINI_API_KEY" in payload["detail"]


check("ai status reports configured flag", test_ai_status)
check("ai ask requires auth", test_ai_ask_requires_auth)
check("ai ask works, or says clearly it's not configured", test_ai_ask_path)


# Grid (grid.py) — public, read-only; checked against the committed expected what-if that
# backend/demo/validate.py wrote. Nothing here creates data.
EXPECTED = json.loads((Path(__file__).parent / "demo" / "expected_whatif.json").read_text(encoding="utf-8"))
ORLANDO = {"lat": EXPECTED["lat"], "lon": EXPECTED["lon"], "mw": EXPECTED["mw"]}
grid = {}


def test_grid_loads():
    g = request("GET", "/api/grid")
    grid.update(g)
    meta = g["meta"]
    assert meta["synthetic"] is True, meta
    assert meta["bus_count"] >= 1000 and meta["branch_count"] >= 1000, meta
    assert len(g["branches"]) == meta["branch_count"], "branch list and count disagree"
    bad = [s for s in g["subs"] if not (24.3 <= s["lat"] <= 31.1 and -87.7 <= s["lon"] <= -79.4)]
    assert not bad, f"{len(bad)} substations outside Florida's box, e.g. {bad[0]}"
    assert all(b["rate_mva"] > 0 for b in g["branches"]), "a branch has no rating"


def test_whatif_matches_expected():
    w = request("POST", "/api/grid/whatif", ORLANDO)
    assert w["sub"] == EXPECTED["sub"], f"snapped to sub {w['sub']}, expected {EXPECTED['sub']}"
    got = {o["id"] for o in w["overloaded"]}
    assert got == set(EXPECTED["overloaded_ids"]), f"overloaded {sorted(got)} != expected {sorted(EXPECTED['overloaded_ids'])}"
    assert abs(w["headroom_mw"] - EXPECTED["headroom_mw"]) <= 1, f"headroom {w['headroom_mw']} vs {EXPECTED['headroom_mw']}"
    assert len(w["loading_pct"]) == len(grid["branches"]), "loading_pct not aligned with /api/grid branches"
    grid["orlando_headroom"] = w["headroom_mw"]


def test_whatif_validation():
    request("POST", "/api/grid/whatif", {"lat": 40.7, "lon": -74.0, "mw": 500}, expect=422)  # New York
    request("POST", "/api/grid/whatif", {**ORLANDO, "mw": 0}, expect=422)
    request("POST", "/api/grid/whatif", {**ORLANDO, "mw": 9999}, expect=422)
    request("POST", "/api/grid/whatif", {"lat": "x", "lon": -81, "mw": 500}, expect=422)


def test_cascade_orlando():
    c = request("POST", "/api/grid/cascade", ORLANDO)
    assert c["outcome"] in ("settled", "islanded"), c["outcome"]
    assert c["total_steps"] <= 30 and len(c["steps"]) <= 30, c["total_steps"]
    total_load = sum(s["load_mw"] for s in grid["subs"])
    assert all(s["lost_mw"] <= total_load for s in c["steps"]), "lost more load than Florida has"
    homes = [s["homes"] for s in c["steps"]]
    assert homes == sorted(homes), f"homes not monotone: {homes}"
    assert c["total_steps"] == EXPECTED["cascade_steps"] and c["outcome"] == EXPECTED["cascade_outcome"], (
        f"{c['total_steps']} steps -> {c['outcome']}, expected {EXPECTED['cascade_steps']} -> {EXPECTED['cascade_outcome']}"
    )


def test_headroom():
    by_sub = request("GET", "/api/grid/headroom")["by_sub"]
    assert len(by_sub) == len(grid["subs"]), f"{len(by_sub)} values for {len(grid['subs'])} substations"
    assert all(math.isfinite(v) and v >= 0 for v in by_sub.values()), "a headroom value is negative or not finite"
    orl = by_sub[str(EXPECTED["sub"])]
    assert abs(orl - grid["orlando_headroom"]) <= 1, f"heatmap says {orl} MW, what-if says {grid['orlando_headroom']} MW"


def test_hero_site():
    # the demo's story (CLAUDE.md → Decisions): the hero site cascades at its big size, is calm at the small one
    h = EXPECTED["hero"]
    site = {"lat": h["lat"], "lon": h["lon"]}
    c = request("POST", "/api/grid/cascade", {**site, "mw": h["mw"]})
    dark = sum(len(s["dark_subs"]) for s in c["steps"])
    assert (c["total_steps"], dark) == (h["cascade_steps"], h["dark_subs"]), (
        f"{c['total_steps']} steps / {dark} dark, expected {h['cascade_steps']} / {h['dark_subs']}"
    )
    calm = request("POST", "/api/grid/whatif", {**site, "mw": h["calm_mw"]})
    assert not calm["overloaded"], f"{len(calm['overloaded'])} over limit at {h['calm_mw']} MW, expected none"


check("grid loads: synthetic, >=1000 buses and branches, inside Florida, rated", test_grid_loads)
check("hero site: cascades at its big size, calm at the small one (expected_whatif.json)", test_hero_site)
check("what-if on the Orlando site matches expected_whatif.json", test_whatif_matches_expected)
check("what-if rejects a point outside Florida and a size of 0 or 9999 MW", test_whatif_validation)
check("cascade on the Orlando site terminates, homes monotone, matches expected", test_cascade_orlando)
check("headroom: one finite value per substation, agrees with the what-if", test_headroom)


# Scenarios (scenarios.py) — every check acts as this run's own throwaway user and deletes
# what it created, so it's safe against the deployed backend too.
auth_headers = {}
scenario = {}


def test_scenarios_require_auth():
    request("GET", "/api/scenarios", expect=401)
    request("POST", "/api/scenarios", {"name": "x", **ORLANDO}, expect=401)


def test_scenario_create_and_list():
    auth_headers["value"] = {"Authorization": f"Bearer {token['value']}"}
    created = request("POST", "/api/scenarios", {"name": "Smoke scenario", **ORLANDO}, headers=auth_headers["value"])
    assert created["name"] == "Smoke scenario" and created["mw"] == ORLANDO["mw"], created
    assert created["summary"]["overloaded"] == len(EXPECTED["overloaded_ids"]), created["summary"]
    scenario["id"] = created["id"]
    listed = request("GET", "/api/scenarios", headers=auth_headers["value"])
    assert any(s["id"] == scenario["id"] for s in listed), "created scenario missing from list"


def test_scenario_validation():
    h = auth_headers["value"]
    request("POST", "/api/scenarios", {"name": "", **ORLANDO}, headers=h, expect=422)
    request("POST", "/api/scenarios", {"name": "   ", **ORLANDO}, headers=h, expect=422)
    request("POST", "/api/scenarios", {"name": "x" * 81, **ORLANDO}, headers=h, expect=422)
    request("POST", "/api/scenarios", {"name": "NYC", "lat": 40.7, "lon": -74.0, "mw": 500}, headers=h, expect=422)
    request("POST", "/api/scenarios", {"name": "Too big", **ORLANDO, "mw": 9999}, headers=h, expect=422)
    # malformed numbers are a 422, never a 500
    request("POST", "/api/scenarios", {"name": "Huge", **ORLANDO, "mw": 10**400}, headers=h, expect=422)
    request("POST", "/api/scenarios", {"name": "NaN", **ORLANDO, "mw": float("nan")}, headers=h, expect=422)
    request("DELETE", f"/api/scenarios/{10**30}", headers=h, expect=422)


def test_scenario_owner_only():
    # a second throwaway user must not see or delete the first user's scenario
    other = request("POST", "/api/auth/signup", {"email": f"other-{email}", "password": "smoketest123"})
    other_headers = {"Authorization": f"Bearer {other['access_token']}"}
    request("DELETE", f"/api/scenarios/{scenario['id']}", headers=other_headers, expect=404)
    assert not any(s["id"] == scenario["id"] for s in request("GET", "/api/scenarios", headers=other_headers)), "leaked"


def test_scenario_delete():
    request("DELETE", f"/api/scenarios/{scenario['id']}", headers=auth_headers["value"])
    request("DELETE", f"/api/scenarios/{scenario['id']}", headers=auth_headers["value"], expect=404)
    listed = request("GET", "/api/scenarios", headers=auth_headers["value"])
    assert not any(s["id"] == scenario["id"] for s in listed), "deleted scenario still listed"


check("scenarios require auth", test_scenarios_require_auth)
check("scenario create + list roundtrip", test_scenario_create_and_list)
check("scenario validation rejects blank/long names, points outside Florida, bad sizes", test_scenario_validation)
check("scenarios are owner-only", test_scenario_owner_only)
check("scenario delete works", test_scenario_delete)

if failures:
    print(f"\n{len(failures)} check(s) failed: {', '.join(failures)}")
    sys.exit(1)
print("\nAll checks passed.")
