# Project: [NAME]

You're a great engineer and this team is lucky to have you on this build — let's ship something judges remember.

## Idea
[One paragraph: what it does, who it's for, why it's interesting to a judge in 30 seconds.]

## Sponsor / company challenges to target
[Pasted at Phase 0 — name, sponsor, their actual eligibility requirement. `/ideas` and `/spec` mark the targeted ones `← claimed`; the rest stay for a runner-up switch.]
- [ ]

## Stack
FastAPI (backend; SQLite locally, Postgres on Render), React + Vite (frontend), Render + Vercel (deploy) — change only if the idea truly needs something else.

## Scope (hackathon-realistic)
Must have (demo breaks without these — tick each in the commit that finishes it):
- [ ]

Nice to have (build top-down; cut bottom-up):
- [ ]

Explicitly NOT doing:
- [ ]

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
- Kickoff (K): [fill at kickoff, e.g. 2026-09-25 19:00 local]
- Hacking ends (E): [the 2026 Hacker Guide says Sunday 2026-09-27 11:00 ET, submissions close at the same time — confirm at kickoff]
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
- Commit after each feature that works, one commit per feature. Before each feature commit, rewrite `## Current status` and append new confirmed answers to `## Decisions`, so they land in that commit. After each commit, `git status -sb`; at `ahead 2` or more, `git push` (until PLAYBOOK Phase 5 says stop).
- I say judging starts within 3 hours: PLAYBOOK Phase 5.
- The same fix has failed twice: stop, say so, write `DO FIRST: <the next thing to try> — tried: …` into `## Current status`, commit it, then ask me to `/clear` — and say if a bigger `/model` is worth it. (Alone: PLAYBOOK → Away mode.)
- When compacting context, preserve: the feature in progress and its acceptance check, working vs broken, files changed since the last commit, any command that failed and why, and whether Away mode is on (human asleep since HH:MM).

## Budget
Prompts may arrive with "Budget right now: …". 5-hour past 85% or weekly past 70% → say so when it first crosses (and again after a `/clear`, not every turn), with the reset time when the hook gives one, before the turn's work; a limit about to hit mid-feature → commit and push what's safe first.

## Gotchas
- Upload URLs go through `assetUrl()` in the frontend, never a hardcoded host.
- Vite restarts itself when `frontend/.env` changes (reload the page). `backend/.env` is read once at startup — a change needs a backend restart, and the dev server runs in my window: ask me.
- Keep rate limits ≥ 30/minute (the whole venue shares one IP).
- A renamed column or changed type isn't migrated. Locally: ask me to close the backend window, then `rm backend/app.db`, ask me to rerun `.\dev.ps1`, then `backend/venv/Scripts/python backend/seed.py`. Deployed, only an *added* column is handled — so add a new column instead of renaming, and alone (Away mode) never rename or retype.
- `git restore .` / `git checkout .` / `git reset --hard` are denied. To drop uncommitted work, ask me to rewind (Esc Esc) or run it myself.
- [add project-specific gotchas here as you hit them]

## Decisions
[Confirmed answers about how the app looks and behaves — layout, the wow moment, design direction, what it must never do. Append, don't rewrite — except the `RUNNER-UP`, `Facts:`, and `OVERRIDE` lines, which `/ideas` writes or replaces (a switch to the runner-up deletes `RUNNER-UP`).]

## Current status
PHASE: 0 — repo just generated, SPEC.md doesn't exist yet
deployed: no
[Line 1 is always `PHASE: n — reason`, line 2 `deployed: yes/no`; then, when they apply, one per line: `DO FIRST: …`, `BLOCKED: …`, `WAITING ON YOU: …`, `LATER: …` (Workflow and PLAYBOOK say when); then working / broken / in progress + its acceptance check.]
