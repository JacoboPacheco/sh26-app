---
name: debug
description: Structured debugging when something is broken — reproduce it, read the real error, fix the root cause, add a check so it stays fixed. Use whenever a feature doesn't work, a request fails, or the page breaks. Not for a nice-to-have in Phase 4 (PLAYBOOK Phase 4).
---

Problem as reported: $ARGUMENTS
If no target was given ("it's broken"), assume the last thing you changed or the last page you showed.

Rules: no code changes until step 3. Guessing costs more time than looking.

1. **Reproduce it and capture the real error, in this order:**
   - Backend: `backend/server.log` — the traceback names the file and line. If the backend isn't running, that's the bug — CLAUDE.md → Commands.
   - Browser: the console and the failed request's response body (it has FastAPI's `detail`) — via the browser tool if this session has one, else a Playwright script per CLAUDE.md → Commands.
   - Still nothing? `/check` — it exercises every endpoint and says which one fails.
   Quote the exact error message in your reply. If you can't reproduce it, say so and ask one AskUserQuestion with options: the page just shown (recommended) / the backend won't start / something else — paste what you see. Don't fix something you can't see. Away mode: PLAYBOOK → Away mode.
2. **Name the cause in one sentence** before touching code. If the error mentions CORS, a port, or "unreachable": deployed → PLAYBOOK → Recovery; local → the backend isn't on :8000 (CLAUDE.md → Commands). A `.env` value not taking effect → CLAUDE.md → Gotchas.
3. **Fix the root cause**, not the symptom. Never suppress an error, widen a `try/except`, or loosen validation to make a message go away.
4. **Prove it:** reproduce the original steps and show they pass. If the bug was in an endpoint, add a check to `backend/smoke_test.py` so `/check` catches it next time.
5. If the fix is more than a few lines, commit it per CLAUDE.md → Workflow (that rule says when the reviewer runs first), with a message saying what was broken.
