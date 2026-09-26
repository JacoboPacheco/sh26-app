// AI-boom mode: the siting agent's steps on the map (owned by the boom track).
//
// As the trace reveals each step, the campuses that step ran with become the case's extra sites
// (useOverload().setExtraSites), added one by one, so the map's squares, the store's what-if and its
// red lines follow the agent: what the engine answered Gemini is what the map shows. When the plan is
// in and revealed, its campuses and its upgrades are put on the map (the lines go calm: the fix).
// Only for runs the AI boom panel started (origin 'boom'), only on the run's own state, and each step
// only once (a remount or an unrelated re-render never puts it back after the user changed the case).
import { useEffect, useSyncExternalStore } from 'react'
import { useOverload } from '../../store'
import { getPlanner, resetPlanner, revealed, usePlanner } from '../planner/plannerStore'
import { frame } from './boomData'

const DROP_MS = 280 // one campus after another

let applied = null // the last thing put on the map: `${runId}:${step n | 'plan' | 'alt'}`
let framed = new Set() // what was framed: `${runId}:${'look' | 'sites' | 'done'}`
let timers = []
let seenReset = null // the store's resetCount this module last saw (survives the map remounting)

// which version of a finished plan is on the map: 'plan' (with its upgrades) or 'alt' (without)
let version = 'plan'
const subs = new Set()
const setVersion = (v) => {
  version = v
  subs.forEach((f) => f())
}
export const useShownVersion = () =>
  useSyncExternalStore(
    (f) => {
      subs.add(f)
      return () => subs.delete(f)
    },
    () => version,
  )

const reduced = () => typeof window !== 'undefined' && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches

const campus = (s) => ({ id: `ai-${s.sub}`, metro: s.town, sub: s.sub, lat: s.lat, lon: s.lon, mw: s.mw })

function sameUps(a = {}, b = {}) {
  const ka = Object.keys(a)
  return ka.length === Object.keys(b).length && ka.every((k) => Math.abs(Number(a[k]) - Number(b[k])) < 0.05)
}

// upgrades as the case takes them ({branch id: new MVA}) from a step's upgrade_lines
export const upsOf = (lines) => Object.fromEntries((lines || []).map((u) => [String(u.id), u.new_mva]))

function stop() {
  timers.forEach(clearTimeout)
  timers = []
}

// Put these campuses (and upgrades) on the map: the ones already there keep their square and take
// the new size, the others drop in one by one.
function put(o, sites, ups) {
  stop()
  const want = new Map(sites.map((s) => [s.sub, s]))
  const now = o.extraSites
  const keep = now.filter((c) => want.has(c.sub)).map((c) => ({ ...c, mw: want.get(c.sub).mw }))
  const have = new Set(keep.map((c) => c.sub))
  const add = sites.filter((s) => !have.has(s.sub)).map(campus)
  const same = keep.length === now.length && keep.every((c, i) => c.mw === now[i].mw)
  if (!sameUps(o.upgrades, ups)) o.setUpgrades(ups)
  if (reduced()) {
    if (add.length || !same) o.setExtraSites([...keep, ...add])
    return
  }
  if (!same) o.setExtraSites(keep)
  add.forEach((c, i) => {
    timers.push(
      setTimeout(() => o.setExtraSites((list) => (list.some((x) => x.sub === c.sub) ? list : [...list, c])), (i + (same ? 0 : 1)) * DROP_MS + 40),
    )
  })
}

// Show a finished plan's version: 'plan' (its sites and upgrades) or 'alt' (the most the same sites
// take with no upgrade). From the panel's buttons.
export function showPlanVersion(o, p, v) {
  const r = p.result
  if (!r?.plan) return
  const alt = v === 'alt' && r.without_upgrades
  setVersion(alt ? 'alt' : 'plan')
  applied = `${p.runId}:${alt ? 'alt' : 'plan'}`
  put(o, alt ? r.without_upgrades.sites : r.plan.sites, alt ? {} : r.plan.upgrades || {})
}

// Before a run: an empty case for the agent (its plan is checked on its own campuses alone).
export function clearForAgent(o) {
  stop()
  applied = null
  framed = new Set()
  setVersion('plan')
  if (o.site) o.clearSite()
  if (o.trip.length) o.setTrip([])
  if (Object.keys(o.upgrades).length) o.setUpgrades({})
  if (o.extraSites.length) o.setExtraSites([])
}

// The latest revealed step whose campuses have sizes (a what-if, a move, a proposal…)
export function lastPlaced(steps) {
  for (let i = steps.length - 1; i >= 0; i--) {
    const s = steps[i]
    if (s.sites?.length && s.sites.every((x) => x.mw != null)) return s
  }
  return null
}

// Called once, from the boom map layer (mounted whenever the map is).
export function useAgentSync() {
  const o = useOverload()
  const p = usePlanner()
  const { region, grid, resetCount, focus, mapRef } = o
  const gridRegion = grid ? grid.meta?.region || 'FL' : null

  // Start over, or another state: the agent's run no longer describes the map
  useEffect(() => {
    if (seenReset === null) seenReset = resetCount
    if (resetCount === seenReset) return
    seenReset = resetCount
    if (getPlanner().origin === 'boom') {
      stop()
      applied = null
      framed = new Set()
      resetPlanner()
    }
  }, [resetCount])

  useEffect(() => {
    // A stale render: Start over (the effect above) reset the run in this same commit, so `p` still
    // shows the old run. Putting it on the map again would bring back the campuses just cleared.
    const cur = getPlanner()
    if (cur.runId !== p.runId || cur.status === 'idle') return
    if (p.origin !== 'boom' || p.status === 'idle' || !p.request) return
    if (region !== p.request.region || gridRegion !== region) return
    const shown = p.steps.slice(0, p.shown)
    const done = revealed(p) && p.result?.plan ? p.result : null

    // the camera: where the agent looks first, then its campuses, then the plan
    // (each once per run: the first candidates, the first campuses, the plan)
    const look = shown.find((s) => s.tool === 'headroom_top' && s.sites?.length)
    const placed = lastPlaced(shown)
    const want = done ? 'done' : placed ? 'sites' : look ? 'look' : null
    if (want && !framed.has(`${p.runId}:${want}`)) {
      framed.add(`${p.runId}:${want}`)
      if (framed.size > 30) framed = new Set([...framed].slice(-10))
      const pts = (want === 'done' ? done.plan.sites : want === 'sites' ? placed.sites : look.sites).map((s) => [s.lon, s.lat])
      frame(pts, focus, mapRef)
    }

    let key
    let sites
    let ups = {}
    if (done) {
      const alt = version === 'alt' && done.without_upgrades
      key = `${p.runId}:${alt ? 'alt' : 'plan'}`
      sites = alt ? done.without_upgrades.sites : done.plan.sites
      ups = alt ? {} : done.plan.upgrades || {}
    } else {
      const last = lastPlaced(shown)
      if (!last) return
      key = `${p.runId}:${last.n}`
      sites = last.sites
      // Gemini's finished proposal carries its re-ratings; a what-if or an asked-for fix runs without them
      if (last.tool === 'finish') ups = upsOf(last.upgrade_lines)
    }
    if (applied === key) return
    applied = key
    put(o, sites, ups)
  }, [p, region, gridRegion, o, focus, mapRef])
}
