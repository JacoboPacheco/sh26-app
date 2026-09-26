"""
The demo path (SPEC.md → Demo script), replayed in a browser and asserted step by step.
scripts/check.sh runs it on every change.

    python frontend/e2e/demo_path.py [frontend-url]     (default http://localhost:4173)

Needs the demo auto-login (VITE_DEMO_EMAIL/VITE_DEMO_PASSWORD at build time) and a seeded
backend (the "Fort Myers · 1,500 MW" scenario). Creates nothing, so it is safe against the deployed app.
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
        expect(page.get_by_text("Signed in as")).to_be_visible(timeout=15000)
        expect(page.get_by_text("Synthetic grid model", exact=False)).to_be_visible()
        expect(page.locator("line.ln").first).to_be_attached(timeout=15000)

        # 2 — drop 1,500 MW at Fort Myers (the saved scenario replays the same site and size): lines over limit.
        page.get_by_role("button", name="Fort Myers · 1,500 MW", exact=True).click()
        expect(page.get_by_text("over limit").first).to_be_visible(timeout=15000)
        expect(page.get_by_text("before the first line overloads")).to_be_visible()
        expect(page.locator("line.ln--over").first).to_be_attached()

        # 3 — the cascade: the homes counter appears, steps play to an outcome, substations go dark.
        page.get_by_role("button", name="Run the cascade").click()
        expect(page.get_by_text(re.compile(r"(People|Homes) without power")).first).to_be_visible(timeout=15000)
        expect(page.get_by_text("The grid split after").or_(page.get_by_text("Settled after"))).to_be_visible(timeout=30000)
        expect(page.locator("circle.sub--dark").first).to_be_attached()

        # 4 — scale: the same spot at 500 MW is calm (keyboard on the slider, as a judge could do).
        slider = page.get_by_label("Size (MW)")
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
