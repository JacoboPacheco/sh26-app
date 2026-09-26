# SPEC — Overload

_Written by `/spec` Fri 2026-09-25 23:58 ET (K+0:58). Judges grade what they see work in 3 minutes: one problem in a sentence, one moment that makes them react, one technical piece doing real work, polish over breadth._

## Problem

Every new AI data center asks the grid for hundreds of megawatts, and nobody outside a utility can see which lines that load would push past their limits or how one overloaded line becomes a regional blackout. Overload lets anyone drop a data center on a model of Florida's grid and watch, live, what fails and whose lights go out.

**Hook (the first sentence the judge hears):** "When the next AI data center plugs in, whose lights go out?"
**First 10 seconds the judge sees:** a dark Florida with the grid glowing on it, one data-center card in the sidebar saying "500 MW — drag me anywhere", and a pill reading "Synthetic grid model (Breakthrough Energy / Texas A&M), not any utility's network."

## Demo script (the exact clicks, 3 minutes, laptop on localhost)

_Revised Sat 00:58 (user): one hero site, Fort Myers — it cascades at 1,500 MW and is calm at 500 MW. Orlando stays seeded as the smoke-test anchor._

1. **Open** — the page loads signed in as the demo account, no login screen. Florida (Census outline) with the grid drawn as lines colored by loading (calm), substations as dots sized by load, the synthetic-model pill, the hook line, the data-center card with a size slider, and saved scenarios as chips ("Fort Myers · 1,500 MW" first).
2. **Drop** — the judge clicks "Fort Myers · 1,500 MW" (or drags the card there at 1,500 MW). The camera flies in; the overloaded line glows red. The panel reads "Connected at FORT MYERS 3 (345 kV). 1 line and 1 transformer over limit" with each loading percentage, and "This site can take 560 MW before the first line overloads."
3. **Cascade** — "Run the cascade". The camera pulls out to everything the cascade will touch; the worst line flashes and trips, flow redistributes, one step every 0.6 s with a scrubber. Substations lose supply and the land around them goes black. The counter counts up to "Homes without power: 1,051,974 (estimate)". It ends "The grid split after 9 steps: 1,503 MW of existing load lost. The data center's own 1,500 MW lost power too."
4. **Scale** — slide to 500 MW at the same spot: "No line over limit." Point: size matters — this site takes 560 MW.
5. **Headroom** — "Where can 500 MW go?": every substation recolors by the MW it can take before the first overload; the legend counts them (115 can take 500 MW, 797 can take under 250). At 1,500 MW the answer is none — the opening for nice-to-have 4.
6. **Stretch (only if nice-to-have 4 is built)** — "Fix it" lists the cheapest line upgrades that clear the overload; applying them turns the map calm.

A saved-scenario click reproduces step 2 exactly (same site, same MW); that is what `demo_path.py` replays, through step 5.

## Must-have features (each with its acceptance check)

- [x] **M1 — Drop a load, see the overloads.** Florida cut of the synthetic grid served by the backend; the frontend draws it; dragging the data-center card (or clicking a saved scenario) calls the what-if endpoint, which snaps to the nearest substation's highest-voltage bus, adds the load, re-solves a DC power flow, and returns every branch's loading, the overloaded branches, and the site's headroom in MW. Saved scenarios (table + endpoints, seeded with the two named above) so the demo and `demo_path.py` replay a known drop. The size slider (100–2,000 MW) is part of M1.
  Acceptance: `smoke_test.py` posts the seeded Orlando drop and gets the overloaded-branch set and headroom recorded in `backend/demo/expected_whatif.json` (regenerated only by `validate.py`); `demo_path.py` clicks "Orlando · 500 MW" and sees "over limit" in the panel.
- [x] **M2 — Cascade with the homes counter.** Endpoint that trips the most overloaded branch above 100 %, re-solves, repeats until no branch is over limit or 30 steps; islands with no generation go dark, islands with a deficit shed the deficit; each step returns tripped ids, newly dark substations, the branches above 80 % with loading, lost MW and estimated homes (lost MW × 700, labeled "estimate, ~1.4 kW per home"). Frontend animates the steps with a scrubber and the counter.
  Acceptance: smoke check that the seeded Orlando cascade terminates within 30 steps, lost MW never exceeds total Florida load, and homes are monotone non-decreasing; `demo_path.py` presses "Run the cascade" and sees "Homes without power".
