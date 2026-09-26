# Playbook — what shcht does at each phase of the event

For the Claude Code session building this project. CLAUDE.md holds the rules for every turn; this file holds what changes by phase. Times are relative to kickoff (K) and hacking end (E), from CLAUDE.md → Timeline; the clock is defined in CLAUDE.md → Phases.

## Knowing where we are (first thing, every session)

1. `date`. Read CLAUDE.md → Timeline and Current status (`PHASE`, `deployed`, and any `DO FIRST` / `BLOCKED` / `WAITING ON YOU` lines), and SPEC.md if it exists.
2. Timeline still has placeholders: write K = the earliest HH:MM already stamped in CLAUDE.md this session, else the first `Now:` line, and E = the date in the E placeholder, both marked `(ASSUMED — correct me)`, say so in one line, and continue; the human corrects them if wrong. Never spend a question on it.
3. Say it in one line: "Phase 3 — hour 14 of 36, milestone 1 done, 2 of 4 must-haves ticked, deployed: yes" — then repeat any `DO FIRST` / `WAITING ON YOU` / `BLOCKED` lines verbatim. Drop a `DO FIRST` line once the human says it's done.
4. When a phase's exit condition is met, write the new `PHASE:` line into Current status and announce it. A phase is only real once it's written.

## Phase 0 — Sanity (K+0:00 → K+0:15)

Entry: SPEC.md does not exist.
- Confirm setup: `backend/venv`, `backend/.env` with `JWT_SECRET` and `GEMINI_API_KEY` (always — candidate ideas may use it), `frontend/node_modules`, and `frontend/.env` with the two `VITE_DEMO_*` lines uncommented (not a secret — copy `frontend/.env.example` and create it yourself). Missing pieces: `python -m venv backend/venv`, `backend/venv/Scripts/pip install -r backend/requirements.txt`, `npm install --prefix frontend`; the human creates `backend/.env` and pastes keys.
- `/check` must end `ALL CHECKS PASSED` with no `SKIPPED` browser section. If SKIPPED: `python -m pip install playwright && python -m playwright install chromium`; if the download fails on venue wifi, ask the human to switch to the phone hotspot and retry now — Phase 0 doesn't exit with SKIPPED.
- CLAUDE.md → "Sponsor / company challenges to target" still a placeholder: ask the human for the list in plain text and paste it in. If it isn't out yet, write `not released yet (HH:MM)` under the heading and continue; paste it the moment they have it.
Exit: check green, sponsors written; write `PHASE: 1 — setup green, no spec yet`. Already inside `/ideas` or `/spec` → continue it; otherwise say: switch `/model` to the bigger model, then type `/ideas` if there's no idea yet, or `/spec <idea>` if there is.

## Phase 1 — Idea and spec (K+0:15 → K+1:00)

`/ideas` (only when the human has no idea, or wants their candidates scored against the sponsor list; K+0:15 → 0:40) then `/spec` (K+0:40 → 1:00). Both are typed by the human; everything they do is in the skills. Exit: `/spec`'s commit and hand-off are done.

## Phase 2 — Walking skeleton (K+1:00 → K+10)

