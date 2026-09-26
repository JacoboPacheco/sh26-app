# Kickoff day runbook

**ShellHacks 2026: Sept 25–27, FIU Graham Center (Modesto Maidique Campus).** Check-in Friday 3–6 pm (physical ID matching your application + the QR code from your Hacker Dashboard). **Hacking and Devpost submissions close Sunday 11:00 am ET.** Judging criteria, equally weighted: Completion, Originality, Design, Technology, Practicality. Judging is in person: a **3-minute presentation to one judge** (Round 1), then top projects get a 3–5 minute Round 2 — be at your assigned table by 1 pm. The Devpost submission needs your GitHub repo link, at least one Discord tag, the sponsor challenges you opt into, and a **required 3-minute demo video**. The Devpost page appears the day before the event — re-read its Rules tab then. Everything runs on the laptop; git push is the backup.

## Before the event (tonight, in this order — then sleep)
- [ ] **Have an idea, or plan to generate one.** Write down up to two candidates, one sentence each, plus the one moment that makes a judge react. At kickoff, once the sponsor challenges are pasted in, `/ideas` scores them on the five judging criteria alongside four it generates, then helps you pick. Decide tonight if you can; the sponsor list only adjusts an idea, it doesn't replace having one.
- [ ] Put the starter on GitHub: `gh auth login`, then `gh repo create shellhacks-starter --public --source . --push`
- [ ] On github.com → the repo → Settings → tick **Template repository**
- [ ] Dry-run the kickoff command so it's not new on the day: in `C:\dev`, `gh repo create kickoff-test --public --clone --template <your-github-user>/shellhacks-starter`, confirm `backend\.env` and `app.db` are NOT in it, then delete the test repo (`gh repo delete kickoff-test --yes`) and the folder
- [ ] Free API keys, saved where you can paste from: Gemini (aistudio.google.com/apikey — `llm.py` is wired for it; the default model is a Flash-Lite because that's the one with a usable free quota, confirm at aistudio.google.com/rate-limit), Unsplash (photos). Sponsor API keys wait until the challenges are released at kickoff
- [ ] `python -m pip install playwright` then `python -m playwright install chromium` (once, ~300MB; `python -m` so it lands in the same `python` that `check.sh` calls) — without it `.\check.ps1`'s browser check prints SKIPPED and still passes, and Claude's `webapp-testing` skill and `/pitch` screenshots use it too
- [ ] Photos: copy the repo-root `.env.example` to `.env` and paste `UNSPLASH_ACCESS_KEY`; Gemini goes in `backend\.env`
- [ ] 15 minutes: learn to open the browser console (F12 → Console and Network tabs) and paste the red text to Claude. This is the one skill that unblocks a stuck AI on a frontend bug.
- [ ] 60-minute timed mini-build from the template: `/spec` a throwaway idea, build one feature end to end (table → endpoint → page), `/check`, commit. This tests your loop, not the code. Write down where you got stuck.
- [ ] Practice deploy per DEPLOY.md, timeboxed to 45 minutes. If Render/Vercel fight you, stop and do it at hour 4 of the event instead. Delete the practice services afterwards (DEPLOY.md top note).
- [ ] Laptop settings for a 36-hour build: Settings → System → Power → "When plugged in, put my device to sleep after" → **Never** (a sleeping laptop kills the Claude session mid-turn). Windows Security → Virus & threat protection → Exclusions → add `C:\dev` (otherwise Defender re-scans `node_modules` on every change and eats the disk — you hit this on `Projects` already).
- [ ] Join the ShellHacks Discord and link it to your ShellHacks account (channels are hidden until you do); non-FIU students fill in the parking portal form or get fined.
- [ ] Sleep 6+ hours. Pack: laptop, charger, phone + charger, battery bank, headphones, jacket (rooms are cold), blanket. **No extension cables or power strips — venue rule.**

## First hour (K → K+1:00)
- [ ] In `C:\dev` (outside OneDrive — it's slow and locks files mid-build):
  `gh repo create <project-name> --public --clone --template <your-github-user>/shellhacks-starter`
  The project gets its own GitHub repo with event-time history only, and a "generated from" badge that discloses the template.
- [ ] Paste the sponsor challenge list (from the opening ceremony / event site) into CLAUDE.md under "Sponsor / company challenges to target" — `/ideas` and `/spec` read it from there (or skip this and paste it when Claude asks)
- [ ] Copy your keys in: `copy C:\dev\shellhacks-starter\backend\.env backend\.env` and `copy C:\dev\shellhacks-starter\.env .env` (Claude can't write keys). Claude does the rest of the setup and the check itself (PLAYBOOK Phase 0).
- [ ] Start Claude Code in the repo and **accept the "trust this folder" prompt** — until you do, the permission allowlist and hooks in `.claude/settings.json` are silently ignored. Also accept the prompt to install the repo's plugins (`frontend-design`, `example-skills`). If no prompt appears, `/plugin` and check they're listed.
- [ ] `/model` → pick the bigger model. No idea yet, or candidates to score: run `/ideas <your candidates, if any>` first (25 minutes) — it generates candidates from your answers and scores them, yours too, on the judging criteria and sponsor fit, and you pick one. Then run `/spec` (after `/ideas`: nothing after it; otherwise `/spec <your idea in a sentence>`) — it interviews you, writes SPEC.md, fills in CLAUDE.md, and commits "Start ShellHacks project". (Claude assumes kickoff = now and the end time from the Hacker Guide — correct it if either is wrong.) Timebox for `/spec`: 20 minutes.
- [ ] `/clear`, `/model` → Sonnet, then "build from SPEC.md, walking skeleton first"

## Building
- [ ] `.\dev.ps1` keeps both servers up in two windows (backend auto-reloads); leave them running all day
- [ ] Model plan: build on Sonnet. Switch up (`/model`) for hard debugging when Sonnet has failed twice, and for the final review. Switch back after.
- [ ] Walking skeleton by hour 10: the ugliest version of the exact demo path working end to end. Then iterate. Never breadth-first.
- [ ] Claude keeps CLAUDE.md's "Current status" and "Decisions" updated with each commit — glance at them when you come back from a break; they're the session's memory
- [ ] If the laptop restarts or the terminal dies: `.\dev.ps1` again, then `claude --continue` in the repo — it picks up the same session; its first move is reading Current status
- [ ] Three Claude Code moves worth knowing: a change made things worse → press **Esc twice** and rewind to before it; a side question you don't want cluttering the session → start it with `/btw`; the session feels confused after many corrections → `/clear` and restate the task (Claude reloads CLAUDE.md, SPEC.md, and Current status)
- [ ] Watch the status line at the bottom: `ctx` is how full Claude's memory is (it compacts itself near the top — fine), `5h` is your usage limit, `↑N` is unpushed commits
- [ ] Deploy early (see DEPLOY.md), between hour 4 and hour 10 (Claude asks after K+4). Data lives in Render's Postgres, so redeploys don't lose anything. A broken deploy found early is a non-event; found late is a crisis.
- [ ] `git push` every couple of hours — backup if the laptop dies, and it keeps the timestamped history safe (Claude nags at 2 unpushed commits)
- [ ] Re-check Scope every few hours — cut "nice to have" the moment you're behind
- [ ] Sleep night one. Solo with no sleep produces garbage on day two.

## Last 3 hours — stop building new features
- [ ] Freeze features, fix only what's broken
- [ ] Judging is table-to-table: **demo from your laptop's localhost** (`.\dev.ps1`, `seed.py` so the demo data exists). The deployed URL is for Devpost and for judges who click later.
- [ ] Final push → Render + Vercel redeploy on their own; run `backend\venv\Scripts\python backend\smoke_test.py <render-url> <vercel-url>` once Render shows green. Demo data persists in Postgres; write the demo login on paper anyway.
- [ ] `/pitch` — 30-second version, the 3-minute presentation script, Devpost writeup, screenshots, project README. Read the presentation out loud with a timer; memorize the 30-second one.
- [ ] Record the **3-minute demo video** (required on Devpost) by one hour before the deadline: screen-record the demo path on localhost while reading the presentation script. Upload it and put the link in the submission.
- [ ] `/ship-check` — every readiness check, including the rules disclosure line, a secrets scan, and the Devpost fields (repo link, Discord tag, challenge opt-ins, video)
- [ ] Full run-through of the demo path on both localhost and the deployed URL

## Before you sleep / leave
- [ ] Confirm the deployed URL works from a phone on cellular data (not the venue wifi)
- [ ] Charge everything