- [x] **M3 — Headroom heatmap.** Endpoint returning, for every substation, the MW that can be added before the first branch reaches 100 %; computed once at startup (one sparse solve per bus, cached in memory), independent of the slider value (the slider only sets the color threshold). Frontend toggle recolors substations with a legend.
  Acceptance: smoke check that headroom returns one finite value ≥ 0 per substation and that the Orlando substation's value equals the what-if headroom within 1 MW; `demo_path.py` toggles it and sees the legend.

The walking skeleton is M1 + M2 + M3 in their ugliest form on the UI kit's defaults (PLAYBOOK Phase 2). If it isn't green by K+10 the cut rule removes M3 first: it is step 4 of the script, furthest from the wow moment in step 3.

## Nice-to-haves (build order; the last is cut first)

1. **Hospitals on backup** — HIFLD Hospitals (public), Florida subset committed as `backend/demo/hospitals_fl.json`; hospitals within 15 km of a dark substation show "on backup power" during the cascade, with a count.
2. **Hurricane trigger** — click any line to knock it out; the same cascade engine runs from there. Copy: "a hurricane takes out this corridor".
3. **Plain-English explanation (the one extra integration — MLH Gemini API)** — `POST /api/grid/explain` sends the cascade summary to `complete()` with `fallback=` a templated sentence and `timeout=10`, returns `{text, fallback}`, stores nothing; the UI shows a `<Badge tone="warn">` when it fell back. Built only after every must-have is green; `demo_path.py` must pass with it disabled.
4. **Fix it (stretch)** — greedy: raise the rating of the most overloaded line by one standard step, re-run, repeat until calm; list the upgrades and their total added MVA.
5. **Basemap tiles** — optional dark tiles under the outline if the design pass wants streets; the app must keep working with no network access to a tile server.

## Explicitly out of scope

AC power flow, voltages, frequency or dynamics; real utility data, real-time data, or any claim about a real network; user-uploaded grids; cost or market modeling; N-2 contingency screening; a login screen or multi-user features; report export; a native mobile layout (the page must not overflow at 375 px and the map scales, but the demo is the laptop).

## Sponsor challenges targeted, and why

