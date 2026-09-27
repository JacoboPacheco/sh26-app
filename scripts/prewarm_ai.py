"""Pre-run every Gemini step the demo shows, so judging reads answers from the cache instead of spending live
quota (llm.py keeps them in memory and on disk for 48 hours). Safe to run again: cached answers cost nothing.

    backend/venv/Scripts/python scripts/prewarm_ai.py http://127.0.0.1:8000        # the local demo backend
    backend/venv/Scripts/python scripts/prewarm_ai.py https://<your-render-url>     # the deployed one

It walks: the Fort Myers hero (briefing + the AI proposer, the full and short presentation, the cost AI column
at 1,500 and 500 MW, the Ask box's suggested questions), the Florida five (briefing + proposer, the proposal
page, the AI analyst when that route exists, the default public comment), Build together's top overlaps (the drafted agreement, English
and Spanish for the first), the two negotiating agents on the top three pairs (and the drafts their terms feed), the
siting agent's default goals (Florida and Texas, 3 GW), and a Strengthen study for Florida at 1,000 MW. Each line
says whether Gemini or the plain version answered, so a run on a day the quota is out shows it at once.
"""

import json
import sys
import time
import urllib.error
import urllib.request

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000").rstrip("/")
HERO = {"lat": 26.6406, "lon": -81.8723, "mw": 1500}
FIVE = ["stonebridge-fort-meade", "atlas-compute-fort-pierce", "sentinel-grove-fort-pierce", "pba-holdings-loxahatchee", "nextnrg-near-jacksonville-international-airport"]
rows: list[tuple[str, str, float]] = []


def call(method, path, body=None, timeout=180):
    req = urllib.request.Request(BASE + path, data=None if body is None else json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method=method)
    t = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read() or b"null"), time.time() - t, r.status
    except urllib.error.HTTPError as e:
        return None, time.time() - t, e.code
    except Exception as e:  # noqa: BLE001
        return {"error": repr(e)[:120]}, time.time() - t, 0


def note(name, who, dt):
    rows.append((name, who, dt))
    print(f"{name:52s} {who:28s} {dt:6.1f} s", flush=True)


def who_deck(d):
    ai = (d or {}).get("ai") or {}
    return "gemini" if ai.get("by") in ("gemini", "mixed") and not ai.get("fallback") else f"plain ({ai.get('by')})"


def proposer(case, label):
    rep, dt, code = call("POST", "/api/briefing", case)
    if not rep or code != 200:
        note(f"{label}: briefing", f"HTTP {code}", dt)
        return
    t0 = time.time()
    for _ in range(40):
        ag = (rep or {}).get("agentic") or {}
        if ag.get("status") not in ("running", None):
            break
        time.sleep(3)
        rep, _, _ = call("POST", "/api/briefing", case)
    ag = (rep or {}).get("agentic") or {}
    note(f"{label}: AI proposer", f"{ag.get('status')} +{ag.get('added', 0)} verified", dt + time.time() - t0)


