import { api } from '../../api'
import { fmt } from '../../geo'
import { moneyRange } from '../cost/money'

// The site report (backend/sitereport.py): for a campus of `mw` at a point, the nearest substations with their
// room and limits, the upgrades that would let the full size in (verified by a re-run), an N-1 screen, and what
// the cascade does to people if it is built anyway. All numbers are estimates on a synthetic grid model.
export const getSiteReport = (body) => api('/api/site/report', { method: 'POST', body })

// The request for the case on screen (the campus's main point, size, load level and service).
export const reportBody = ({ region, site, mw, loadFactor, firm }) => ({
  region,
  lat: site.lat,
  lon: site.lon,
  mw,
  load_factor: loadFactor,
  firm: !!firm,
})

export const reportKey = (b) => [b.region, b.lat.toFixed(5), b.lon.toFixed(5), b.mw, b.load_factor, b.firm ? 1 : 0].join('|')

// 240 -> "240 MW", 1,500 -> "1,500 MW", 3.5 -> "3.5 MW"
export function fmtMw(x) {
  const v = Number(x) || 0
  if (v >= 10 || Math.abs(v - Math.round(v)) < 0.05) return `${fmt(v)} MW`
  return `${v.toFixed(1)} MW`
}

// A substation's room: "557 MW", or "50,000+ MW" when no line limits it at any size the model takes
export const fmtRoom = (row) => (row.headroom_unbounded || row.headroom_mw >= 50000 ? '50,000+ MW' : fmtMw(row.headroom_mw))

export const cost = (up) => moneyRange(up.cost_low_usd, up.cost_high_usd)

// The plain answer for one row: {kind: 'fits' | 'fix' | 'no', text}
export function rowVerdict(row) {
  if (row.fits) return { kind: 'fits', text: 'Fits' }
  if (row.upgrade?.verified) return { kind: 'fix', text: `Fits after upgrades (${cost(row.upgrade)})` }
  return { kind: 'no', text: "Doesn't fit" }
}

// How one N-1 loss came out, in words
export function n1Result(l) {
  if (l.secure) return { kind: 'ok', text: 'Holds' }
  const what = l.lost_mw >= 0.5 && !l.overloaded_after ? 'customers lose power' : 'over its limit'
  return l.campus_adds ? { kind: 'bad', text: `${what}: the campus tips it` } : { kind: 'bad', text: `${what}: already so without the campus` }
}

// The report as plain Markdown text (the "Download as text" button)
export function reportToMarkdown(r) {
  const s = r.site
  const out = []
  out.push(`# Site report: ${fmtMw(s.mw)} near ${s.nearest_town}, ${s.region_name}`)
  out.push(`*Synthetic grid model (Breakthrough Energy / Texas A&M, CC-BY 4.0). A screening estimate, not an interconnection study and not any utility's network.*`)
  out.push(`*Every figure is computed by the power-flow engine. No AI wrote or proposed anything in this report.*`)
  out.push('')
  out.push(`Point: ${s.lat}, ${s.lon} · load level ${Math.round(s.load_factor * 100)} % of the model's snapshot · ${s.firm ? 'firm' : 'flexible'} service`)
  out.push('')
  out.push(`**${r.verdict}**`)
  out.push('')
  out.push('## Nearest substations')
  out.push('')
  out.push('| # | Substation | km | kV | Room | Result |')
  out.push('|---|---|---|---|---|---|')
  for (const x of r.substations) out.push(`| ${x.rank} | ${x.name}${x.id === r.best_id ? ' (best)' : ''} | ${x.distance_km} | ${x.kv} | ${fmtRoom(x)} | ${rowVerdict(x).text} |`)
  out.push('')
  for (const x of r.substations) {
    out.push(`### ${x.rank}. ${x.name}`)
    if (x.limiting) {
      const l = x.limiting
      out.push(`- Limiting element: ${l.label} (${l.kv} kV, ${l.rating_mva} MVA), ${l.loading_pct_at_mw} % loaded at ${fmtMw(s.mw)}${l.limits_headroom ? '; it sets the room' : ''}`)
    }
    out.push(x.fits ? `- Takes the full ${fmtMw(s.mw)} with no upgrades.` : `- Right-size: takes up to ${fmtMw(x.right_size_mw)} with no upgrades.`)
    const u = x.upgrade
    if (u) {
      out.push(`- Upgrades for the full ${fmtMw(s.mw)}: ${u.count} ${u.count === 1 ? 'line or transformer' : 'lines and transformers'}, ${fmt(u.mva_added)} MVA added, about ${cost(u)}. ${u.verified ? 'Verified by re-running the model with them.' : u.reason || 'Not verified.'}`)
      for (const ln of u.lines.slice(0, 8)) out.push(`  - ${ln.label} (${ln.kv} kV): ${ln.old_mva} to ${ln.new_mva} MVA, ${moneyRange(ln.cost_low_usd, ln.cost_high_usd)}`)
    }
    out.push('')
  }
  const n1 = r.n_minus_1
  if (n1) {
    out.push(`## One line out (N-1) at ${n1.substation.name}`)
    out.push('')
    out.push(n1.summary)
    out.push('')
    for (const l of n1.lines) out.push(`- Lose ${l.label} (${l.kv} kV, ${l.pct_before} % before): worst line ${l.worst_label} at ${l.worst_pct} %. ${n1Result(l).text}.`)
    out.push('')
  }
  const b = r.if_built_anyway
  if (b) {
    out.push('## If it is built anyway')
    out.push('')
    out.push(b.summary)
    out.push('')
    for (const x of b.sites) out.push(`- At ${x.substation.name}: ${x.steps} steps, about ${fmt(x.people_hit)} people hit, about ${fmt(x.people_lost)} without power where it settles, ${fmt(x.lost_mw)} MW of existing load lost (estimates).`)
    out.push('')
  }
  out.push('## Method and assumptions')
  out.push('')
  out.push(r.method)
  out.push('')
  for (const a of r.assumptions) out.push(`- ${a}`)
  out.push('')
  out.push('## Sources')
  out.push('')
  for (const x of r.sources) out.push(`- ${x.name}: ${x.url}`)
  out.push('')
  out.push(r.disclaimer)
  return out.join('\n')
}

// Save text as a file (a Blob and a temporary link)
export function downloadText(name, text) {
  const url = URL.createObjectURL(new Blob([text], { type: 'text/markdown;charset=utf-8' }))
  const a = document.createElement('a')
  a.href = url
  a.download = name
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}
