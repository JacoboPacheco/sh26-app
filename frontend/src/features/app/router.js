import { useEffect, useState } from 'react'

// The app's hash router under #/next. Pages:
//   #/next                          home: the national map of announced AI data centers
//   #/next/state/XX?lat&lon&mw&t&firm&dc   a state's workspace (the query places a campus once, on entry)
//   #/next/dc/<id>                  one catalog data center: facts, sources, "Test it"
//   #/next/library                  saved scenarios
//   #/next/compare/<a>/<b>          two scenarios side by side
//   #/next/brief/<id>               one scenario as a shareable page
// No router library: the whole app is six pages.

export const BASE = '#/next'

export function parseHash(hash = window.location.hash) {
  const raw = hash.startsWith(BASE) ? hash.slice(BASE.length) : ''
  const [path, qs = ''] = raw.split('?')
  const parts = path.split('/').filter(Boolean).map((p) => decodeURIComponent(p))
  const query = Object.fromEntries(new URLSearchParams(qs))
  const [head, a, b] = parts
  if (!head) return { page: 'home', query }
  if (head === 'state' && a && /^[A-Za-z]{2}$/.test(a)) return { page: 'state', code: a.toUpperCase(), query }
  if (head === 'dc' && a) return { page: 'dc', id: a, query }
  if (head === 'library') return { page: 'library', query }
  if (head === 'compare') return { page: 'compare', a: a || null, b: b || null, query }
  if (head === 'brief' && a) return { page: 'brief', id: a, query }
  return { page: 'missing', path, query }
}

export function useRoute() {
  const [route, setRoute] = useState(() => parseHash())
  useEffect(() => {
    const on = () => setRoute(parseHash())
    window.addEventListener('hashchange', on)
    return () => window.removeEventListener('hashchange', on)
  }, [])
  return route
}

// '#/next/state/FL?lat=26.64' from ('/state/FL', {lat: 26.64}); empty values are dropped
export function href(path = '', query) {
  const q = query
    ? new URLSearchParams(Object.entries(query).filter(([, v]) => v !== undefined && v !== null && v !== '' && v !== false)).toString()
    : ''
  return `${BASE}${path}${q ? `?${q}` : ''}`
}

// Navigate (a new history entry), or with {replace} rewrite the address without one. A replace
// never fires hashchange, so the page doesn't re-read it: use it only to mirror state already on screen.
export function go(path, query, { replace = false } = {}) {
  const next = href(path, query)
  if (replace) {
    if (window.location.hash !== next) window.history.replaceState(window.history.state, '', next)
    return
  }
  if (window.location.hash === next) return
  window.location.hash = next
}