def main():
    up, _, code = call("GET", "/api/health", timeout=30)
    if code != 200:
        sys.exit(f"{BASE} is not answering /api/health ({code})")
    st, _, _ = call("GET", "/api/ai/status")
    print(f"AI configured: {st and st.get('configured')}, model {st and st.get('model')}, used today {st and st.get('used_today')}/{st and st.get('cap')}\n")

    # the hero
    proposer(HERO, "hero")
    for length in ("short", "full"):
        d, dt, code = call("POST", "/api/briefing/deck", {**HERO, "length": length})
        note(f"hero: presentation ({length})", who_deck(d) if code == 200 else f"HTTP {code}", dt)
    for mw in (1500, 500):
        c, dt, code = call("POST", "/api/cost/ai", {**HERO, "mw": mw})
        note(f"hero: cost AI column at {mw} MW", ("plain" if (c or {}).get("fallback") else "gemini") if code == 200 else f"HTTP {code}", dt)
    sug, _, code = call("POST", "/api/ask/suggestions", {"case": HERO, "lang": "en"})
    for q in ((sug or {}).get("questions") or [])[:4]:
        a, dt, code = call("POST", "/api/ask", {"case": HERO, "question": q, "lang": "en"})
        note(f"hero ask: {q[:40]}", ((a or {}).get("source") or "?") if code == 200 else f"HTTP {code}", dt)

    # the Florida five
    places, _, _ = call("GET", "/api/catalog/places?state=FL")
    by_id = {p["id"]: p for p in ((places or {}).get("places") or (places or {}).get("entries") or places or []) if isinstance(p, dict)} if places else {}
    for pid in FIVE:
        p = by_id.get(pid)
        if p and p.get("lat") is not None and p.get("mw"):
            proposer({"lat": p["lat"], "lon": p["lon"], "mw": min(float(p["mw"]), 50000)}, pid[:28])
        v, dt, code = call("GET", f"/api/vote/proposal/{pid}")
        note(f"{pid[:28]}: proposal page", "ok" if code == 200 else f"HTTP {code}", dt)
        a, dt, code = call("POST", f"/api/analyst/{pid}")
        if code != 404:
            note(f"{pid[:28]}: AI analyst", ((a or {}).get("by") or "?") if code == 200 else f"HTTP {code}", dt)
        # "Write your public comment" with the page's default choices (CommentWriter.jsx DEFAULTS)
        c, dt, code = call("POST", f"/api/vote/proposal/{pid}/comment", {"concerns": ["bill", "blackouts"], "stance": "questions", "minutes": 2, "lang": "en"})
        if code != 404:
            note(f"{pid[:28]}: public comment", (f"{(c or {}).get('by')} {(c or {}).get('checked')}/{(c or {}).get('total')} checked"
                                                 + (f" ({(c or {}).get('why')})" if (c or {}).get("fallback") else "")) if code == 200 else f"HTTP {code}", dt)

    # Build together
    opp, _, code = call("GET", "/api/gridlock/opportunities")
    ids = [o.get("id") or o.get("overlap_id") for o in ((opp or {}).get("opportunities") or [])][:5]
    for i, oid in enumerate(ids):
        if not oid:
            continue
        for lang in (("en", "es") if i == 0 else ("en",)):
            d, dt, code = call("GET", f"/api/agreement/{oid}?lang={lang}&ai=true")
            note(f"agreement {oid[:30]} {lang}", ((d or {}).get("by") or "?") + (" verified" if (d or {}).get("verified") else "") if code == 200 else f"HTTP {code}", dt)

    # Build together: the two negotiating agents on the top pairs (they feed the drafts above when agreed)
    for i, oid in enumerate(ids[:3]):
        if not oid:
            continue
        n, dt, code = call("POST", f"/api/negotiate/{oid}?lang=en&ai=true", {}, timeout=200)
        out = (n or {}).get("outcome") or {}
        note(f"negotiation {oid[:30]}", (f"{(n or {}).get('by')} {'agreed' if out.get('agreed') else 'no deal'} r{out.get('round')}") if code == 200 else f"HTTP {code}", dt)
        if code == 200 and out.get("agreed"):
            d, dt, code = call("GET", f"/api/agreement/{oid}?lang=en&ai=true&negotiated=en")
            note(f"agreement {oid[:30]} negotiated", ((d or {}).get("by") or "?") if code == 200 else f"HTTP {code}", dt)

    # AI boom mode: the siting agent's default goals (what "Let the AI place them" sends first)
    for region, name in (("FL", "Florida"), ("TX", "Texas")):
        body = {"region": region, "goal": f"Place 3 GW of AI campuses in {name} without blacking anyone out", "total_mw": 3000, "max_sites": 3, "load_factor": 1.0, "firm": False}
        job, dt, code = call("POST", "/api/planner/start", body)
        jid = (job or {}).get("job_id") or (job or {}).get("id")
        t0 = time.time()
        s = job
        while code == 200 and jid and time.time() - t0 < 120:
            s, _, _ = call("GET", f"/api/planner/jobs/{jid}")
            if (s or {}).get("status") in ("done", "error"):
                break
            time.sleep(2)
        res = (s or {}).get("result") or {}
        note(f"siting agent {region} 3 GW", f"{(s or {}).get('status')} by {res.get('by') or res.get('agent', {}).get('by') if isinstance(res, dict) else '?'}" if code == 200 else f"HTTP {code}", dt + time.time() - t0)

    # Strengthen (a background job)
    job, dt, code = call("POST", "/api/unlock/start", {"region": "FL", "mw": 1000})
    if code == 200 and job:
        jid = job.get("job_id") or job.get("id")
        t0 = time.time()
        while time.time() - t0 < 240:
            s, _, _ = call("GET", f"/api/unlock/jobs/{jid}")
            if (s or {}).get("status") in ("done", "error"):
                break
            time.sleep(2)
        note("Strengthen Florida 1,000 MW", (s or {}).get("status") or "?", dt + time.time() - t0)

    # Watch the story: the five Florida episodes in English (the director agent writes each once; replays are cached)
    for ep in ("collapse", "hurricane", "boom", "strengthen", "together"):
        job, dt, code = call("POST", f"/api/show/{ep}", {"region": "FL", "lang": "en"})
        t0 = time.time()
        s = job
        while code == 200 and job and (s or {}).get("status") == "pending" and time.time() - t0 < 240:
            time.sleep(2)
            s, _, _ = call("GET", f"/api/show/jobs/{job['id']}")
        if code == 200 and (s or {}).get("status") == "pending":
            s, _, _ = call("GET", f"/api/show/jobs/{job['id']}")
        ai = ((s or {}).get("show") or {}).get("ai") or {}
        note(f"show: {ep}", (f"{ai.get('status')} {ai.get('scenes_by_gemini')}/{(ai.get('scenes_by_gemini') or 0) + (ai.get('scenes_template') or 0)} scenes"
                             if (s or {}).get("status") == "done" else f"{(s or {}).get('status')} {(s or {}).get('error') or ''}") if code == 200 else f"HTTP {code}", dt + time.time() - t0)

    # Harden before the storm: the hero storm at the page's three budgets (the planner's cache is in memory:
    # rerun after every backend restart)
    for budget in (150e6, 50e6, 500e6):
        body = {"region": "FL", **HERO, "sites": [], "load_factor": 1.0, "upgrades": {}, "firm": False, "preset": "gulf-fort-myers", "budget_usd": budget}
        r, dt, code = call("POST", "/api/harden/run", body)
        t0 = time.time()
        while r and r.get("status") == "running" and time.time() - t0 < 240:
            time.sleep(2)
            r, _, code = call("GET", f"/api/harden/jobs/{r['job']}")
        note(f"harden: hero storm ${budget / 1e6:.0f}M", ((r or {}).get("result") or {}).get("by") or f"HTTP {code}", time.time() - t0 + dt)

    # Build together, Sperry's table: the top 12 pairs of the current ranking (DESC 2026-2030 x Georgia Power) with
    # everything a pair's sheet asks for: the plain and Gemini drafts, the two agents' negotiation, and the drafts on the
    # negotiated terms (the sheet uses them once the agents agree). Negotiations and drafts are cached in memory: rerun
    # after a backend restart. Paced under the routes' 30/minute limit.
    opp, _, code = call("GET", "/api/gridlock/opportunities?limit=12")
    top = [o.get("id") for o in ((opp or {}).get("opportunities") or []) if o.get("id")]
    for k, oid in enumerate(top, 1):
        for q in ("lang=en&ai=false", "lang=en&ai=true") + (("lang=es&ai=true",) if k == 1 else ()):
            d, dt, code = call("GET", f"/api/agreement/{oid}?{q}")
            note(f"pair {k} draft {q.replace('&', ' ')}", ((d or {}).get("by") or "?") if code == 200 else f"HTTP {code}", dt)
            time.sleep(2.1)
        n, dt, code = call("POST", f"/api/negotiate/{oid}?lang=en&ai=true", {}, timeout=200)
        out = (n or {}).get("outcome") or {}
        note(f"pair {k} negotiation", (f"{(n or {}).get('by')} " + ("nothing to negotiate" if out.get("nothing") else "agreed" if out.get("agreed") else "no deal"))
             if code == 200 else f"HTTP {code}", dt)
        time.sleep(2.1)
        if code == 200 and out.get("agreed") and out.get("verified"):
            for q in ("lang=en&ai=false&negotiated=en", "lang=en&ai=true&negotiated=en"):
                d, dt, code = call("GET", f"/api/agreement/{oid}?{q}")
                note(f"pair {k} draft on the agreed terms", ((d or {}).get("by") or "?") if code == 200 else f"HTTP {code}", dt)
                time.sleep(2.1)

    st, _, _ = call("GET", "/api/ai/status")
    print(f"\nDone: {len(rows)} steps. AI used today {st and st.get('used_today')}/{st and st.get('cap')}; cached answers {st and st.get('cached_answers')}; models out today {st and st.get('models_out_today')}")


if __name__ == "__main__":
    main()
