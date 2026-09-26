---
name: ship-check
description: Pre-demo and pre-submission readiness check. Only runs when the user types /ship-check.
disable-model-invocation: true
---

Go through each item, actually run the command where there is one, and report a short PASS/FAIL list. Fix only what's quick and safe; flag the rest.

1. `/check` passes locally.
2. The deployed pair works together: take both URLs from CLAUDE.md → Deployed (ask if not filled in), then run the deployed checks (PLAYBOOK → Deploy). A Render cold start can exceed `smoke_test.py`'s 30 s timeout — if the first run says "not reachable", rerun once; only a second failure counts.
3. The deployed frontend actually works in a browser: `python frontend/e2e/smoke.py <vercel-url>`, then look at `.claude/tmp/e2e.png`, which is now the live site.
4. `git status` is clean and the latest commit is pushed (`git status -sb` shows no "ahead").
5. README.md describes the actual project (what it is, how to run it) — not the starter template text.
6. Rules disclosure: README.md and the Devpost writeup in PITCH.md contain the `pitch` skill's disclosure line. If missing, draft it per that skill and add it.
7. CREDITS.md lists every third-party asset (Unsplash photos, etc.), and each photo shows its credit in the UI.
8. No secrets in the repo. Both of these must print nothing:
   - `git ls-files ':(glob)**/.env' ':(glob)**/.env.*' ':(exclude,glob)**/*.example'` (a real .env or .env.local/.env.production file is tracked)
   - `git grep --untracked -nIE "((SECRET|KEY|TOKEN|PASSWORD)[A-Za-z_]*['\"]?[[:space:]]*[:=][[:space:]]*['\"][A-Za-z0-9_./+-]{20,}['\"]|sk-[A-Za-z0-9_-]{20,}|AIza[0-9A-Za-z_-]{30,}|gh[pous]_[A-Za-z0-9]{30,})" -- . ':!*.example' ':!scripts/' ':!**/package-lock.json'` (hardcoded key-looking values)
   If either finds something, move the value into the right `.env`, read it with `os.getenv` / `import.meta.env`, and tell the user to rotate that key since it may already be in git history.
9. Devpost submission (you can't see it — one AskUserQuestion with these 4 questions): (1) GitHub repo link and at least one Discord tag are in the submission? (2) every sponsor challenge from PITCH.md opted into (+ Best First-Time Hacker if eligible)? (3) the 3-minute demo video is uploaded and linked? (4) Submit clicked — not a draft — from the account with the same email as the ShellHacks registration?
