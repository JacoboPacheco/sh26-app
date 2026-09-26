// The agent trace's pure helpers (AgentTrace.jsx and the presentation's timing share them).
const HEADS = new Set(['propose', 'revise', 'call'])
const VERDICTS = new Set(['verify', 'result'])

export const loc = (x, lang = 'en') => (x == null ? '' : typeof x === 'string' ? x : x[lang] || x.en || '')

// a proposal (or a tool call) and the engine's verdict on it become one item
export function groupTrace(trace = []) {
  const items = []
  for (let i = 0; i < trace.length; i++) {
    const r = trace[i]
    const next = trace[i + 1]
    if (HEADS.has(r.kind) && next && VERDICTS.has(next.kind)) {
      items.push({ key: `${r.n}-${next.n}`, head: r, verdict: next })
      i += 1
    } else items.push({ key: String(r.n), head: r, verdict: null })
  }
  return items
}

// The short version (the presentation): the first and the last item, then by priority the first plan that failed, the
// first time the findings went back, the first revision that held, the first plan that held, then the rest in order.
export function compactItems(items, max) {
  if (!max || items.length <= max) return items
  const first = items[0]
  const last = items[items.length - 1]
  const mid = items.slice(1, -1)
  const pick = new Set()
  const add = (it) => it && pick.size < max - 2 && pick.add(it)
  const fb = mid.findIndex((it) => it.head.kind === 'feedback')
  add(mid.find((it) => it.verdict?.tone === 'over'))
  add(mid[fb])
  add(fb >= 0 ? mid.slice(fb).find((it) => it.verdict?.tone === 'holds') : null)
  add(mid.find((it) => it.verdict?.tone === 'holds'))
  mid.forEach(add)
  return [first, ...mid.filter((it) => pick.has(it)), last]
}
