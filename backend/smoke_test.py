"""
Quick pass/fail check against a running backend (default http://localhost:8000).
Run this after starting the server to confirm health, auth, the removed template routes
(upload, saved scenarios, share links: 404) and every feature actually work — don't just eyeball it.

Sign-up is open on a local backend (the checks act as a throwaway user) and off on a deployed one
(Render sets RENDER; see ALLOW_SIGNUP in backend/.env.example): against a deployed backend the
script expects sign-up to answer 403 and skips the checks that need a signed-in throwaway user.
SMOKE_SIGNUP_OPEN=1 tells it a deployed backend has sign-up on (a one-off seed).

Usage:
  venv/Scripts/python smoke_test.py                              # local server on :8000
  venv/Scripts/python smoke_test.py https://yourapp.onrender.com                          # the deployed backend
  venv/Scripts/python smoke_test.py https://yourapp.onrender.com https://yourapp.vercel.app # + CORS and build-URL checks
  (SMOKE_BASE_URL / SMOKE_ORIGIN env vars also work)
"""

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

BASE = (sys.argv[1] if len(sys.argv) > 1 else os.getenv("SMOKE_BASE_URL", "http://localhost:8000")).rstrip("/")
# Second argument (or SMOKE_ORIGIN): the deployed frontend's origin, e.g. https://yourapp.vercel.app.
# When given, also verifies CORS and that the deployed frontend was built with THIS backend's URL.
ORIGIN = (sys.argv[2] if len(sys.argv) > 2 else os.getenv("SMOKE_ORIGIN", "")).rstrip("/") or None
TIMEOUT = 30
failures = []
# Sign-up is open on a local backend and off on a deployed one (auth.py signup_allowed).
SIGNUP_OPEN = urllib.parse.urlparse(BASE).hostname in ("localhost", "127.0.0.1", "::1") or os.getenv("SMOKE_SIGNUP_OPEN") == "1"


class Skip(Exception):
    """A check that can't run here (say why in the message); printed as SKIP, not counted as a failure."""


def check(name, fn):
    try:
        fn()
        print(f"PASS  {name}")
    except Skip as e:
        print(f"SKIP  {name}: {e}")
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
    # the code-split build keeps the API base in a shared chunk (api-*.js) that index.html only preloads
    scripts += re.findall(r'<link[^>]*rel="modulepreload"[^>]*href="([^"]+\.js)"', html)
    bundle = "".join(fetch_text(urllib.parse.urljoin(ORIGIN + "/", s)) for s in scripts)
    if "VITE_API_URL is not set" in bundle and BASE not in bundle:
        raise AssertionError(f"the frontend at {ORIGIN} was built WITHOUT VITE_API_URL — set it in Vercel to {BASE} and redeploy")
    assert BASE in bundle, f"the frontend at {ORIGIN} was built for a different backend (expected {BASE} in its bundle)"


def need_user():
    if not token.get("value"):
        raise Skip("sign-up is off on this backend, so there is no throwaway user")


def test_signup():
    if SIGNUP_OPEN:
        payload = request("POST", "/api/auth/signup", {"email": email, "password": "smoketest123"})
        assert "access_token" in payload
        token["value"] = payload["access_token"]
        return
    # a hosted backend: nobody can make an account, and the answer is the same 403 whatever the body
    payload = request("POST", "/api/auth/signup", {"email": email, "password": "smoketest123"}, expect=403)
    assert "turned off" in payload["detail"], payload
    request("POST", "/api/auth/signup", {"email": "not-an-email", "password": "x"}, expect=403)
    request("POST", "/api/auth/signup", {}, expect=403)


def test_me_authenticated():
    need_user()
    payload = request("GET", "/api/auth/me", headers={"Authorization": f"Bearer {token['value']}"})
    assert payload["email"] == email


def test_me_unauthenticated():
    request("GET", "/api/auth/me", expect=401)


def test_signup_duplicate_rejected():
    need_user()
    request("POST", "/api/auth/signup", {"email": email, "password": "smoketest123"}, expect=400)


