---
name: check
description: Run every project check (frontend lint + build, backend smoke test against a live server, headless-browser check of the built app) and report pass/fail with evidence. Use whenever the user asks if things work, and per CLAUDE.md → Workflow.
---

Run from the repo root, with the Bash tool timeout set to 600000 (it can pass the default 120 s once the demo path exists):

```
bash scripts/check.sh
```

- Show the user the final PASSED/FAILED line and the names of anything that failed. That output is the evidence — don't summarize it as "looks good".
- If something failed, fix it and rerun. Never weaken a check, skip it, or edit smoke_test.py to make a failure go away unless the test itself is wrong — and say so explicitly if you believe it is.