Entry: `PHASE: 2`.
- The walking skeleton is the ugliest possible version of the exact demo path working end to end, built by copying the example feature (CLAUDE.md → How this codebase is wired): tables → endpoints → smoke checks → seed rows → the screens a judge sees, on the UI kit's defaults. Nothing else — no design pass, no nice-to-haves.
- As soon as the path works once, write `frontend/e2e/demo_path.py` (start from `frontend/e2e/demo_path.example.py`): URL as `sys.argv[1]`, exits non-zero on any failed step; it replays SPEC.md's demo script and asserts what a judge sees at each step. It must also be safe to run against the deployed app: create nothing a judge would see, or clean up after itself. `/check` runs it from then on against a throwaway backend holding `smoke_test.py`'s rows plus `seed_project_data`'s.
- The idea's core piece proves impossible — not merely hard — before K+4: say so and ask in one AskUserQuestion (Away mode: a `WAITING ON YOU` line): switch to the `RUNNER-UP` line from CLAUDE.md → Decisions · keep it and cut the core to <named smaller version>; no `RUNNER-UP` line → cut scope, no question. Switch accepted: in one `git rm` call (one prompt; the human is present) delete SPEC.md, the idea's routers, its screens, and `frontend/e2e/demo_path.py`; by Edit remove its `include_router` line, models, smoke checks, `seed_project_data` rows, and its use in `App.jsx` (git history keeps it all); commit `Abandon <idea>`; replace Idea and `# Project:` with the runner-up's, delete that line, append `— switched to runner-up at HH:MM; Decisions above, except Facts:, are for <old idea> only —` to Decisions, write `PHASE: 1 — switching to runner-up`; tell the human: `/clear`, `/model` to the bigger model, then `/spec`. An idea built against an `OVERRIDE` line in Decisions gets the same question once, at or after K+4, whether or not anything is impossible — options: switch to the runner-up · keep as is · keep and cut the core to <smaller version> — then append ` (asked HH:MM: <answer>)` to the OVERRIDE line; a line carrying "(asked" is never asked again. That answer may still switch. Otherwise, after K+4 never switch ideas; cut scope instead.
- Request outside the demo path: "skeleton isn't done yet — X still fails; the smallest piece that serves it is Y" and offer Y. If they accept waiting ("later"): add it to SPEC.md and CLAUDE.md → Scope nice-to-haves now, build nothing. If they insist: push back once, never twice, then build it per CLAUDE.md → Workflow's feature-request rule, as its own commit after the current skeleton step is committed.
Exit (milestone 1): `/check` green including `demo_path.py`, the template's EXAMPLE feature removed (every piece CLAUDE.md lists — the project must contain only its own code), and the human clicked through the demo on localhost. Write `PHASE: 3 — milestone 1 done at hour N`.
Cut rule — past K+10 and not there, or earlier when CLAUDE.md's scope guard says the skeleton won't make K+10: cut the must-have whose demo step is last in the script and furthest from the wow moment — shorten SPEC.md's demo script and `demo_path.py` to match, move it to the top of nice-to-haves, say "Cutting X: it's step 5 of 5, the wow moment is step 3" — repeat until green. In Away mode, don't cut: write `WAITING ON YOU: cut decision (skeleton not green at K+10)` and keep building.

## Deploy

Deploy is the human's task (DEPLOY.md), never a gate: you build, they deploy. First turn after K+4 with `deployed: no`: write `WAITING ON YOU: deploy per DEPLOY.md (asked HH:MM)` in Current status and say it once. From K+10, whenever that timestamp is an hour or more old, say it again (first in the turn's AskUserQuestion if there is one) and update the timestamp — in Away mode leave the timestamp, the human hasn't seen it. When they give both URLs (in chat or in CLAUDE.md): write them under Deployed, flip `deployed: yes`, drop the line, commit CLAUDE.md. "Deployed" / "it's up" counts as Render green. The deployed checks — `smoke_test.py <render-url> <vercel-url>`, then `seed.py <render-url>` (every time) — run only after the human says the deploys are green. Green means: Vercel for the latest commit, Render for the last commit that touched `backend/` (`render.yaml` sets `rootDir: backend`, so other pushes don't redeploy it). You can't see either dashboard, and before green you're testing the old build.

## Phase 3 — Build-out (milestone 1 → K+24)

Entry: `PHASE: 3`.
- Must-haves in SPEC.md order; extend `demo_path.py` when the must-have is on the demo path.
- When CLAUDE.md's scope guard says they don't fit: "At this pace feature 4 won't make it; dropping it to nice-to-have — say no to keep it," then proceed with the recommendation unless told otherwise.
- The one extra sponsor integration (SPEC.md names it, or the human names one mid-event): only after every must-have is ticked; `demo_path.py` must pass with it disabled.
Exit: every must-have ticked and green in `/check`. Write `PHASE: 4 — all must-haves done at hour N`. If K+24 arrives first: freeze the must-have list to what's green, demote the rest with Phase 2's cut rule, write `PHASE: 4` anyway.

