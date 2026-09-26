# Shellhacks starter

**This is a template, not a project.** It holds plumbing (auth, uploads, deploy, checks, an LLM helper), a small UI kit, and one clearly-labelled example feature that exists to be copied and then deleted. The hackathon project is generated from it at kickoff and built from that point on; the first commit after generation is "Start ShellHacks project".

FastAPI backend (SQLite locally, Postgres on Render — uploads live in the database too, so nothing is lost on deploys) + React (Vite) frontend with a small UI kit and one example feature, wired together and verified working end to end, plus a `.claude/` setup that makes Claude Code faster and more reliable on this repo.
At kickoff, generate the project's repo from this template, run `/ideas` (no idea yet, or candidates to score) and `/spec` in Claude Code, and start building features instead of plumbing.

> **"Running scripts is disabled on this system"?** Fresh Windows blocks `.ps1` files. Run any of this repo's scripts as `powershell -ExecutionPolicy Bypass -File .\script.ps1`, or allow them for your account once with `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.

## One-time setup (PowerShell, from the repo root)

```
cd backend
python -m venv venv
venv\Scripts\pip install -r requirements.txt
copy .env.example .env
cd ..\frontend
npm install
cd ..
```

Then open `backend\.env` and set `JWT_SECRET` (generate one with `python -c "import secrets; print(secrets.token_hex(32))"`). Add `GEMINI_API_KEY` there too if the idea uses AI (free key: aistudio.google.com/apikey).

Optional, for photos: copy `.env.example` (repo root) to `.env` and add a free Unsplash access key.

Optional, for features that need a "who" without a login screen (saves, likes, history): copy `frontend\.env.example` to `frontend\.env` and uncomment `VITE_DEMO_EMAIL`/`VITE_DEMO_PASSWORD` — `useAuth()` (called from `App.jsx`) then signs in as the seeded demo account on load.

New machine with nothing installed? `powershell -ExecutionPolicy Bypass -File .\setup-machine.ps1` installs git, GitHub CLI, Node, Python, and prints the rest.

## Run it

From PowerShell (VS Code's terminal is fine):

```
.\dev.ps1
```

Opens the backend (auto-reloads when files change) and frontend in two windows. Open http://localhost:5173 — it should show "Backend status: ok".

Demo account and demo data (recreate any time, locally or on Render): `backend\venv\Scripts\python backend\seed.py [render-url]`.

## Check that it actually works

```
.\check.ps1
```

(From Git Bash or inside Claude Code: `bash scripts/check.sh` — same thing.) Lints and builds the frontend, starts a throwaway backend, runs the smoke test (health, signup, auth, validation, uploads, AI status/auth), then opens the built app in headless Chromium and checks it rendered, didn't crash, logged no errors, has alt text and labels everywhere, and fits a phone screen (needs `python -m pip install playwright` + `python -m playwright install chromium`; skipped otherwise). Prints `ALL CHECKS PASSED` or names what failed, and saves a screenshot to `.claude/tmp/e2e.png`. CI runs the same script on every push.

## The Claude Code setup (CLAUDE.md, PLAYBOOK.md, `.claude/`, `scripts/` — picked up automatically)

| | What it does |
|---|---|
| `CLAUDE.md` | Standing orders: commands, codebase patterns, how to interpret loose requests, gotchas, and the two sections Claude keeps updated (Decisions, Current status) |
| `PLAYBOOK.md` | What Claude does in each phase of the event — sanity, spec, walking skeleton, build-out, away mode, polish, freeze and ship, recovery |
| `/ideas` | No idea yet, or candidates to score: interviews you, generates four candidates from your answers, scores yours and its own on the judging criteria and sponsor fit, kills the weak ones with evidence, helps you pick |
| `/spec <idea>` | Interviews you about your idea and writes SPEC.md (never suggests its own ideas) |
| `/check` | Runs `scripts/check.sh` and reports with evidence |
| `images` skill | When a page is done, Claude finds its photo spots, looks at Unsplash options, places its best picks with credit, then asks you once to confirm or swap. Also runs on `/images` |
| `/pitch` | Drafts the 30-second pitch, the 3-minute presentation script (also the video narration), Devpost writeup, disclosure line, judge Q&A, screenshots, and the project README from what actually got built |
| `/ship-check` | Pre-demo readiness check, including the ShellHacks disclosure rule and a secrets scan (run after `/pitch`) |
| `/debug` | Reproduce → read the real error (server log, browser console) → fix the root cause → add a check. No guessing first |
| `reviewer` agent | Fresh-eyes review of a diff before commit — used for auth/data/security changes |
| Edit hook | After every edit, lint-checks JS/JSX and syntax-checks Python so broken code is caught immediately |
| Push reminder | When Claude finishes a turn with 2+ unpushed commits, it says so — pushes are your timestamped proof of event-time work and your backup if the machine dies |
| Permissions | Routine commands (npm scripts, checks, the project's python scripts, git status/diff/commit/push) and file edits inside the repo run without asking; force-push, hard reset, `checkout .`, `clean` are denied |
| Plugins | `frontend-design` (visual polish) and `example-skills` (incl. `webapp-testing`) are registered in `.claude/settings.json`, so a fresh machine installs them on first open |
| Status line | Bottom of the Claude Code window: model, context-usage bar, 5-hour limit, branch with unpushed count |
| Time and budget awareness | Every prompt carries the current time (the playbook's phase rules are clock-based) and, when the status line refreshed in the last 20 minutes, the 5-hour and weekly usage; Claude warns when 85% of the 5-hour or 70% of the weekly limit is first crossed |

Also in the app:
- `backend/llm.py` — a ready LLM helper (`await complete(prompt, fallback=...)`, `complete_json(...)` for parsed JSON; Gemini free tier) with an example authenticated route. Without a key it returns a clear 503 — or the fallback you passed — so AI features can be built before the key exists. A whole-app daily cap protects the free quota.
- An **example feature** to copy for the first real one: `backend/items.py` (list/create/delete, auth, rate limit, AI-suggested tags that fall back gracefully) with its table, smoke checks, seed rows, and `frontend/src/ItemsPanel.jsx`. Delete or rename it once your own feature exists. `frontend/e2e/demo_path.example.py` shows the demo-path check Claude writes for your idea.
- A small **UI kit** (`frontend/src/ui.jsx`: Button, Field, Card, EmptyState, Loading, ErrorBanner, Badge) and page shell (`Layout.jsx`), themed by the tokens at the top of `index.css` — light and dark, accessible by default.

## More

- Deploying: [DEPLOY.md](./DEPLOY.md)
- Kickoff-day runbook: [KICKOFF_CHECKLIST.md](./KICKOFF_CHECKLIST.md)
