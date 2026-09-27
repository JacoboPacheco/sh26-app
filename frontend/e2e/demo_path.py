"""
The demo path (SPEC.md → Demo script), replayed in a browser and asserted step by step.
scripts/check.sh runs it on every change.

    python frontend/e2e/demo_path.py [frontend-url]     (default http://localhost:4173)

Needs the demo auto-login (VITE_DEMO_EMAIL/VITE_DEMO_PASSWORD at build time) and a seeded
backend. Opens the hero with its link (#/?at=26.6406,-81.8723&mw=1500). Creates nothing, so it is safe against the deployed app.
"""

import os
import re
import sys

from playwright.sync_api import expect, sync_playwright

URL = (sys.argv[1] if len(sys.argv) > 1 else os.getenv("E2E_URL", "http://localhost:4173")).rstrip("/")

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    ok = False
    try:
        # 1 — open: signed in as the demo account, the grid drawn, the synthetic-model pill visible.
        page.goto(URL, wait_until="load", timeout=30000)
        # signed in as the demo account: the token is stored (the page no longer prints "Signed in as")
        page.wait_for_function("() => !!localStorage.getItem('token')", timeout=15000)
        expect(page.locator(".appbar")).to_be_visible()
        expect(page.locator(".pill", has_text="Synthetic grid model")).to_be_visible()
        expect(page.locator("line.ln").first).to_be_attached(timeout=15000)

        # 2 — drop 1,500 MW at Fort Myers (the hero link drops the same site and size): lines over limit.
        page.goto(URL + "/#/?at=26.6406,-81.8723&mw=1500", wait_until="load", timeout=30000)
        page.wait_for_function("() => !!localStorage.getItem('token')", timeout=15000)
        expect(page.get_by_text("over limit").first).to_be_visible(timeout=15000)
        expect(page.get_by_text("before the first line overloads")).to_be_visible()
        expect(page.locator("line.ln--over").first).to_be_attached()
        # Fix it lives on Strengthen now: the results column offers it right after the drop, before any cascade,
        # and the left rail has no Fix it (or Plants) mode.
        expect(page.get_by_role("button", name=re.compile(r"^Fix it in Strengthen"))).to_be_visible(timeout=15000)
        expect(page.locator("nav.modes").get_by_role("button", name=re.compile(r"^(Fix it|Plants)$"))).to_have_count(0)

        # 3 — the cascade: the people-hit counter appears, steps play to an outcome, substations go dark.
        page.get_by_role("button", name="Run the cascade").click()
        expect(page.get_by_text(re.compile(r"People hit|(People|Homes) without power")).first).to_be_visible(timeout=15000)
        expect(page.get_by_text("The grid split after").or_(page.get_by_text("Settled after"))).to_be_visible(timeout=30000)
        expect(page.locator("circle.sub--dark").first).to_be_attached()

        # 4 — scale: the same spot at 500 MW is calm (keyboard on the slider, as a judge could do).
        slider = page.get_by_label("Size (MW)", exact=True)
        slider.focus()
        slider.press("Home")  # 100 MW
        for _ in range(8):  # 50 MW per step → 500 MW
            slider.press("ArrowRight")
        expect(page.get_by_text("No line over limit.")).to_be_visible(timeout=15000)

        # 5 — headroom: the toggle recolors substations and shows the legend.
        page.get_by_role("button", name="Where can 500 MW go?").click()
        expect(page.get_by_text("Headroom before the first overload")).to_be_visible(timeout=15000)
        expect(page.locator("circle.sub--hr-ok").first).to_be_attached()
        ok = True
        print("PASS  demo path")
    except Exception as e:  # noqa: BLE001 — any failure is a FAIL line plus a non-zero exit
        print(f"FAIL  demo path: {type(e).__name__}: {str(e)[:300]}")
    finally:
        browser.close()
    sys.exit(0 if ok else 1)
