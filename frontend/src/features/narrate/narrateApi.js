import { api } from '../../api'

// The narrated build-up for Strengthen the grid (backend/narrate.py).
//   POST /api/strengthen/narration {region, mw, load_factor, mode: 'firm'|'flexible', budget, lang: 'en'|'es', ai}
//     → {slides[{id, kind, step_from, step, steps, headline{lang}, narration{lang: [{role, text, chars, cues, key}]},
//        est_s{lang}, written_by{lang}, site}], today, bought{n, cost_high}, ai{by, fallback, reason}, facts, lang, ...}
//     409 while the state has no finished study for that size (the page runs it first) · 422 a bad body
// The server only speaks what it wrote: each segment's audio is asked for by its key (briefing/voiceApi.getSegment).

const scripts = new Map() // body JSON -> Promise<script> (the server caches too; this saves the round trip)

export function getNarration(body) {
  const k = JSON.stringify(body)
  if (!scripts.has(k)) {
    const p = api('/api/strengthen/narration', { method: 'POST', body })
    p.catch(() => scripts.get(k) === p && scripts.delete(k))
    scripts.set(k, p)
    while (scripts.size > 24) scripts.delete(scripts.keys().next().value)
  }
  return scripts.get(k)
}
