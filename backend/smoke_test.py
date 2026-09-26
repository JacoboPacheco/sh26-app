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
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid

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


# EXAMPLE feature (items.py) — every check acts as this run's own throwaway user and
# deletes what it created, so it's safe against the deployed backend too.
auth_headers = {}
item = {}


def test_items_require_auth():
    request("GET", "/api/items", expect=401)
    request("POST", "/api/items", {"title": "x"}, expect=401)


def test_item_create_and_list():
    auth_headers["value"] = {"Authorization": f"Bearer {token['value']}"}
    created = request("POST", "/api/items", {"title": "Smoke item", "notes": "made by smoke_test"}, headers=auth_headers["value"])
    assert created["title"] == "Smoke item" and isinstance(created["tags"], list), created
    assert isinstance(created["fallback"], bool), created
    item["id"] = created["id"]
    listed = request("GET", "/api/items", headers=auth_headers["value"])
    assert any(it["id"] == item["id"] for it in listed), "created item missing from list"


def test_item_validation():
    request("POST", "/api/items", {"title": ""}, headers=auth_headers["value"], expect=422)
    request("POST", "/api/items", {"title": "   "}, headers=auth_headers["value"], expect=422)
    request("POST", "/api/items", {"title": "x" * 121}, headers=auth_headers["value"], expect=422)


def test_item_owner_only():
    # a second throwaway user must not see or delete the first user's item
    other = request("POST", "/api/auth/signup", {"email": f"other-{email}", "password": "smoketest123"})
    other_headers = {"Authorization": f"Bearer {other['access_token']}"}
    request("DELETE", f"/api/items/{item['id']}", headers=other_headers, expect=404)
    assert not any(it["id"] == item["id"] for it in request("GET", "/api/items", headers=other_headers)), "leaked"


def test_item_delete():
    request("DELETE", f"/api/items/{item['id']}", headers=auth_headers["value"])
    request("DELETE", f"/api/items/{item['id']}", headers=auth_headers["value"], expect=404)
    listed = request("GET", "/api/items", headers=auth_headers["value"])
    assert not any(it["id"] == item["id"] for it in listed), "deleted item still listed"


check("items require auth", test_items_require_auth)
check("item create + list roundtrip", test_item_create_and_list)
check("item validation rejects empty, blank, and oversized titles", test_item_validation)
check("items are owner-only", test_item_owner_only)
check("item delete works", test_item_delete)

if failures:
    print(f"\n{len(failures)} check(s) failed: {', '.join(failures)}")
    sys.exit(1)
print("\nAll checks passed.")
