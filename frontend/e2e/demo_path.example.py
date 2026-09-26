"""
EXAMPLE of frontend/e2e/demo_path.py — the script that replays SPEC.md's demo
script and asserts what a judge sees at each step. Copy it to demo_path.py and
rewrite the steps for the real demo; scripts/check.sh runs demo_path.py on every
change once it exists.

    python frontend/e2e/demo_path.py [frontend-url]     (default http://localhost:4173)

Rules it follows: URL as argv[1]; exits non-zero on any failed step; safe against
the deployed app (a unique title per run, and cleanup runs even after a failure);
needs the demo auto-login (VITE_DEMO_EMAIL/VITE_DEMO_PASSWORD at build time) and
a seeded backend. Each run spends one AI-quota slot on the deployed app.
"""

import os
import sys
import uuid

from playwright.sync_api import expect, sync_playwright

URL = (sys.argv[1] if len(sys.argv) > 1 else os.getenv("E2E_URL", "http://localhost:4173")).rstrip("/")
TITLE = f"demo-check-{uuid.uuid4().hex[:6]}"  # unique per run, so a failed run can't collide with the next

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    ok = False
    try:
        # Step 1 — the judge opens the app: it's signed in as the demo account, no login screen.
        page.goto(URL, wait_until="load", timeout=30000)
        expect(page.get_by_text("Signed in as")).to_be_visible(timeout=15000)

        # Step 2 — they add an item and see it appear at the top of the list.
        page.get_by_label("Title").fill(TITLE)
        page.get_by_label("Notes").fill("typed during the demo")
        page.get_by_role("button", name="Add", exact=True).click()  # exact: other buttons contain "add"
        expect(page.get_by_role("listitem").filter(has_text=TITLE)).to_be_visible(timeout=15000)
        ok = True
        print("PASS  demo path")
    except Exception as e:  # noqa: BLE001 — any failure is a FAIL line plus a non-zero exit
        print(f"FAIL  demo path: {type(e).__name__}: {str(e)[:300]}")
    finally:
        # Cleanup — leave the data as the judge should find it, even if a step above failed.
        try:
            page.goto(URL, wait_until="load", timeout=30000)
            row = page.get_by_role("listitem").filter(has_text=TITLE)
            if row.count():
                row.get_by_role("button", name=f"Delete {TITLE}", exact=True).click()
                expect(row).to_have_count(0, timeout=10000)
        except Exception as e:  # noqa: BLE001
            print(f"WARN  cleanup failed: {str(e)[:200]} — delete {TITLE!r} by hand")
        browser.close()
    sys.exit(0 if ok else 1)
