import { useEffect, useState } from 'react'

// One POST /api/gridlock/plans answer per (pair, language): the collaboration plans the companies' agents proposed and
// the coordinator compared. Kept for the session (a pair reopened, or a language switched back, costs nothing; the
// route is rate-limited for the whole venue's IP). `tries` is in the key, so Retry always asks again. {status: 'idle'}
// until `id` is given (the plans step asks only when it opens).
const KEPT = new Map()
const KEEP = 60

export function usePlans(client, id, { lang = 'en', months, tries = 0 } = {}) {
  const key = id ? `${id}@${lang}@${months ?? ''}@${tries}` : null
  const [st, setSt] = useState({ key: null })
  useEffect(() => {
    if (!client || !id || KEPT.has(key)) return undefined
    let live = true
    const started = Date.now()
    client.plans(id, { lang, window_months: months }).then(
      (data) => {
        KEPT.set(key, data)
        if (KEPT.size > KEEP) KEPT.delete(KEPT.keys().next().value)
        if (live) setSt({ key, status: 'ready', data, ms: Date.now() - started })
      },
      (error) => live && setSt({ key, status: 'error', error }),
    )
    return () => {
      live = false
    }
  }, [client, id, lang, months, key])
  if (!id) return { status: 'idle' }
  const kept = KEPT.get(key)
  if (kept) return { status: 'ready', data: kept }
  return st.key === key ? st : { status: 'loading' }
}

// elapsed ms while the plans are loading (for the "still working" line)
export function useElapsed(on) {
  const [ms, setMs] = useState(0)
  useEffect(() => {
    if (!on) return undefined
    const t0 = Date.now()
    const id = setInterval(() => setMs(Date.now() - t0), 1000)
    return () => {
      clearInterval(id)
      setMs(0)
    }
  }, [on])
  return ms
}
