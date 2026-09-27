import { useEffect, useState } from 'react'
import { displayName } from './format'

// One /api/agreement answer for (id, window setting, language, ai, negotiated terms); {status: 'idle'} without an id.
// Answers are kept for the session (the route is rate-limited at 30/minute for everyone on the venue's IP, so going
// back to a pair or a language, or reopening one, costs nothing). `defer` (ms) waits that long before asking, so a
// pair or language only passed through never starts a Gemini draft; a kept answer shows at once. `tries` is in the
// key, so Retry always asks again.
const KEPT = new Map()
const KEEP = 80

export function useAgreement(client, id, { months, lang, ai, negotiated, plan, tries = 0, defer = 0 }) {
  const key = id ? `${id}@${months}@${lang}@${ai ? 1 : 0}@${negotiated || ''}@${plan || ''}@${tries}` : null
  const [st, setSt] = useState({ key: null })
  useEffect(() => {
    if (!client || !id || KEPT.has(key)) return undefined
    let live = true
    const ask = () =>
      client.agreement(id, { window_months: months, lang, ai, negotiated: negotiated || undefined, plan: plan || undefined }).then(
        (raw) => {
          const data = withDisplayNames(raw)
          KEPT.set(key, data)
          if (KEPT.size > KEEP) KEPT.delete(KEPT.keys().next().value)
          if (live) setSt({ key, status: 'ready', data })
        },
        (error) => live && setSt({ key, status: 'error', error }),
      )
    const t = defer > 0 ? setTimeout(ask, defer) : null
    if (!t) ask()
    return () => {
      live = false
      clearTimeout(t)
    }
  }, [client, id, months, lang, ai, negotiated, plan, key, defer])
  if (!id) return { status: 'idle' }
  const kept = KEPT.get(key)
  if (kept) return { status: 'ready', data: kept }
  return st.key === key ? st : { status: 'loading' }
}

// The backend titles Georgia's all-caps names with an older rule ("Thurmond Dam (usa)", "Sav: Goshen (sav)"). Every
// place the draft prints a project's backend display name gets format.js's name instead ("(USA)", "SAV:"), so the
// list, the sheet, the draft and its print copy name a project alike. A no-op once the two rules agree.
function withDisplayNames(doc) {
  const swaps = (doc?.overlap?.projects || [])
    .map((p) => [p.display_name, displayName(p.name)])
    .filter(([from, to]) => from && to && from !== to)
  if (!swaps.length) return doc
  const fix = (v) => {
    if (typeof v === 'string') return swaps.reduce((s, [from, to]) => s.split(from).join(to), v)
    if (Array.isArray(v)) return v.map(fix)
    if (v && typeof v === 'object') return Object.fromEntries(Object.entries(v).map(([k, x]) => [k, k === 'name' ? x : fix(x)]))
    return v
  }
  return fix(doc)
}
