# Deploy path

Do this once before the event, as practice, so the kickoff-day deploy is just "repeat these steps."
Order matters: backend first (Vercel needs its URL), then frontend, then close the loop.

After the practice run, delete the practice services (Render: service → Settings → Delete, and the database; Vercel: project → Settings → Delete). Otherwise on kickoff day the Blueprint's names `shellhacks-backend` / `shellhacks-db` are already taken and Vercel's project name collides — a confusing 10 minutes you don't need. (Or rename them in `render.yaml` at kickoff.)

## 1. Backend + database → Render (about 5 clicks — `render.yaml` does the rest)

1. Push this repo to GitHub.
2. render.com → New → **Blueprint** → connect the repo. Render reads `render.yaml` and shows two things: the `shellhacks-backend` web service and a free `shellhacks-db` Postgres. Accounts, uploads, and everything else live in that database, so **nothing is lost on deploys or restarts**. (Render's free Postgres expires 30 days after creation — plenty for the event.)
3. It asks for `ALLOWED_ORIGINS` — put `http://localhost:5173` for now (you'll change it in section 3). `JWT_SECRET` and `DATABASE_URL` are filled in for you. If the idea uses AI, add `GEMINI_API_KEY` under Environment after the service exists (same value as in `backend/.env`).
4. Apply. Wait for the deploy to go green. Copy the URL (e.g. `https://shellhacks-backend.onrender.com`) into CLAUDE.md under **Deployed**.
5. Confirm: `https://<your-render-url>/api/health` shows `{"status":"ok"}` (it checks the database too — `db-error` means the database link is wrong).

## 2. Frontend → Vercel

1. vercel.com → New Project → import the same repo.
2. Root directory: `frontend` (Vite is auto-detected; `frontend/vercel.json` handles page routing).
3. Environment variable `VITE_API_URL` = your Render URL from above, **no trailing slash**. Unless the idea has real multi-user accounts, also add `VITE_DEMO_EMAIL` and `VITE_DEMO_PASSWORD` (same values as `frontend/.env`).
4. Deploy. Get the URL from **Project → Settings → Domains** (the stable `yourapp.vercel.app` one), not the "Visit" button — that often opens a per-deployment URL like `yourapp-abc123.vercel.app`, which won't match the CORS setting below. Copy it into CLAUDE.md under **Deployed**.

## 3. Close the loop

Render → your service → Environment → set `ALLOWED_ORIGINS` to your Vercel URL (no trailing slash; several origins are comma-separated) → save, it redeploys. Then open the Vercel URL: "Backend status: ok" with no CORS errors in the browser console. If it says "backend unreachable", read the text after the dash: if it names `VITE_API_URL`, set that in Vercel and redeploy; otherwise the origin doesn't match — compare the browser's address bar to `ALLOWED_ORIGINS` character by character (https, no trailing slash, the Domains URL not a deployment URL).

Prove the deployed pair works, not just that it's up — the backend answers, it accepts requests from the Vercel origin, and the Vercel build actually points at this backend — then create the demo account and demo data once (they persist from here on):

```
backend\venv\Scripts\python backend\smoke_test.py https://<your-render-url> https://<your-vercel-url>
backend\venv\Scripts\python backend\seed.py https://<your-render-url>
```

## 4. Keep the backend awake (2 minutes, do it right after deploying)

Render's free tier spins down after 15 min idle, and the next request waits 30s or more — that would be a judge's first click. The repo includes a GitHub Action scheduled every 5 minutes (GitHub often delays it, so expect roughly every 10) that pings the backend. Turn it on: GitHub → your repo → Settings → Secrets and variables → Actions → **Variables** → New repository variable → name `RENDER_URL`, value your Render URL. Confirm under the Actions tab that "Keep backend warm" runs green. Still open the site yourself a minute before demoing.

## Redeploying during the hackathon

Vercel redeploys on every `git push`; Render only when the push changes something under `backend/` (`render.yaml` sets `rootDir: backend`) — a few minutes for Render, less for Vercel — and **the data stays**. Two things to know:
- Render's free tier has ~500 build minutes a month at ~3 per deploy. Pushing every ten minutes for 36 hours would burn through them; if you're pushing that often, Render → service → Settings → Build & Deploy → Auto-Deploy: No, and redeploy manually a few times a day.
- Don't redeploy in the last 15 minutes before a demo, and demo from localhost when judges are at your table.

## Why Render + Vercel

Both have free tiers, both deploy straight from a GitHub push (no CLI setup needed mid-hackathon), the Blueprint provisions the database, and this combo is battle-tested for FastAPI + Vite.
