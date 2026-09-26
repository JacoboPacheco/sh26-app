import { fmt } from '../../geo'

// Words and numbers shared by the catalog's components.

export const mwText = (mw) => (mw >= 1000 ? `${(mw / 1000).toFixed(mw % 1000 === 0 ? 0 : 1)} GW` : `${fmt(mw)} MW`)

// 53,001 -> "53k", 1,240,000 -> "1.2M"
export function compact(n) {
  if (n == null) return '–'
  if (n >= 1e6) return `${(n / 1e6).toFixed(n >= 1e7 ? 0 : 1)}M`
  if (n >= 1e4) return `${Math.round(n / 1e3)}k`
  if (n >= 1e3) return `${(n / 1e3).toFixed(1)}k`
  return fmt(n)
}

export const STATUS_LABEL = {
  operating: 'Operating',
  'under construction': 'Under construction',
  announced: 'Announced',
  'paused/canceled': 'Paused or canceled',
}
export const statusLabel = (s) => STATUS_LABEL[s] || (s ? s[0].toUpperCase() + s.slice(1) : 'Unknown')

// The chip on every row: what the campus does to its state's synthetic model, tested alone.
export function verdictOf(test) {
  if (!test) return { key: 'pending', label: 'Testing…', title: 'Being tested on its state’s synthetic model' }
  if (!test.tested) return { key: 'untested', label: 'Not tested', title: test.reason }
  if (test.verdict === 'outage')
    return {
      key: 'outage',
      label: `${compact(test.people)} people (est.)`,
      title: `Lines trip on the synthetic model and about ${fmt(test.people)} people lose power (estimate)`,
    }
  if (test.verdict === 'overloads')
    return {
      key: 'overloads',
      label: 'Over limit',
      title: `${test.over_text || test.overloaded} over ${test.overloaded === 1 ? 'its limit' : 'their limits'} on the synthetic model; nobody else loses power`,
    }
  return { key: 'fits', label: 'Fits', title: `Nothing goes over its limit on the synthetic model (room for ${fmt(test.headroom_mw)} MW)` }
}

// risk order: most people cut (estimate), then most lines over limit, then the biggest shortfall of room
export function riskScore(e) {
  const t = e.test
  if (!t || !t.tested) return [-1, -1, -1]
  return [t.people, t.overloaded, (e.mw || 0) - (t.headroom_mw || 0)]
}

export function placeLine(e) {
  const where = [e.city, e.state].filter(Boolean).join(', ')
  return where || e.state_name
}
