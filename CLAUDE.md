# Project: Overload

You're a great engineer and this team is lucky to have you on this build — let's ship something judges remember.

## Idea
**Overload** — when the next AI data center plugs in, whose lights go out? A synthetic model of Florida's grid (Breakthrough Energy / Texas A&M USA test system, CC-BY 4.0; labeled synthetic on screen, never a real utility's network) glows over a dark map. The judge drags a 500 MW data center onto it: a DC power flow recomputes every line's loading, overloaded lines turn red, and the site's headroom appears. "Run the cascade" trips the worst line, redistributes flow and spreads failures step by step until the grid settles or a region goes dark, while a homes-without-power estimate climbs. A headroom heatmap answers the inverse: where can this load go? For Floridians who'd lose power and anyone deciding where the next data center goes. Judge moment: the drop turns lines red within a second, then the cascade spreads live. Kill switch: power flow validated against the dataset's own solved flows by 3:00 AM Sat, else the Texas interconnect, else the runner-up (SPEC.md → Milestone 0).

## Sponsor / company challenges to target
[Pasted at Phase 0 — name, sponsor, their actual eligibility requirement. `/ideas` and `/spec` mark the targeted ones `← claimed`; the rest stay for a runner-up switch.] Full text + Devpost link: C:\dev\notes\SPONSORS.md. Devpost: https://shellhacks-2026.devpost.com/ (opt in per challenge or you're not judged for it).
- [ ] Sperry Tech — The GridLock Challenge: tool that compares AT LEAST TWO utilities' public future construction plans and flags overlaps (physically close OR scheduled around the same time). Prizes: 1st guaranteed internships + MacBook Airs, 2nd interviews + iPads, 3rd interviews.
- [ ] Waymo — Mobility Challenge: use publicly available data (e.g. Google Maps APIs) to improve transportation. Prizes TBA.
- [ ] Microsoft — What's Missing?: AI-powered experience that makes something difficult/inaccessible/missing better; core experience CANNOT be a chatbot or depend on a chat window; demo must show a real task accomplished. Swag.
- [ ] Assurant — Take Control of AI: privacy protection, spending visibility, or confident AI-tool selection. Headphones / desk set / trackball.
- [ ] Blackstone — Reimagining the Investor Experience: public financial/economic data; portfolio understanding or research. $350 gift card.
- [ ] State Farm — Auto Insurance: make auto insurance simpler for students, reduce everyday risks. Backpacks, safes.
- [ ] INIT National — Building Together: help student builders collaborate over weeks/months. Claude subscriptions per member.
- [ ] MLH — Best Use of Gemini API (swag kits) ← claimed only if the Gemini explanation (SPEC nice-to-have 3) ships in the demo. MLH — Best Use of ElevenLabs (earbuds). MLH — Tiger Data (Postgres-based time-series; Stream Deck Mini). MLH — DigitalOcean ($200 credits; mouse). MLH — Snowflake API (Raspberry Pi). MLH — MongoDB Atlas (M5Stack). MLH — Solana (Ledger). MLH — GoDaddy Registry best domain (gift card).
- Best Overall (1st–3rd; creativity, execution, impact; in-person 3-min demo) — automatic ← the target. Best First-Time Hacker — needs 50% first-timers and opt-in; judged separately, opt in at /ship-check if eligible (user: only if it can't cost #1).

## Stack
FastAPI (backend; SQLite locally, Postgres on Render), React + Vite (frontend), Render + Vercel (deploy) — change only if the idea truly needs something else.

## Scope (hackathon-realistic)
Must have (demo breaks without these — tick each in the commit that finishes it):
- [x] M1 — drop a load (or click a saved scenario), DC power flow, overloaded lines + site headroom; saved scenarios table seeded with "Orlando · 500 MW" and "Miami · 1,500 MW"
- [x] M2 — cascade: trip → re-solve → spread until settled/islanded, animated steps, homes-without-power estimate
- [x] M3 — headroom heatmap: MW each substation can take before the first overload

Nice to have — the expansion the user asked for (Sat 01:10: "everything, while not cluttered"), built in parallel by a workflow (Sat 01:33), one track each:
- [ ] 1. Living grid: electricity flowing along every line (canvas), a one-time opening, a visual map legend
- [ ] 2. Heat-wave clock: time-of-day load presets (3 AM / 9 AM / 4 PM / heat wave) + ambient tint
- [ ] 3. Hurricane mode: draw a storm track, its corridor is knocked out, the same cascade runs (replaces "click a line")
- [ ] 4. Who loses power: a live feed of towns going dark + town labels on the map (hospitals wait for the HIFLD download OK)
- [ ] 5. Fix it (smallest set of upgrades, in MVA) + best sites for the chosen size
- [ ] 6. Gemini emergency bulletin with fallback + read aloud (browser speech) — MLH Gemini
- [ ] 7. AI-boom mode: several gigawatt campuses at once
- [ ] 8. Later: ElevenLabs voice for the bulletin (needs a key) · hospitals on backup (needs the HIFLD download OK) · dark basemap tiles

Explicitly NOT doing:
- [ ] AC power flow / voltages / dynamics; real or real-time utility data; user-uploaded grids; markets or cost modeling; login screen; report export; native mobile layout

## Phases
Every session starts with PLAYBOOK.md → "Knowing where we are", before anything else. When I say I'm leaving or going to sleep, reread PLAYBOOK → Away mode and follow it instead of asking. Every prompt arrives with a `Now:` line from a hook — that's the clock for every time rule; in a long turn, run `date`.

## Commands
Shell commands go through the Bash tool (Git Bash) from the repo root. The shell's directory persists between calls, so don't `cd` — every command here is root-relative and allowlisted.
- Verify everything: `bash scripts/check.sh` (or `/check`, which owns the timeout) — lint, build, backend smoke test, headless-browser check, and `demo_path.py` once it exists. Self-contained on :8765/:4173; the dev servers needn't be running.
- The dev servers are the human's (`.\dev.ps1`). If you must start one, use `run_in_background` — a foreground server blocks the Bash tool. Backend: `backend/venv/Scripts/python -m uvicorn main:app --app-dir backend --reload --reload-dir backend --port 8000`. Frontend: `npm run dev --prefix frontend` (:5173, proxies `/api` and `/uploads` to :8000).
- New dependency: `npm install --prefix frontend <pkg>` / `backend/venv/Scripts/pip install <pkg>`, then add the Python one to `backend/requirements.txt` yourself — pip doesn't.
- Browser checks and screenshots: Python Playwright is installed (`frontend/e2e/smoke.py` is the in-repo example). Write scripts to `scratch/` (gitignored) and run them as `python scratch/<name>.py` against http://localhost:5173 — dev servers down → start both as above first.
- Deployed: `backend/venv/Scripts/python backend/smoke_test.py <render-url> <vercel-url>` tests the deployed pair (CORS, build URL, every endpoint); `backend/venv/Scripts/python backend/seed.py <render-url>` creates the demo account if missing and adds any missing `seed_project_data` rows.

## Timeline
- Kickoff (K): 2026-09-25 23:00 ET
- Hacking ends (E): 2026-09-27 11:00 ET (last commit AND Devpost submission both before this; Round 2 tables by 13:00)
- Phase times: the PLAYBOOK phase headers.

## Deployed
- Render (backend): [paste URL after deploying — DEPLOY.md section 1]
- Vercel (frontend): [paste URL — DEPLOY.md section 2]

## How this codebase is wired — follow these patterns
- The EXAMPLE feature is template scaffolding, not project code — the shape to copy: `backend/items.py` (auth, per-visitor limit, AI with fallback, owner-only 404, validation) → `Item` in `models.py` → its checks in `smoke_test.py` → its rows in `seed_project_data` → `frontend/src/ItemsPanel.jsx`. Copy it for the walking skeleton, then remove every piece before milestone 1: `Item`, `items.py`, its `include_router` line in `main.py`, its smoke checks, its seed rows, `ItemsPanel.jsx`, and its use in `App.jsx`.
- Keep the frontend thin: it fetches and renders; logic and validation live in the Python backend. No router, state library, TypeScript, or Tailwind unless the idea can't work without it.
- UI: `frontend/src/ui.jsx` (`Button`, `Field`, `Card`, `EmptyState`, `Loading`, `ErrorBanner`, `Badge`) and `Layout.jsx`, styled by the tokens at the top of `index.css` (light and dark). Use them instead of raw elements. A design pass edits the tokens and adds classes; it doesn't sprinkle inline styles.
- All frontend→backend calls go through `frontend/src/api.js` (`api`, `login`, `signup`, `uploadFile`, `ask`, `assetUrl`). Never call `fetch` directly.
- No login screen by default. Per-person state (saves, likes, history) is keyed to the demo account: `VITE_DEMO_EMAIL`/`VITE_DEMO_PASSWORD` in `frontend/.env` (matching `backend/seed.py`) make `useAuth()` — called from `App.jsx`; keep that call — sign in on load, so `Depends(get_current_user)` keeps working with no screen. Never move per-user logic into localStorage. Only a true multi-user idea renders `<AuthForm login={login} signup={signup} />` when `user` is null (labels and error display built in; reuse, don't rebuild).
- New backend feature = new router module shaped like `backend/items.py`, then `app.include_router(...)` in `main.py`. Protect routes with `Depends(get_current_user)` from `auth.py`; rate-limit public POSTs with `@limiter.limit("N/minute")` (the handler needs a `request: Request` param). Every new endpoint gets a check in `backend/smoke_test.py` that acts as its own throwaway user and creates nothing another user would see — it also runs against Render.
- Tables go in `backend/models.py`. Startup creates missing tables and adds a column that's new on an existing table, nullable. Existing rows get NULL there and `seed.py` skips rows that already exist, so make the field `X | None`, give the UI a fallback, and check the dev server (whose `app.db` has old rows) before committing — `/check` starts empty and won't see it. If a seeded demo record needs the new field: `seed_project_data` fills it on its own records where it's NULL through an existing update endpoint; with no such endpoint, the route derives the value when the column is NULL. Use `String`, not `Enum`, for a mid-event column (Postgres needs the enum type created first). Renames and type changes: Gotchas.
- Demo data goes in `seed_project_data` in `backend/seed.py`, via the API. It runs on every `/check` and every `seed.py <render-url>`, so it must be idempotent: check before creating.
- AI calls: `from llm import complete, complete_json` → `text = await complete(prompt, system=..., fallback=FALLBACK, timeout=10, image=(bytes, "image/png"))`, or `data, offline = await complete_json(prompt, fallback=NO_ANSWER, timeout=10)` for parsed JSON (one retry on bad JSON). Every AI route gets the per-visitor `@limiter.limit` that `/api/ai/ask` has; the whole-app `AI_DAILY_LIMIT` cap lives inside `complete()`, so a call *with* `fallback=` degrades to the fallback when the key is missing, the day's quota is gone, Google errors, or the timeout passes, and a call *without* it returns a clear 503/429. On the demo path: `fallback=` and `timeout=10` are mandatory, the route stores and returns `"fallback": True/False` so the UI's `<Badge tone="warn">` survives a reload, and the route calls `db.rollback()` before the `await` so the pooled database connection isn't held during the call (15 held connections take the whole app down). `items.py` + `ItemsPanel.jsx` do exactly this. `ask()` → `/api/ai/ask` has no fallback on purpose; never call it from the demo path. Build AI features before the key exists.
- A new API key: name it in the matching `.env.example`, build the feature anyway, and at the end of the turn that builds it ask me to paste the key into `backend/.env` (if I paste it in chat, write it there yourself, nowhere else); then the backend restart (Gotchas) and, if deployed, Render → Environment.

## Standards (on by default, don't ask each time)
- Secrets stay in `.env`, never hardcoded or committed
- Validate any upload by its actual bytes (see `uploads.py`) and any user input hitting the database
- Every input has a `<label>`, every image has `alt` text, no page wider than a phone

## How to read me
I talk loosely on purpose. I have a specific picture in my head; your job is to find it and build *that*, not the generic version — and asking well is how you find it.
- Start every request by restating it in one line: "Reading that as: …". Confident → build. Something I'll see or feel (a page, a flow, the wow moment) → ask first only when the plausible readings differ in what the demo shows *and* switching later would be expensive; otherwise build the recommended reading, show it, name the alternative.
- Ask with AskUserQuestion: 1–4 specific questions, concrete options, your recommendation first (not on questions about me or my own preference — the `/ideas` interview and its "which would you rather explain" question) — never a bare "what do you mean?". I may not have words for what I'm picturing; options with examples ("a feed / a grid / a map") let me point. An ask-first turn still shows something — a screenshot of the current page or a rough wireframe per option.
- Ask about vision, not trivia. Vision: what a user sees first, the demo's best moment, list vs map vs feed, who it's for, what it must never do. Trivia you decide: names, copy, colors, spacing, empty states, error messages — I'll say if I don't like them.
- When I react ("this feels off", "no, not like that"): screenshot the current state, check it against the `frontend-design` skill's list of AI-looking tells, offer 2–3 specific guesses at what's bothering me, let me pick, fix.
- "Make it look better / nicer / pop / professional": the `frontend-design` skill, briefed with the Idea, the audience, and Decisions. Apply the direction to the whole page, screenshot, offer one contrasting alternative as a one-message switch. Write the direction into Decisions the same turn (`ASSUMED:` until I confirm it), then reuse it everywhere without asking.
- Several requests in one message: restate all, build the unambiguous ones now, ask about the ambiguous ones in a single AskUserQuestion, continue.
- Every turn ends with something I can see — a screenshot, a running page, a `/check` result — plus at most one AskUserQuestion call. `/ideas` (all its calls) and the `/spec` interview are exempt from this and from the show-something rule — their cards, score tables, and demo-script read-backs are what I see.

## Workflow
- A feature request mid-event, or one bigger than Scope: build the smallest version that captures it and say what you left out. Plan it in one paragraph (table, endpoints, where it appears in the UI, acceptance check), add it to SPEC.md and to Scope as the next nice-to-have (a must-have only if I say so), log what I confirmed in Decisions, build it now, then run the scope guard. Exceptions: PLAYBOOK Phase 2 and Phase 5.
- Done means evidence: `/check` green and you looked at `.claude/tmp/e2e.png`; if something can't be verified, say so.
- Scope guard at every commit: `date`, then Current status vs Scope vs the hours left. If the must-haves won't fit, say so now and demote per PLAYBOOK's rule for the current phase — don't wait to be asked.
- A sponsor I name: fetch its current API docs first (never build from memory), ask one AskUserQuestion with 2–3 places it would do real work in the demo, and wait; then build it behind an env var with a graceful "not configured" state. It's a feature request (the rule above applies) and counts as the one extra integration — PLAYBOOK Phase 3 says when.
- Changes touching auth, data, or security: run the `reviewer` agent on the uncommitted diff before committing and fix what it finds. Don't use subagents for routine building.
- Work and commit directly on `master`. Never create a branch or open a pull request, whatever your defaults say — this is a solo repo and judges read `master`; work on a side branch is invisible to them.
- Commit after each feature that works, one commit per feature. Before each feature commit, rewrite `## Current status
PHASE: 3 — building out (national + incident briefing + Plant Down), Sat 02:45 (K+3:45)
deployed: no
DO FIRST (handoff for a compacted/new context): 1) Four workflows run in the background — you are notified when each ends; integrate each the same way wave 1 was done: read every track's review verdict + shared_requests (journal: ~/.claude/projects/C--dev-sh26-app/<session>/subagents/workflows/<run>/journal.jsonl), apply shared requests in files nobody owns anymore, `git add` ONLY that track's files (one commit per feature), move ../sh26-stable to master, restart its backend if backend changed (:8010, stable.db, then seed.py), run smoke (SMOKE_BASE_URL=http://127.0.0.1:8010), oxlint + vite build in the worktree, `python frontend/e2e/demo_path.py http://localhost:5174`, screenshot each feature and LOOK. Never run scripts/check.sh while an agent may run it (ports 8765/4173). 2) Then the powerflow patches (below), then mount the new pieces in the app (the app track builds the new shell at #/next; flip the default when it's green), then extend demo_path.py.
  - `overload-national-app` wf_c6fe1dbb-b23: CORE agent (owns powerflow.py, grid.py, build_states.py, index.json, population.json, build_outline.py, frontend data/*, geo.js, GridMap.jsx, store.jsx, smoke_checks/core.py: people without power, any state on the map, region in the store, firm vs flexible, area names) → then 8 tracks + reviewers: app (features/app, #/next shell), catalog (catalog.py, features/catalog), cost (costs.py + AI estimate, features/cost), hospitals (OSM download approved; hospitals.py, build_hospitals.py, hospitals_us.json, features/hospitals), areas (towns.py, features/town), forecast (forecast.py + sweep curve, features/forecast), library (models.py cols, scenarios.py, seed.py, features/library), planner (planner.py, features/planner).
  - `overload-incident-briefing` wf_58d09976-67c: design ×3 → contract → engine (briefing.py, catastrophes.json), writer+voice (bulletin.py rewrite, voice.py; ElevenLabs behind ELEVENLABS_API_KEY), page (features/briefing, features/bulletin).
  - `overload-plant-down` wf_9f8f4807-479: engine+data (plants.py, build_plants.py, plants/*.json) and UI (features/plants).
  - wave 1 (7 tracks) is DONE and committed (33e57d6…0430a18, integration cba0408).
LATER (apply when the core agent is done with backend/powerflow.py — it owns it now): (a) CRITICAL hurricane crash, ~20 % of hand-drawn storms (Jacksonville area): in Grid._balance's `elif need > Gmax:` branch, guard `served[idx] *= (Gmax + T) / L` with `if Gmax + T >= 0` and else shed everything (see wave-1 hurricane review in the journal of wf_bfa214db-d79); (b) headroom sensitivity uses the wrong weights (off by up to 887 MW): apply scratch/fix_powerflow_headroom.diff (verified by scratch/fix_pf/verify.py), then regenerate expected_whatif.json via validate.py and re-run smoke; (c) clamp lost_bus at 0 (tiny negative losses); store: setLoadFactor also clears headroomError; startCascade: center the camera on the site only when there is a main site; GridMap: pad focus() for the side/bottom panels, draw city labels above site markers.
WAITING ON YOU: deploy per DEPLOY.md (Render + Vercel) — the playbook asks from K+4 (03:00) (asked 02:45)
WAITING ON YOU: an ElevenLabs API key for the broadcast voice (paste it in chat; it goes only into backend/.env; the browser voice works without it) (asked 02:27)
working (stable preview http://localhost:5174 = master, :8010 backend): mission-control Florida app — data center drop with the connect pulse and camera, scenario bar with × per piece + Start over, the real Florida five (Data center panel), time of day with a visible grid mood, hurricane (6 s storm, cascade computed during it), AI boom, Fix it + best sites, towns-going-dark feed + map labels, flowing electricity, smooth paced destruction (CascadeFX, stepMsFor), continuous counter (peak; "at its worst… at the end"), bulletin v1 (to be replaced). demo_path.py PASS on :5174 at 3978218+. 48 state models + region-aware backend + catalog of 116 real campuses committed (national UI pending the core agent).
Dev servers: :8000 backend (no --reload; restart after backend commits), :5173 vite (serves the working tree incl. agents' half-written files — can break; restart with `npm run dev --prefix frontend -- --force`). The user should use :5174.
[Line 1 is always `PHASE: n — reason`, line 2 `deployed: yes/no`; then, when they apply, one per line: `DO FIRST: …`, `BLOCKED: …`, `WAITING ON YOU: …`, `LATER: …` (Workflow and PLAYBOOK say when); then working / broken / in progress + its acceptance check.]