- **Best Overall** — automatic. The whole spec serves it.
- **MLH — Best Use of Gemini API** — only if nice-to-have 3 ships and is in the demo. It is the one extra integration; the key is already in `backend/.env`.
- **Best First-Time Hacker** — eligibility only, judged separately, cannot affect Best Overall; opt in at `/ship-check` if the user confirms eligibility (user: only if it can't cost the top prize).
- **Not claimed:** Sperry GridLock (requires comparing two utilities' construction plans, which the demo doesn't show), Waymo, Microsoft, Assurant, Blackstone, State Farm, INIT, DigitalOcean (Render is the tested deploy path; switching hosts for a track costs hours), Tiger Data, Snowflake, MongoDB, Solana, ElevenLabs, GoDaddy (a domain is a Phase 5 nicety, not a target).

## Data and honesty

- **Source:** Breakthrough Energy Sciences' USA test system (`PowerSimData`, `powersimdata/network/usa_tamu/data/`: `bus.csv`, `branch.csv`, `sub.csv`, `bus2sub.csv`, `plant.csv`, `zone.csv`), derived from Texas A&M's ACTIVSg synthetic grids. License CC-BY 4.0; credit both on screen (footer) and in the README. Raw files: `https://raw.githubusercontent.com/Breakthrough-Energy/PowerSimData/develop/powersimdata/network/usa_tamu/data/<file>` (verified Fri 23:52: `sub.csv` has `lat,lon`; `bus.csv` has `Pd, Va, baseKV, zone_id, interconnect`; `zone.csv` rows 21–23 are Florida Panhandle / North / South; `branch.csv` is >10 MB and was not opened — expected MATPOWER columns `from_bus_id, to_bus_id, r, x, rateA, Pf, Pt`; check the header first thing).
- **Cut:** buses with `zone_id ∈ {21, 22, 23}`; branches with both ends inside; a branch with one end outside becomes a fixed injection at its Florida end equal to the dataset's own solved flow (`Pf`/`Pt`); generation is scaled proportionally so the island balances (DC flow has no losses). Substation coordinates via `bus2sub` → `sub`. Branches with a zero rating get a default by voltage class, and the build log says how many.
- **Output committed:** `backend/demo/florida_grid.json` (buses, substations with coordinates, branches with reactance and rating, ties, source and license text). Never fetched at runtime.
- **On screen, always:** the pill "Synthetic grid model (Breakthrough Energy / Texas A&M), not any utility's network" and "estimate" on the homes counter. The pitch never says FPL, Duke or TECO.

## Milestones

- **Milestone 0 — validation gate, by 3:00 AM Sat (K+4). PASSED Sat 00:10 (K+1:10): correlation 1.000, 0 base-case overloads, 19 of 20 random 500 MW sites overload something; `expected_whatif.json` written for Orlando (7 branches, headroom 217 MW).** `backend/demo/build_grid.py` builds the JSON; `backend/demo/validate.py` prints: (a) the Pearson correlation between our DC base-case branch flows and the dataset's solved `Pf` on Florida branches, (b) the share of branches over 100 % in our base case, (c) the seeded Orlando drop's overloaded set and headroom, which it writes to `expected_whatif.json`. **Pass:** correlation ≥ 0.9, base-case overloads ≤ 2 % of branches, and a 500 MW drop somewhere in Florida produces at least one overload. **Fail at 3:00 AM:** rebuild with `interconnect == "Texas"` (an island by design, no boundary cut) and re-validate; if the DC power flow itself is untrustworthy, switch to the `RUNNER-UP` in CLAUDE.md → Decisions (Next Stop) via `/spec` again ("Restart on runner-up").
- **Milestone 1 — walking skeleton, by K+10 (9:00 AM Sat). Green in `/check` Sat 00:52 (K+1:52), including `demo_path.py`; EXAMPLE feature removed; waiting on the human's click-through.** M1–M3 ugly but end to end; `demo_path.py` green in `/check`; the template's EXAMPLE feature removed; the human clicked through on localhost. Then deploy (PLAYBOOK → Deploy) and, per Decisions, the design direction before more features.
- **Phase 3:** nice-to-haves 1–2, then the Gemini extra, then 4–5.

## New endpoints and tables

Endpoints (all JSON; what-if and cascade rate-limited `120/minute` — one sparse solve each, and the size slider re-fires the what-if; scenario POSTs `30/minute`; never below 30 per CLAUDE.md):
- `GET /api/grid` — the drawable grid: substations `{id, name, lat, lon, load_mw, kv_max}`, branches `{id, from_sub, to_sub, kv, rate_mva, base_pct}`, `meta {source, license, synthetic: true, bus_count, branch_count}`. Public, cached in memory.
- `POST /api/grid/whatif` `{lat, lon, mw}` → `{bus, sub_name, loading_pct: [per branch], overloaded: [{id, pct, from, to, kv}], headroom_mw}`. 422 with a clear message when the point is outside Florida's bounding box or `mw` is outside 1–5,000.
- `POST /api/grid/cascade` `{lat, lon, mw}` → `{steps: [{n, tripped: [ids], dark_subs: [ids], hot: [{id, pct}], lost_mw, homes}], final_loading_pct: [...], outcome: "settled" | "islanded", total_steps}`.
- `GET /api/grid/headroom` → `{by_sub: {sub_id: mw}}` (the headroom at the substation's connect bus — the bus a drop there connects to — so it equals the what-if headroom; changed Sat 00:25 from "min over the substation's buses", which disagreed at 266 of 1,329 substations).
- `GET /api/scenarios`, `POST /api/scenarios` `{name, lat, lon, mw}`, `DELETE /api/scenarios/{id}` — `Depends(get_current_user)`, owner-only 404 on delete, validation like `items.py`.
- Nice-to-have 3 adds `POST /api/grid/explain`; nice-to-have 2 adds an optional `trip: [branch_id]` field to `cascade`.

Tables (`backend/models.py`):
- `Scenario`: `id`, `name String(80)`, `lat Float`, `lon Float`, `mw Integer`, `note String(280) | None`, `summary JSON | None` (last what-if: overload count, headroom, homes), `user_id FK users`.

Backend modules: `backend/powerflow.py` (pure math: load the JSON, build B′, factor it once with a sparse LU, base flows, what-if, headroom, cascade — no FastAPI imports, so `validate.py` can import it), `backend/grid.py` (router), `backend/scenarios.py` (router, shaped like `items.py`), `backend/demo/build_grid.py`, `backend/demo/validate.py`. New Python deps: `numpy`, `scipy` (wheels on Windows and Linux; add both to `requirements.txt`). Frontend: no new dependency — the map is an SVG with a simple equirectangular projection over a committed Florida outline (`frontend/src/data/florida_outline.json`, Census cartographic boundary, public domain) plus ten city labels; pan/zoom via wheel and drag on the SVG group. Outline built once by `backend/demo/build_outline.py` from `cb_2024_us_state_20m` (downloaded with the user's OK, Sat 01:01). Everything renders offline.

Memory rule (Render free tier is 512 MB): never build a dense PTDF or B′⁻¹ (≈ 4k × 4k floats and up); factor once with `scipy.sparse.linalg.splu`, one solve per what-if, and precompute only the per-bus headroom vector at startup.

## Starter pieces touched

- `uploads.py` — reused as-is (unused by the demo; stays for the smoke checks).
- `llm.py` — reused as-is; only nice-to-have 3 calls `complete(prompt, fallback=..., timeout=10)`.
- `seed.py` — `seed_project_data` extended: creates the two scenarios through `POST /api/scenarios` if no scenario with that name exists (idempotent). Milestone 1.
- `smoke_test.py` — extended with the checks below. Milestone 1.
- The EXAMPLE feature (`Item`, `items.py`, its router line, smoke checks, seed rows, `ItemsPanel.jsx`, its use in `App.jsx`) is copied for `scenarios.py` and then removed before milestone 1.
- `frontend/src/api.js` — add `grid()`, `whatIf()`, `cascade()`, `headroom()` and scenario helpers; nothing calls `fetch` directly. `useAuth()` stays in `App.jsx`; no login screen.

## Verification

`smoke_test.py` (runs in `/check` and against Render, as its own throwaway user, creating nothing a judge sees):
- grid loads: ≥ 1,000 buses, ≥ 1,000 branches, every substation inside 24.3–31.1 N / −87.7 to −79.4 W (the dataset's offshore tie points reach −79.57), every branch rating > 0, `meta.synthetic` is true.
- what-if on the committed Orlando site (`backend/demo/expected_whatif.json`: lat, lon, mw, expected overloaded ids, expected headroom) matches: same overloaded set, headroom within 1 MW.
- what-if outside Florida → 422; `mw = 0` and `mw = 9999` → 422.
- cascade on the Orlando site: terminates ≤ 30 steps, `lost_mw` ≤ total load, homes monotone, outcome is one of the two strings.
- headroom: one finite value ≥ 0 per substation; the Orlando substation's value equals the what-if headroom within 1 MW.
- scenarios: unauthenticated → 401; create + list + delete roundtrip; another user's scenario → 404 on delete; blank or 81-character name → 422.
- (nice-to-have 3) explain returns text and a boolean `fallback` whether or not the key is present.

`frontend/e2e/demo_path.py` (URL as argv[1]; creates nothing): open → "Signed in as" and the synthetic pill visible → click "Fort Myers · 1,500 MW" → "over limit" and a red line → "Run the cascade" → "Homes without power", an outcome line, a dark substation → slider to 500 MW by keyboard → "No line over limit." → "Where can 500 MW go?" → legend and a green substation.
- smoke: the hero site (`expected_whatif.json` → `hero`, written by `validate.py`) cascades with the recorded steps and dark-substation count at 1,500 MW and has nothing over limit at 500 MW.

Committed sample files: `backend/demo/florida_grid.json`, `backend/demo/expected_whatif.json`; nice-to-have 1 adds `backend/demo/hospitals_fl.json`.

Latency ("within a second" for a drop, ~0.6 s per cascade step) and the validation numbers above are targets, measured after the skeleton, never promised in the pitch.

## Open questions (don't block the skeleton)

- ~~Whether the Panhandle's ties leave the cut well-conditioned.~~ Resolved Sat 00:10: in the model the Panhandle (284 buses, 3.5 GW) connects only through Alabama/Georgia, so the build keeps the largest component: peninsular Florida, 2,469 buses, 1,329 substations, 3,347 branches. Say "peninsular Florida" on screen if asked.
- Resolved Sat 00:10: 644 branches the dataset marks "unlimited" (rating 0, mostly transformers) get max(kV-class default, base flow + 30 %) and carry `rate_est: true`; the base case is calm (max 95 % loading). The homes counter counts existing load only; the data center's own blackout is reported separately (`site_dark_mw`).
- The exact homes-per-MW constant (700 is ~1.4 kW average household load); label it an estimate either way.
- Whether the cascade should trip one line per step (better animation) or every line over 100 % (faster spread); start with one per step.
- Measured Sat 00:55 (for the demo script, the user decides): Orlando's cascade is small (3 steps, ~38k homes, 1 substation dark) and identical at 500–5,000 MW because the site's own feeders trip first and island the data center; Fort Myers (26.64, −81.87) is calm at 500 MW and collapses at 1,500 MW (9 steps, ~1.05M homes, 49 substations dark) — a better step 5; substation MIAMI 23 (25.757, −80.246) at 500 MW runs 25 steps (~1.96M homes, 33 dark). 1,215 of 1,329 substations cascade at 500 MW; several Miami/Fort Lauderdale sites hit the 30-step cap.
