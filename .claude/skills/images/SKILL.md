---
name: images
description: Photo pass for finished pages — fill obvious photo spots (hero, empty states, feature cards, about) with Unsplash photos, place the best picks, then ask the user once to confirm or swap. Use as the LAST step of a turn after /check passes, or when the user asks for images/photos. Only when Current status says PHASE 3 or 4 and the user is present. Never while a feature is still being built, and never in a turn that applied a new design direction.
---

Goal: the user stays hands-off, and nothing waits on their answer. User request, if any: $ARGUMENTS

0. **Preflight:** `node scripts/unsplash.mjs status`. If `configured` is false, skip this whole skill without comment (unless the user explicitly asked for images — then tell them in one line to add `UNSPLASH_ACCESS_KEY` to the repo-root `.env`, see `.env.example`).
1. **Which pages:** only ones that now work and don't have photos yet; if several finished this turn, they share one pass.
2. **Pick at most 4 spots total** across those pages — the ones where a photo clearly helps. Leave the rest for a later pass.
3. **Search each spot:** `node scripts/unsplash.mjs search "<short concrete query>" --count 4 --orientation <landscape|portrait|squarish>` matching the slot's shape. On a rate-limit or key error, stop the pass and mention it in one line.
4. **Look at every thumbnail** with the Read tool and rank by what's actually in the image (fits the spot, works under any text, suits the page colors), not by the description text.
5. **Place the #1 pick for each spot now:** `node scripts/unsplash.mjs use <id>`, then:
   - `<img src={hotlinkUrl} alt="...">` using `hotlinkUrl` exactly as returned (Unsplash requires hotlinking — never save images into the repo). Rewrite vague `alt` text to describe what matters on this page.
   - `<PhotoCredit photographerName=... photographerUrl=... unsplashUrl=... />` from `frontend/src/PhotoCredit.jsx`, near the image or in the footer.
6. **Ask once, as the very last thing in the turn:** one AskUserQuestion call with one question per spot that has at least one alternate (max 4 questions, folded into the turn's one AskUserQuestion — CLAUDE.md → How to read me). Options: your pick first labeled "(current)", then up to 2 alternates — each description is one line on what's in the photo plus its `viewUrl`. A spot with no alternates is just placed, not asked about.
7. **If the user swaps:** `use` the new id, update the `img` and `PhotoCredit`, then `node scripts/unsplash.mjs drop <old id>` to keep CREDITS.md accurate. If they pick "Other" and describe something, search once more for that spot.

Budget: demo keys allow 50 requests/hour; a search is 1, a `use` is 2 — a full 4-spot pass is about 12.
