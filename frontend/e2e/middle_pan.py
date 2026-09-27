"""
Middle-mouse panning check: holding the middle button and dragging pans the map on every map page
(Watch it fail / Strengthen the grid — both GridMap; Build together — PlansMap; Proposed data centers — UsMap),
whatever the mode, without adding a campus, drawing a hurricane track, or selecting anything. Not wired into
scripts/check.sh (a scenario-specific regression check, not part of the general smoke pass); run directly:

    python frontend/e2e/middle_pan.py [frontend-url]     (default http://localhost:5173)

Requires the dev server (or a preview build) already running; asserts against real DOM state, not screenshots.
"""

import sys
import time

from playwright.sync_api import sync_playwright

URL = (sys.argv[1] if len(sys.argv) > 1 else "http://localhost:5173").rstrip("/")

failures = []


def check(name, ok, detail=""):
    print(f"{'PASS' if ok else 'FAIL'}  {name}{'' if ok else ': ' + detail}")
    if not ok:
        failures.append(name)


def middle_drag(page, cx, cy, dx=-120, dy=-70, steps=6):
    page.mouse.move(cx, cy)
    page.mouse.down(button="middle")
    for i in range(1, steps + 1):
        page.mouse.move(cx + dx * i / steps, cy + dy * i / steps)
    page.mouse.up(button="middle")


# a point off toward a corner of the map, away from its dead center: the demo's proposal plaques, project marks
# and other overlays sit near the middle of the land they mark and would otherwise swallow the pointerdown (their
# own onPointerDown stops it from reaching the map) before this test's drag ever starts
def corner_point(box, fx=0.88, fy=0.85):
    return box["x"] + box["width"] * fx, box["y"] + box["height"] * fy


def grid_map_case(page, hash_route, label):
    page.goto(f"{URL}/#{hash_route}", wait_until="networkidle", timeout=30000)
    page.wait_for_selector(".map-svg", timeout=15000)
    time.sleep(1.5)  # the grid, and (on Strengthen) Florida's precomputed study, to settle
    cam = page.locator(".map-cam")
    before = cam.get_attribute("style")
    sites_before = page.locator(".site").count()
    cx, cy = corner_point(page.locator(".map-svg").bounding_box())
    middle_drag(page, cx, cy)
    time.sleep(0.3)
    after = cam.get_attribute("style")
    sites_after = page.locator(".site").count()
    check(f"{label}: middle-drag pans the camera", before != after, f"transform unchanged: {after}")
    check(f"{label}: middle-drag adds no campus", sites_after == sites_before, f"{sites_before} -> {sites_after} sites")


def plans_map_case(page):
    page.goto(f"{URL}/#/plans", wait_until="networkidle", timeout=30000)
    page.wait_for_selector(".gl-map__svg", timeout=20000)
    time.sleep(1.5)
    g = page.locator(".gl-map__svg > g").first
    before = g.get_attribute("transform")
    cx, cy = corner_point(page.locator(".gl-map__svg").bounding_box())
    middle_drag(page, cx, cy)
    time.sleep(0.3)
    after = g.get_attribute("transform")
    check("Build together: middle-drag pans the camera", before != after, f"transform unchanged: {after}")
    check("Build together: no pair sheet opened on release", page.locator(".gl-sheet").count() == 0)


def us_map_case(page):
    page.goto(f"{URL}/#/views", wait_until="networkidle", timeout=30000)
    page.wait_for_selector(".vw-map__svg", timeout=20000)
    svg = page.locator(".vw-map__svg")
    svg.scroll_into_view_if_needed()  # this tab's map sits well below the fold, unlike the other three pages
    time.sleep(1.5)
    before = svg.get_attribute("viewBox")
    cx, cy = corner_point(svg.bounding_box())
    middle_drag(page, cx, cy)
    time.sleep(0.3)
    after = svg.get_attribute("viewBox")
    check("Proposed data centers: middle-drag pans the camera", before != after, f"viewBox unchanged: {after}")
    check("Proposed data centers: no state filter toggled on release", page.locator(".vw-state--on").count() == 0)


with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, args=["--disable-speech-api", "--mute-audio"])
    page = browser.new_page(viewport={"width": 1440, "height": 900})

    grid_map_case(page, "/", "Watch it fail")
    grid_map_case(page, "/strengthen", "Strengthen the grid")
    plans_map_case(page)
    us_map_case(page)

    browser.close()

print()
if failures:
    print(f"FAILED: {', '.join(failures)}")
    sys.exit(1)
print("ALL CHECKS PASSED")
