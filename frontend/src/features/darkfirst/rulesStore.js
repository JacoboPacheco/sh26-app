import { useCallback, useEffect, useSyncExternalStore } from 'react'
import { api } from '../../api'

// "Who goes dark first?" — the shared state of the results column's block (DarkFirst.jsx) and the map layer
// (DarkFirstLayer.jsx): the case's three service rules (backend/service_rules.py → POST /api/grid/service-rules),
// fetched ONCE per case, which rule is on screen, and whether the block is showing (the map draws a rule's end
// state only while it is). Module-level, like features/fix/flipCase.js, so the two never ask the server twice.
//
// The rules, in the order the switch shows them:
export const RULES = [
  { key: 'flexible', label: 'Nobody planned' },
  { key: 'firm', label: 'Keep the campus on' },
  { key: 'step_down', label: 'The campus steps down first' },
]

// The body the three rules are solved for: the map's campuses (caseBody) with the network the cascade on screen
// ran on (its echoed trip list, upgrades and load level: a storm's lines can come from the run, not the case).
// null when the cascade isn't this case's campuses (sizes differ) or there is no campus to rule on.
export function rulesBody(caseBody, cascade) {
  if (!caseBody || !cascade || caseBody.region === 'US') return null
  const b = {
    region: caseBody.region || 'FL',
    load_factor: cascade.load_factor ?? caseBody.load_factor ?? 1,
    trip: cascade.trip ?? caseBody.trip ?? [],
    upgrades: cascade.upgrades ?? caseBody.upgrades ?? {},
    sites: (caseBody.sites || []).map(({ lat, lon, mw }) => ({ lat, lon, mw })),
  }
  if (caseBody.lat != null && caseBody.lon != null && caseBody.mw != null) Object.assign(b, { lat: caseBody.lat, lon: caseBody.lon, mw: caseBody.mw })
  const total = (Number(b.mw) || 0) + b.sites.reduce((s, x) => s + (Number(x.mw) || 0), 0)
  if (!(total > 0) || Math.abs(total - (Number(cascade.mw) || 0)) > 0.5) return null
  return b
}
export const rulesKey = (body) => (body ? JSON.stringify(body) : '')
// the rule the map's own cascade ran under: the base map already shows its end state
export const baseRule = (cascade) => (cascade?.firm ? 'firm' : 'flexible')

// ------------------------------------------------------------------ the store
let st = { key: '', status: 'idle', data: null, error: null, pick: null, pickKey: '', active: 0 }
const subs = new Set()
const emit = (patch) => {
  st = { ...st, ...patch }
  subs.forEach((f) => f())
}
const subscribe = (f) => {
  subs.add(f)
  return () => subs.delete(f)
}
const read = () => st
let seq = 0

function load(key, force = false) {
  if (!key) return
  if (!force && key === st.key && st.status !== 'error') return // already asked (every reader calls this)
  const id = ++seq
  emit({ key, status: 'loading', data: null, error: null })
  api('/api/grid/service-rules', { method: 'POST', body: JSON.parse(key) })
    .then((data) => id === seq && emit({ data, status: 'done' }))
    .catch((error) => id === seq && emit({ status: 'error', error }))
}

/** The whole store: {key, status, data, error, pick, pickKey, active}. */
export const useDarkFirst = () => useSyncExternalStore(subscribe, read, read)

/** The three rules for `body` (asked once per case): {data, status, error, retry, key}. */
export function useServiceRules(body, enabled = true) {
  const key = enabled ? rulesKey(body) : ''
  const s = useDarkFirst()
  useEffect(() => {
    if (key) load(key)
  }, [key])
  const retry = useCallback(() => load(key, true), [key])
  const mine = !!key && s.key === key
  return { key, data: mine ? s.data : null, status: !key ? 'idle' : mine ? s.status : 'loading', error: mine ? s.error : null, retry }
}

/** Which rule is on screen for this case (`base` until one is picked). */
export const shownRule = (s, key, base) => (key && s.pickKey === key && s.pick) || base

export const pickRule = (key, rule) => emit({ pick: rule, pickKey: key })

/** The rule's name on screen: the server's (a campus that can only switch off is labeled so), else the switch's. */
export const ruleLabel = (r, key) => r?.label || RULES.find((x) => x.key === key)?.label || ''

/** Under "keep the campus on": is everyone who lost power someone the operator cut to keep it on? (A storm's own
 *  victims, or a line that tripped anyway, are not: backend people_cut counts only the areas cut on purpose.) */
export const allCut = (r) => r?.key === 'firm' && !!r.firm_held && r.shed_mw > 0 && r.people_dark > 0 && (r.people_cut ?? 0) >= r.people_dark

/** The block registers while it is on screen; the map draws a rule only then. Leaving (a replay starts, the case
 *  changes) puts the map back on the case's own rule. */
export function useShowing(on) {
  useEffect(() => {
    if (!on) return undefined
    emit({ active: st.active + 1 })
    return () => emit({ active: Math.max(0, st.active - 1), ...(st.active <= 1 ? { pick: null, pickKey: '' } : null) })
  }, [on])
}

/** Ask for the rules as soon as the cascade lands (the block then opens with its numbers at the replay's end). */
export const prefetchRules = (body) => load(rulesKey(body))

// ------------------------------------------------------------------ words
/** "after the storm" (lines knocked out first) / "during the peak" / "during the heat wave" / "at this hour", from
 *  the case's storm and load level. */
export function whenText(lf, storm = false) {
  if (storm) return 'after the storm'
  const f = Number(lf) || 1
  if (f > 1.001) return 'during the heat wave'
  if (f >= 0.9) return 'during the peak'
  return 'at this hour'
}

// ------------------------------------------------------------------ taking the question to the meeting
// Opens the proposal's page (#/vote/<id>) and, once it has rendered, scrolls to "Ask before you vote" and opens the
// firm-or-flexible question (backend/demo/questions.json → id firm-or-flexible). The page has no deep link to one
// question, so this waits for it to render (up to 8 s) and falls back to the section.
const FIRM_Q = /cut back during a heat wave or an emergency/i
export function openFirmQuestion(id) {
  window.location.hash = `#/vote/${id}`
  const t0 = performance.now()
  const tick = () => {
    const sec = document.getElementById('vote-ask')
    if (!sec) {
      if (performance.now() - t0 < 8000) window.setTimeout(tick, 150)
      return
    }
    const d = [...sec.querySelectorAll('details')].find((el) => FIRM_Q.test(el.querySelector('summary')?.textContent || ''))
    if (d) d.open = true
    const calm = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
    ;(d || sec).scrollIntoView({ behavior: calm ? 'auto' : 'smooth', block: 'center' })
    d?.querySelector('summary')?.focus({ preventScroll: true })
  }
  window.setTimeout(tick, 150)
}