## Away mode (the human says "keep going, I'm going to sleep")

Any phase. Before they leave: run `git push` once so you know it works without a prompt, and finish-and-commit or WIP-and-revert (Recovery) anything uncommitted. Then keep working inside this same turn — once you end it, nothing runs until they're back — until the must-haves are exhausted or all BLOCKED:
- Phase 2: the skeleton steps in order, then `demo_path.py`; once green, write `WAITING ON YOU: click through the demo on localhost (milestone 1)` and continue with must-haves in SPEC.md order without writing `PHASE: 3`. Phase 3+: must-haves in SPEC.md order. `git push` and `date` after each commit. A failed push: one retry, then keep committing locally, write `WAITING ON YOU: push failing since HH:MM (<first error line>)`, retry after each later commit, drop the line when one succeeds.
- Never call AskUserQuestion — it blocks until they return. Where CLAUDE.md → How to read me would ask first: a `WAITING ON YOU: …` line, then the next must-have. Where it would build the recommendation: do that and log `ASSUMED: …` in Decisions.
- A fix that fails twice: WIP-and-revert it (Recovery) with the message `WIP (blocked): X`, write `BLOCKED: X — attempt in commit <the short sha git commit printed>` in Current status, `git commit -m "Status: X blocked" CLAUDE.md`, `git push`, move on. Never loop on it, never leave a red diff in the tree.
- Between must-haves, Read `.claude/tmp/usage.json` (the Budget hook can't reach you mid-turn): `five_hour.used_percentage` ≥ 90 → go straight to the last step. If the file is missing or older than 20 minutes (only the terminal UI's status line writes it), use the `get_usage` tool if you have one; otherwise skip the check and put `no usage data` in Current status — every must-have is already committed and pushed, so a limit hit loses nothing.
- Must-haves exhausted: in Phase 4, do Phase 4's empty/loading/error states and 375px fixes on the demo path first. Then `/check`, update status, commit CLAUDE.md, `git push`, and end the turn with the one-line status (Knowing where we are step 3) and every `WAITING ON YOU` / `BLOCKED` / `ASSUMED` line verbatim. No nice-to-haves, no design direction, no sponsor integration alone.

## Phase 4 — Polish (K+24 → E−3)

Entry: `PHASE: 4`.
- One `frontend-design` pass over the demo path (CLAUDE.md → How to read me says how).
- Empty, loading, and error states on the demo path, in the interface's voice.
- Mobile: screenshot the demo path at 375px.
- Nice-to-haves top-down. If the demo path goes red while building one: one fix attempt, then WIP-and-revert it (Recovery) with the message `WIP (dropped): <name>` and note `BLOCKED: <name> (nice-to-have) — attempt in <sha>` — don't debug a nice-to-have.
- After each push that touches the demo path: the deployed checks (Deploy).
Exit: E−3 by the Timeline. Write `PHASE: 5 — feature freeze`, then announce it.

## Phase 5 — Freeze and ship (E−3 → E)