def test_signup_rejects_short_password():
    need_user()
    request("POST", "/api/auth/signup", {"email": f"x{email}", "password": "short"}, expect=422)


def test_signup_rejects_bad_email():
    need_user()
    request("POST", "/api/auth/signup", {"email": "not-an-email", "password": "longenough123"}, expect=422)


def test_login_email_case_insensitive():
    need_user()
    body = urllib.parse.urlencode({"username": f"  {email.upper()} ", "password": "smoketest123"}).encode()
    req = urllib.request.Request(BASE + "/api/auth/login", data=body, method="POST")
    with urllib.request.urlopen(req) as resp:
        assert "access_token" in json.loads(resp.read())


def test_removed_template_routes():
    # the template's upload, saved-scenarios and share-link routes were cut (nothing in the app used them, and on a
    # public host they were open write surface): they must stay gone, not merely need a login
    for method, path, data in (
        ("POST", "/api/upload", None),
        ("GET", "/uploads/abc.png", None),
        ("GET", "/api/scenarios", None),
        ("POST", "/api/scenarios", {"name": "x", "lat": 28.5, "lon": -81.4, "mw": 500}),
        ("POST", "/api/scenarios/examples", None),
        ("GET", "/api/share/abcdefgh", None),
    ):
        request(method, path, data, expect=404)


check("health check", test_health)
check("CORS allows the frontend origin (when SMOKE_ORIGIN is set)", test_cors_for_frontend_origin)
check("deployed frontend was built for this backend (when SMOKE_ORIGIN is set)", test_frontend_points_at_this_backend)
check("signup returns a token here (open locally) or answers 403 (off on a deployed backend)", test_signup)
check("authenticated /me returns correct user", test_me_authenticated)
check("unauthenticated /me is rejected", test_me_unauthenticated)
check("duplicate signup is rejected", test_signup_duplicate_rejected)
check("short password is rejected", test_signup_rejects_short_password)
check("malformed email is rejected", test_signup_rejects_bad_email)
check("login ignores email case/whitespace", test_login_email_case_insensitive)
check("removed routes (upload, uploads, saved scenarios, share links) answer 404", test_removed_template_routes)


def test_ai_status():
    payload = request("GET", "/api/ai/status")
    assert isinstance(payload["configured"], bool)
    return payload["configured"]


def test_ai_ask_requires_auth():
    request("POST", "/api/ai/ask", {"prompt": "hi"}, expect=401)


def test_ai_ask_path():
    need_user()
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
    request("POST", "/api/grid/whatif", {**ORLANDO, "mw": 50001}, expect=422)
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
check("what-if rejects a point outside Florida and a size of 0 or over 50 GW", test_whatif_validation)
check("cascade on the Orlando site terminates, homes monotone, matches expected", test_cascade_orlando)
check("headroom: one finite value per substation, agrees with the what-if", test_headroom)


# Feature checks: each backend/smoke_checks/<feature>.py defines register(ctx) and adds its own
# checks through ctx.check — one file per feature, so parallel work never edits this file.
import importlib.util  # noqa: E402
import types  # noqa: E402

ctx = types.SimpleNamespace(
    check=check,
    request=request,
    expected=EXPECTED,
    auth=lambda: {"Authorization": f"Bearer {token['value']}"} if token.get("value") else {},
)
for _path in sorted((Path(__file__).parent / "smoke_checks").glob("*.py")):
    _spec = importlib.util.spec_from_file_location(f"smoke_checks_{_path.stem}", _path)
    _mod = importlib.util.module_from_spec(_spec)
    try:
        _spec.loader.exec_module(_mod)
        _mod.register(ctx)
    except Exception as e:  # noqa: BLE001 — a broken check module is a failure, not a crash
        print(f"FAIL  smoke_checks/{_path.name} could not run: {e}")
        failures.append(f"smoke_checks/{_path.name}")

if failures:
    print(f"\n{len(failures)} check(s) failed: {', '.join(failures)}")
    sys.exit(1)
print("\nAll checks passed.")
