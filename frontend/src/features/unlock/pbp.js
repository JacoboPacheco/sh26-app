// "Watch it get built", the play-by-play (PlayByPlay.jsx over the page, PbpLayer.jsx on the map): pure helpers that
// read the narration script (backend/narrate.py: intro, one slide per PACKAGE, closing) against the capacity plan on
// screen. The script names the packages (their campus steps, cost, who grouped them); the geometry comes from the
// plan's own steps, so the map draws exactly the upgrades the meter and the plan count.
import { blockOf } from './capacity'

// every line and transformer a run of steps raises, once (its latest rating), in the order they are first raised
export function projectsOf(m, stepNs) {
  const by = new Map()
  for (const n of stepNs) {
    const st = m?.steps?.[n - 1]
    if (!st) continue
    for (const p of st.projects) {
      const was = by.get(p.branch_id)
      by.set(p.branch_id, was ? { ...was, rating_after_mva: p.rating_after_mva } : { ...p, firstAt: st.n })
    }
  }
  return [...by.values()]
}

export const midOf = (p) => (p.mid ? [p.mid[1], p.mid[0]] : [(p.from.lon + p.to.lon) / 2, (p.from.lat + p.to.lat) / 2])
export const ends = (p) => [
  [p.from.lon, p.from.lat],
  [p.to.lon, p.to.lat],
]

// The packages with their geometry: [{...script package, projects, sites, center:[lon,lat]}]
export function packagesOf(m, script) {
  if (!m || !script?.packages) return []
  return script.packages.map((pk) => {
    const projects = projectsOf(m, pk.steps)
    const sites = pk.steps.map((n) => m.steps[n - 1]).filter(Boolean)
    const pts = projects.length ? projects.map(midOf) : sites.map((st) => [st.site.lon, st.site.lat])
    const center = pts.length ? [pts.reduce((a, q) => a + q[0], 0) / pts.length, pts.reduce((a, q) => a + q[1], 0) / pts.length] : null
    return { ...pk, projects, sites, center }
  })
}

// The weak points this build runs into (what stops the next campus first): the script's, with their loading today,
// else the plan's own limits (while the script is on its way)
export function problemsOf(m, script, target) {
  if (script?.problems) return script.problems
  if (!m) return []
  const out = []
  const seen = new Set()
  const add = (b, n) => {
    if (!b || seen.has(b.branch_id)) return
    seen.add(b.branch_id)
    out.push({ ...b, stops: n, base_pct: null, fixed_in: null })
  }
  const fb = m.first_block
  if (fb && fb.at_campus === m.today + 1) add(fb, m.today + 1)
  for (const st of m.steps.slice(m.today, Math.max(m.today, target))) add(blockOf(st), st.n)
  return out.slice(0, 6)
}

// which package a beat is on: 0 in the intro, the package's number, K + 1 in the closing and the final frame
export function beatPackage(slide, k, final) {
  if (final || slide?.kind === 'close') return k + 1
  if (slide?.kind === 'package') return slide.package || 0
  return 0
}

// The camera frame for a beat: its points, widened so what it shows sits clear of the beats and the scoreboard (top),
// the beat card (left on a wide screen, top on a phone) and the captions (bottom).
export function framed(points) {
  if (!points.length) return points
  let wide = false
  try {
    wide = window.matchMedia('(min-width: 861px)').matches
  } catch {
    wide = false
  }
  const lons = points.map((q) => q[0])
  const lats = points.map((q) => q[1])
  const w = Math.max(Math.max(...lons) - Math.min(...lons), 0.8)
  const h = Math.max(Math.max(...lats) - Math.min(...lats), 0.7)
  // a phone: the map is tall, the beat card sits over its top and the captions over its bottom
  if (!wide) return [...points, [Math.min(...lons), Math.min(...lats) - h * 0.2], [Math.max(...lons), Math.max(...lats) + h * 0.42]]
  // the frame spans the map's height on a wide screen: about 16 % of it above for the beats and the scoreboard, 28 %
  // below for the captions, a little on the left for the beat card
  return [...points, [Math.min(...lons) - w * 0.22, Math.min(...lats) - h * 0.5], [Math.max(...lons), Math.max(...lats) + h * 0.28]]
}

// where a package's cost tag is pinned: its costliest upgrade
export function anchorOf(pk) {
  const top = [...(pk.projects || [])].sort((a, b) => (b.cost?.high || 0) - (a.cost?.high || 0))[0]
  return top ? midOf(top) : pk.center
}

// "Campus 3", "Campuses 5–14"
export const campusesLabel = (pk) => (pk.first === pk.last ? `Campus ${pk.first}` : `Campuses ${pk.first}–${pk.last}`)
