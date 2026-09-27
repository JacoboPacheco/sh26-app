"""
Create (or recreate) the demo account and demo data in one command — locally,
or on the deployed backend after the first deploy. Idempotent: safe to rerun.

    venv/Scripts/python seed.py                          # local backend on :8000
    venv/Scripts/python seed.py https://<render-url>     # deployed backend

Demo login (change via env DEMO_EMAIL / DEMO_PASSWORD, and write it on paper):
    demo@example.com / demo-password1

Add the project's own demo data in `seed_project_data` below, through the API
(so it works everywhere), using the demo user's token.

Sign-up is off by default on a hosted backend (Render sets RENDER): to create the demo account on a FRESH
hosted database, set ALLOW_SIGNUP=1 on the backend for one deploy, run this, then remove it again. Locally
and against an existing account nothing extra is needed.
"""

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

# base URL: first argument, else SMOKE_BASE_URL, else the local dev server
BASE = (sys.argv[1] if len(sys.argv) > 1 else os.getenv("SMOKE_BASE_URL", "http://localhost:8000")).rstrip("/")
EMAIL = os.getenv("DEMO_EMAIL", "demo@example.com")
PASSWORD = os.getenv("DEMO_PASSWORD", "demo-password1")


def call(method, path, data=None, token=None, form=None):
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if form is not None:
        body = urllib.parse.urlencode(form).encode()
    elif data is not None:
        body = json.dumps(data).encode()
        headers["Content-Type"] = "application/json"
    else:
        body = None
    req = urllib.request.Request(BASE + path, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            return resp.status, json.loads(resp.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null")
    except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
        print(f"backend at {BASE} is not reachable ({e}); start it first (or wait for Render to wake up)")
        sys.exit(1)


def get_token() -> str:
    status, payload = call("POST", "/api/auth/signup", {"email": EMAIL, "password": PASSWORD})
    if status == 200:
        print(f"created demo account {EMAIL}")
        return payload["access_token"]
    # 400: the account exists. 403: sign-up is switched off (a hosted deploy: see ALLOW_SIGNUP in
    # backend/.env.example), so an account made earlier is all there is to sign in as.
    if status in (400, 403):
        status2, payload2 = call("POST", "/api/auth/login", form={"username": EMAIL, "password": PASSWORD})
        if status2 == 200:
            print(f"demo account {EMAIL} already exists, logged in")
            return payload2["access_token"]
        if status == 403:
            print(
                f"sign-up is off on {BASE} and {EMAIL} could not log in ({status2}). To create the demo account once on a "
                "fresh hosted database: set ALLOW_SIGNUP=1 on the backend (Render -> Environment), redeploy, run this "
                "script, then remove ALLOW_SIGNUP again."
            )
            sys.exit(1)
    print(f"could not sign up or log in as {EMAIL}: {status} {payload}")
    sys.exit(1)


def seed_project_data(token: str) -> None:
    """The project's own demo data, written through the API with the demo user's token. Idempotent.
    Nothing to write today: the demo cases (the Fort Myers hero and the rest) open by link and are computed by
    the engine, and the saved-scenarios routes that used to hold them were removed (Sun 01:55)."""
    return


if __name__ == "__main__":
    status, _ = call("GET", "/api/health")
    if status != 200:
        print(f"backend at {BASE} is not answering (HTTP {status}); start it first")
        sys.exit(1)
    token = get_token()
    seed_project_data(token)
    print(f"seeded {BASE} - demo login: {EMAIL} / {PASSWORD}")