Entry: `PHASE: 5`, or the human says judging starts within 3 hours (CLAUDE.md → Workflow) — write it first.
- Feature = new endpoint, table, screen, or `demo_path.py` step. Copy, CSS, empty/error states on existing screens, and prompt changes are fixes. Features are refused without negotiation: "Frozen — added as `LATER: …` in Current status." A prompt change is verified with a `scratch/` script calling the dev server's route on the seeded demo inputs (10 calls at most — it spends the daily AI quota), shown as before/after answers; two failed attempts → keep the old prompt and give `/pitch` a `Backup:` note.
- Every turn: `/check` + `git status -sb`, then exactly one next action, in this priority: broken → unpushed (until E−1:30) → pitch (by E−1:30 at the latest, whatever the deploy state; a broken step becomes a `Backup:` note) → undeployed (`deployed: no`, or the deployed checks fail after the deploys are green). The human's order: `/pitch`, record and upload the video, `/ship-check` (pitch first — it rewrites the README that ship-check verifies; you can't run any of the three).
- Final code push by E−1:30 (venue wifi is slow; the phone hotspot is the fallback). When the deploys are green (Deploy says what green means): the deployed checks, then `python frontend/e2e/smoke.py <vercel-url>` and `python frontend/e2e/demo_path.py <vercel-url>`. Not green by E−0:45: stop pushing code; run `python frontend/e2e/demo_path.py <vercel-url>` — passes → the deployed URL is a valid backup for judges who click the link, fails → tell the human to run `/pitch Backup: deployed URL is down — say so if a judge asks for the link`. The `/pitch` commit is the only push allowed after E−0:45, never within 15 minutes of a demo; once Vercel is green, rerun `python frontend/e2e/smoke.py <vercel-url>`.
- The 3-minute demo video is required on Devpost. The human records it, narrating `/pitch`'s presentation script over the demo path on localhost, by E−1:00; your part is a green `/check` and seeded demo data before they start, and no code changes while they record. Video uploaded and the Devpost project **submitted** (Submit clicked, not a draft) by E−0:20 — at E−0:30, if `/ship-check` hasn't confirmed both, say so before anything else.
- E−0:30: stop touching code and say so. A demo-path bug found after that is routed around, not fixed: the human reruns `/pitch` with a note naming the seeded record to open instead. After E: no commits or pushes; a `/pitch` rerun only edits PITCH.md, uncommitted.
Exit: `/ship-check` green, PITCH.md exists, the video is uploaded, Devpost submitted, the human rehearsed the 3-minute presentation once with a timer.

## Recovery (any phase)

- **Broken:** `/debug`. **Same fix failed twice:** CLAUDE.md → Workflow (human present) or Away mode (alone).
- **WIP-and-revert X** (keeps the attempt in history, leaves the tree green, needs no human): `git add` its files — not CLAUDE.md — then `git commit -m "<message>"` and `git revert --no-edit HEAD`.
- **Uncommitted breakage:** CLAUDE.md → Gotchas. **Committed breakage:** `git revert --no-edit <sha>`.
- **`/check` red after a big change:** fix in order lint → build → backend smoke → browser check → demo path.
- **Session lost / reboot:** the human runs `claude --continue`; your first turn is Knowing where we are plus `git status` and `git diff --stat` (uncommitted work: finish it, `/check`, commit); the dev servers are dead — tell them `.\dev.ps1`.
- **Deployed app misbehaves:** `smoke_test.py <render-url> <vercel-url>` names the culprit (CORS / wrong build URL / backend); `db-error` from `/api/health` = the Postgres link; demo data missing = new database → `seed.py <render-url>`.
- **An AI route says "Too many requests" or "AI quota for today is used up":** our own limits (30/minute per visitor, or `AI_DAILY_LIMIT` — in-memory, so restarting the backend resets it; raising it is the human's: `backend/.env` locally, Render's env tab deployed).
- **AI answers wearing the fallback badge:** `backend/server.log` has a `WARNING … AI fallback used: …` line saying why (deployed: ask the human for the line from Render → Logs). `AI quota for today is used up` = our own cap (line above). `AI request failed (429)` = Google's free quota: say the reset time (midnight Pacific), stop exercising AI routes on the dev server, ask the human to lower `AI_DAILY_LIMIT` (where: the line above); a second key is the human's call. Demoing on the fallback is fine if the badge is visible.
- **Usage limit warning:** CLAUDE.md → Budget. **Laptop dead:** the human clones onto another machine (`setup-machine.ps1`), re-enters `.env` keys, runs README setup; you resume from the repo — only unpushed work is lost.
