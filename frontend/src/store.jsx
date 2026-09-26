import { createContext, useCallback, useContext, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { api, deleteScenario, listScenarios, runCascade, saveScenario, whatIf } from './api'
import { US_BBOX, fmt, headroomClass, loadClass, regionAt, setProjectionFor } from './geo'
import { buildSchedule } from './shell/cascadeSchedule'

// The app's shared state and actions. Every feature reads and changes the scenario through
// useOverload() — it never keeps its own copy of the grid, the case, or the results.
//
// A *case* is what the backend solves (backend/grid.py → CaseIn):
//   region           the state whose synthetic model is solved ('FL' default; 'US' = the national map, no case)
//   site + mw        the main data center (Campus mode)
//   extraSites       more data centers [{id, lat, lon, mw}] (AI-boom mode)
//   loadFactor       1.0 = the dataset's snapshot (a summer afternoon); the heat-wave clock scales it
//   trip             branch ids knocked out first (hurricane mode)
//   upgrades         {branch id: new rating MVA} (Fix it)
//   firm             every campus on firm service: kept on, the operator cuts other customers instead
//                    (false = flexible: the plain cascade, the campus's own line may trip)
// Any change re-runs the what-if (debounced) and clears the cascade.
//
// People without power (an estimate) come from the backend: lost MW x the state's people per MW
// (Census population / the state model's load). view.peopleMax is the counter's number.

const Ctx = createContext(null)
export const useOverload = () => useContext(Ctx)

// The main replay no longer uses a uniform step: it follows the blast schedule (shell/cascadeSchedule.js,
// exposed as `fx`). These stay for the features that still pace their own replays by step.
export const STEP_MS = 1400 // default step length (use stepMsFor(n))
// paced so the destruction can be watched: a cascade of n steps takes at least ~12 s, a step 1.1-1.8 s
export const stepMsFor = (n) => Math.round(Math.min(1800, Math.max(1100, 12000 / Math.max(n || 1, 1))))
export const HOMES_PER_MW = 700 // matches backend/powerflow.py; an estimate (~1.4 kW per home)
export const SEED_MARK = '(demo scenario)' // matches backend/seed.py

// "NAPLES 12" -> "Naples": the town a synthetic substation is named after (backend: powerflow.area_of)
export function townOf(name) {
  const base = String(name || '')
    .replace(/\s+\d+$/, '')
    .trim()
    .toLowerCase()
  return base.replace(/\b\w/g, (c) => c.toUpperCase())
}

// The substation a click connects to (the model's own rule: nearest, within 75 km), or null off the model.
function nearestSub(subs, lat, lon) {
  const k = Math.cos((lat * Math.PI) / 180)
  let best = null
  let bd = Infinity
  for (const s of subs) {
    const d = (s.lat - lat) ** 2 + ((s.lon - lon) * k) ** 2
    if (d < bd) {
      bd = d
      best = s
    }
  }
  return best && Math.sqrt(bd) * 111 <= 75 ? best : null
}
export const MAX_POINTS = 12 // the backend takes 12 data centers per case: the main one and eleven more

// The national map: no grid model of its own, just the state outlines (GridMap draws them).
const US_GRID = { meta: { region: 'US', region_name: 'the U.S.', bbox: US_BBOX, synthetic: true }, subs: [], branches: [] }
const regionQuery = (r) => `region=${encodeURIComponent(r)}`

export function OverloadProvider({ user, children }) {
  const [grid, setGrid] = useState(null)
  const [gridError, setGridError] = useState(null)

  // regions
  const [region, setRegionState] = useState('FL')
  const [regions, setRegions] = useState(null)
  const [regionsError, setRegionsError] = useState(null)
  const [regionLoading, setRegionLoading] = useState(false)
  const gridCache = useRef(new Map()) // region -> /api/grid payload
  const gridReq = useRef(0)
  const pendingPlace = useRef(null) // a drop waiting for its state's grid: {lat, lon, mw?}

  // the case
  const [site, setSite] = useState(null)
  const [mw, setMwState] = useState(500)
  const [extraSites, setExtraSitesState] = useState([])
  const [loadFactor, setLoadFactorState] = useState(1.0)
  const [trip, setTripState] = useState([])
  const [upgrades, setUpgradesState] = useState({})
  const [firm, setFirmState] = useState(false)

  // results
  const [result, setResult] = useState(null)
  const [solving, setSolving] = useState(false)
  const [whatifError, setWhatifError] = useState(null)
  const [cascade, setCascade] = useState(null)
  const [cascading, setCascading] = useState(false)
  const [cascadeError, setCascadeError] = useState(null)
  const [step, setStepState] = useState(0)
  const [playing, setPlaying] = useState(false)
  const [fx, setFx] = useState(null) // the replay on screen: {schedule, startedAt (performance.now()), from}; null when not playing

  // headroom heatmap (per region and load level)
  const [headroomOn, setHeadroomOn] = useState(false)
  const [headroomByLevel, setHeadroomByLevel] = useState({})
  const [headroomError, setHeadroomError] = useState(null)

  // UI
  const [mode, setMode] = useState('campus') // campus | hurricane | boom | fix
  const [mapTool, setMapTool] = useState(null) // a feature's pointer handlers for the map, or null (see GridMap)
  const [resetCount, setResetCount] = useState(0) // bumps on "Start over" and on a region change: features clear their own local state on it

  // saved scenarios (the demo account's)
  const [scenarios, setScenarios] = useState(undefined)
  const [scenarioError, setScenarioError] = useState(null)
  const [saving, setSaving] = useState(false)

  const latest = useRef(0)
  const cascadeReq = useRef(0)
  const mapRef = useRef(null)
  const focusedSite = useRef(null)

  const subById = useMemo(() => new Map((grid?.subs || []).map((s) => [s.id, s])), [grid])
  const branchById = useMemo(() => new Map((grid?.branches || []).map((b) => [b.id, b])), [grid])
  const branchIndex = useMemo(() => new Map((grid?.branches || []).map((b, i) => [b.id, i])), [grid])
  const subName = useCallback((id) => subById.get(id)?.name || `#${id}`, [subById])
  const areaOf = useCallback(
    (id) => {
      const s = subById.get(id)
      return s ? s.area || townOf(s.name) : `#${id}`
    },
    [subById],
  )
  const subPos = useCallback((id) => {
    const s = subById.get(id)
    return s ? [s.lon, s.lat] : null
  }, [subById])
  const focus = useCallback((points, center) => mapRef.current?.focus(points.filter(Boolean), center), [])
  const peoplePerMw = grid?.meta?.people_per_mw || 0
  const population = grid?.meta?.population || null

  // ------------------------------------------------------------------ loading
  // The region's grid (cached per region). The projection is set before the map sees the new grid.
  const loadGrid = useCallback(
    (code = region) => {
      const id = ++gridReq.current
      const show = (g) => {
        if (id !== gridReq.current) return
        setProjectionFor(g)
        setGridError(null)
        setGrid(g)
        setRegionLoading(false)
      }
      if (code === 'US') {
        show(US_GRID)
        return Promise.resolve(US_GRID)
      }
      const cached = gridCache.current.get(code)
      if (cached) {
        show(cached)
        return Promise.resolve(cached)
      }
      setRegionLoading(true)
      return api(`/api/grid?${regionQuery(code)}`)
        .then((g) => {
          gridCache.current.set(code, g)
          show(g)
          return g
        })
        .catch((err) => {
          if (id !== gridReq.current) return null
          setRegionLoading(false)
          setGridError(err)
          return null
        })
    },
    [region],
  )
  useEffect(() => {
    loadGrid(region)
  }, [loadGrid, region])

  const loadRegions = useCallback(
    () =>
      api('/api/regions')
        .then((r) => {
          setRegionsError(null)
          setRegions(r.regions)
        })
        .catch(setRegionsError),
    [],
  )
  useEffect(() => {
    loadRegions()
  }, [loadRegions])

  const loadScenarios = useCallback(
    () =>
      listScenarios()
        .then((list) => {
          setScenarioError(null)
          setScenarios(list)
        })
        .catch(setScenarioError),
    [],
  )
  useEffect(() => {
    if (user) loadScenarios()
  }, [user, loadScenarios])

  // ------------------------------------------------------------------ the case
  const caseBody = useMemo(() => {
    const body = {
      region,
      firm,
      load_factor: loadFactor,
      trip,
      upgrades,
      sites: extraSites.map(({ lat, lon, mw: m }) => ({ lat, lon, mw: m })),
    }
    if (site) Object.assign(body, { lat: site.lat, lon: site.lon, mw })
    return body
  }, [region, firm, site, mw, extraSites, loadFactor, trip, upgrades])
  // the national map has no model to solve, and a case waits for its own state's grid
  const gridRegion = grid ? grid.meta?.region || 'FL' : null
  const hasCase = region !== 'US' && gridRegion === region && !!(site || extraSites.length || trip.length || loadFactor !== 1.0)

  const clearCascade = useCallback(() => {
    cascadeReq.current++ // a cascade still in flight belongs to the old case; drop it when it lands
    setCascading(false)
    setCascade(null)
    setCascadeError(null)
    setPlaying(false)
    setStepState(0)
  }, [])

  // Re-solve whenever the case changes (debounced for sliders); only the newest answer lands.
  useEffect(() => {
    if (!hasCase) {
      // the last ingredient was removed: nothing to solve, clear the old answer
      latest.current++
      const t = setTimeout(() => setResult(null), 0)
      return () => clearTimeout(t)
    }
    const id = ++latest.current
    const t = setTimeout(() => {
      setSolving(true)
      whatIf(caseBody)
        .then((r) => {
          if (id !== latest.current) return
          setResult({ ...r, forSite: site }) // which click this answer belongs to (the map's connect pulse)
          setWhatifError(null)
          if (site && focusedSite.current !== site) {
            focusedSite.current = site
            // fly to the substation the campus plugs into, keeping its overloaded lines on screen
            focus(r.overloaded.flatMap((o) => [subPos(o.from), subPos(o.to)]), [r.sub_lon, r.sub_lat])
          }
        })
        .catch((err) => {
          if (id !== latest.current) return
          setResult(null)
          setWhatifError(err)
        })
        .finally(() => id === latest.current && setSolving(false))
    }, 120)
    return () => clearTimeout(t)
  }, [caseBody, hasCase, site, subPos, focus])

  // Play the cascade as one slow-motion blast (shell/cascadeSchedule.js): when playback starts from
  // step s the schedule is built once and published as `fx`; the map's effects and the counter run
  // off its clock, and `step` advances to each tier's step at that tier's END. Pausing, scrubbing
  // (setStep), clearing or a new cascade cancels the timers and clears fx.
  const stepRef = useRef(0)
  useLayoutEffect(() => {
    stepRef.current = step
  })
  useLayoutEffect(() => {
    const n = cascade?.steps?.length || 0
    if (!playing || !n) {
      setFx(null)
      return undefined
    }
    const from = stepRef.current >= n ? 0 : stepRef.current
    if (from !== stepRef.current) setStepState(0) // Play at the end replays from the top
    const schedule = buildSchedule(cascade, subById, branchById, from)
    const startedAt = performance.now()
    setFx({ schedule, startedAt, from })
    const timers = schedule.tiers.map((tier, i) =>
      setTimeout(() => {
        setStepState(tier.step)
        if (i === schedule.tiers.length - 1) setPlaying(false)
      }, tier.t1),
    )
    if (!schedule.tiers.length) setPlaying(false)
    return () => timers.forEach(clearTimeout)
  }, [playing, cascade, subById, branchById])

  // setters that also clear a cascade that no longer matches the case
  const placeHere = useCallback((lat, lon) => {
    setSite({ lat, lon })
    clearCascade()
  }, [clearCascade])
  const setMw = useCallback((v) => {
    setMwState(v)
    clearCascade()
  }, [clearCascade])
  const setExtraSites = useCallback((v) => {
    setExtraSitesState(v)
    clearCascade()
  }, [clearCascade])
  const setLoadFactor = useCallback((v) => {
    setLoadFactorState(v)
    clearCascade()
  }, [clearCascade])
  const setTrip = useCallback((v) => {
    setTripState(v)
    clearCascade()
  }, [clearCascade])
  const setUpgrades = useCallback((v) => {
    setUpgradesState(v)
    clearCascade()
  }, [clearCascade])
  const setFirm = useCallback((v) => {
    setFirmState(!!v)
    clearCascade()
  }, [clearCascade])
  const clearSite = useCallback(() => {
    setSite(null)
    focusedSite.current = null
    if (!extraSites.length && !trip.length && loadFactor === 1.0) setResult(null)
    clearCascade()
  }, [clearCascade, extraSites.length, trip.length, loadFactor])
  // every ingredient of the case and its results (Start over and a region change share it)
  const clearCase = useCallback(() => {
    latest.current++ // a what-if still in flight is dropped when it lands
    setSite(null)
    focusedSite.current = null
    setExtraSitesState([])
    setLoadFactorState(1.0)
    setTripState([])
    setUpgradesState({})
    setFirmState(false)
    setResult(null)
    setWhatifError(null)
    setSolving(false)
    clearCascade()
    setHeadroomOn(false)
    setMapTool(null)
    setMode('campus')
    setResetCount((n) => n + 1)
  }, [clearCascade])
  // "Start over": the whole case, any map tool, the camera back to the whole region
  const resetAll = useCallback(() => {
    clearCase()
    mapRef.current?.reset()
  }, [clearCase])

  // Switch to another state ('US' = the national map): clears the case like Start over, loads that
  // state's grid, and the map flies there. opts.place = [lat, lon] (and opts.mw) drops the campus
  // there once the grid is in. Returns false for an unknown code.
  const setRegion = useCallback(
    (code, opts = {}) => {
      const next = String(code || 'FL').toUpperCase()
      if (next !== 'US' && regions && !regions.some((r) => r.code === next)) return false
      pendingPlace.current = opts.place ? { lat: opts.place[0], lon: opts.place[1], mw: opts.mw } : null
      if (next !== region) {
        clearCase()
        setHeadroomError(null)
        setRegionState(next)
      }
      return true
    },
    [region, regions, clearCase],
  )
  // place a drop that was waiting for its state's grid
  useEffect(() => {
    const p = pendingPlace.current
    if (!p || gridRegion !== region) return
    pendingPlace.current = null
    if (p.mw) setMwState(p.mw)
    setMode('campus')
    placeHere(p.lat, p.lon)
  }, [gridRegion, region, placeHere, resetCount])

  // A click on the map. On the national map it opens the state under the click and drops the campus there.
  // Ctrl/Cmd+click: one more data center of the current size, on the substation the click connects to.
  // The first point of a case is the main one; a second point on the same substation is ignored.
  const addPoint = useCallback(
    (lat, lon) => {
      if (!site) return placeHere(lat, lon)
      if (extraSites.length + 1 >= MAX_POINTS) return
      const sub = nearestSub(grid?.subs || [], lat, lon)
      if (!sub || extraSites.some((c) => c.sub === sub.id)) return
      setExtraSites([...extraSites, { id: `p${Date.now().toString(36)}${extraSites.length}`, metro: townOf(sub.name), sub: sub.id, lat: sub.lat, lon: sub.lon, mw }])
    },
    [site, extraSites, grid, mw, placeHere, setExtraSites],
  )
  const place = useCallback(
    (lat, lon, opts = {}) => {
      if (region !== 'US') return opts.multi ? addPoint(lat, lon) : placeHere(lat, lon)
      const code = regionAt(lat, lon)
      if (code && code !== 'DC') setRegion(code, { place: [lat, lon] })
    },
    [region, placeHere, addPoint, setRegion],
  )

  const setStep = useCallback((s) => {
    setPlaying(false)
    setStepState(s)
  }, [])

  // Run the cascade for the current case; `extra` overrides case fields for this run (e.g. a hurricane's trip list).
  const startCascade = useCallback(
    async (extra = {}, pending = null) => {
      const id = ++cascadeReq.current
      setCascading(true)
      setCascadeError(null)
      try {
        const c = await (pending || runCascade({ ...caseBody, ...extra })) // `pending`: a cascade already computing (the hurricane starts it while the storm crosses)
        if (id !== cascadeReq.current) return null
        setCascade(c)
        setStepState(0)
        setPlaying(c.steps.length > 0)
        // pull the camera out to everything the cascade will touch
        const pts = c.sites.map((s) => [s.sub_lon, s.sub_lat])
        c.steps.forEach((st) => {
          st.tripped.forEach((bid) => {
            const b = branchById.get(bid)
            if (b) pts.push(subPos(b.from_sub), subPos(b.to_sub))
          })
          st.dark_subs.forEach((sid) => pts.push(subPos(sid)))
          st.newly_affected.forEach(([sid]) => pts.push(subPos(sid)))
          st.hits?.forEach((h) => h.subs.forEach((sid) => pts.push(subPos(sid)))) // every town the blast reaches
        })
        // centered on the main site when there is one; the zoom fits everything the cascade touches
        if (pts.length) focus(pts, c.sub_lat != null ? [c.sub_lon, c.sub_lat] : undefined)
        return c
      } catch (err) {
        if (id === cascadeReq.current) setCascadeError(err)
        return null
      } finally {
        if (id === cascadeReq.current) setCascading(false)
      }
    },
    [caseBody, branchById, subPos, focus],
  )

  // ------------------------------------------------------------------ headroom
  const levelKey = `${region}:${loadFactor.toFixed(2)}`
  const headroom = headroomByLevel[levelKey] || null
  // MW each substation can take, for a region and load level (cached; the heatmap uses the current one)
  const getHeadroom = useCallback(
    (lf = loadFactor, code = region) =>
      api(`/api/grid/headroom?load_factor=${encodeURIComponent(lf)}&${regionQuery(code)}`).then((h) => {
        setHeadroomByLevel((m) => ({ ...m, [`${code}:${Number(lf).toFixed(2)}`]: h.by_sub }))
        return h.by_sub
      }),
    [loadFactor, region],
  )
  // fetched when the heatmap is on and this region + level isn't cached; Retry clears the error, which refetches
  useEffect(() => {
    if (!headroomOn || headroom || headroomError || region === 'US') return
    getHeadroom().catch(setHeadroomError)
  }, [headroomOn, headroom, headroomError, region, getHeadroom])
  const fetchHeadroom = useCallback(() => setHeadroomError(null), [])
  const toggleHeadroom = useCallback(() => setHeadroomOn((on) => !on), [])

  // ------------------------------------------------------------------ scenarios
  const saveName = result?.sub_name ? `${result.sub_name} · ${fmt(mw)} MW`.slice(0, 80) : ''
  // saved scenarios are Florida's (the scenarios table has no region yet)
  const canSave = !!(user && region === 'FL' && site && result && !scenarios?.some((sc) => sc.name === saveName))
  const saveSite = useCallback(async () => {
    setSaving(true)
    setScenarioError(null)
    try {
      const created = await saveScenario({ name: saveName, lat: site.lat, lon: site.lon, mw: Math.round(mw) })
      setScenarios((list) => [...(list || []), created])
    } catch (err) {
      setScenarioError(err)
    } finally {
      setSaving(false)
    }
  }, [saveName, site, mw])
  const removeScenario = useCallback(async (id) => {
    try {
      await deleteScenario(id)
    } catch (err) {
      if (!/not found/i.test(err.message)) {
        setScenarioError(err)
        return
      }
    }
    setScenarios((list) => list.filter((sc) => sc.id !== id))
  }, [])
  const pickScenario = useCallback(
    (sc) => {
      const code = sc.region || 'FL'
      if (code !== region) {
        setRegion(code, { place: [sc.lat, sc.lon], mw: sc.mw })
        return
      }
      // a saved scenario is the same case every time: drop what the last case added (Strengthen's "Try it"
      // upgrades, a storm's knocked-out lines, Ctrl+click campuses); another state's scenario gets this from clearCase
      setUpgradesState({})
      setTripState([])
      setExtraSitesState([])
      setMwState(sc.mw)
      setMode('campus')
      placeHere(sc.lat, sc.lon)
    },
    [region, setRegion, placeHere],
  )

  // ------------------------------------------------------------------ what the map shows
  const view = useMemo(() => {
    if (!grid) return null
    const base = result ? result.loading_pct : grid.branches.map((b) => b.base_pct)
    const flow = result ? result.flow_mw : grid.branches.map((b) => b.base_flow)
    const lineClasses = new Array(grid.branches.length)
    const subClasses = {}
    const steps = cascade?.steps || []
    const out = new Set(trip) // knocked out before the cascade (hurricane)
    let dark = new Set()
    let affected = new Map() // sub id -> MW of existing load lost so far
    let flowNow = flow
    if (cascade && step > 0) {
      const tripped = new Set(trip)
      for (let j = 0; j < step; j++) {
        steps[j].tripped.forEach((id) => tripped.add(id))
        steps[j].dark_subs.forEach((id) => dark.add(id))
        steps[j].newly_affected.forEach(([id, lost]) => affected.set(id, lost))
      }
      const last = step >= steps.length
      const hot = new Map(steps[step - 1].hot.map((h) => [h.id, h.pct]))
      grid.branches.forEach((b, i) => {
        if (tripped.has(b.id)) lineClasses[i] = 'ln--tripped'
        else if (last) lineClasses[i] = loadClass(cascade.final_loading_pct[i])
        else if (hot.has(b.id)) lineClasses[i] = loadClass(hot.get(b.id))
        else lineClasses[i] = loadClass(Math.min(base[i], 79.9)) // not hot now: under 80 %
      })
      if (last) {
        affected = new Map(Object.entries(cascade.affected).map(([k, v]) => [Number(k), v]))
        flowNow = cascade.final_flow_mw
      }
    } else {
      grid.branches.forEach((b, i) => (lineClasses[i] = out.has(b.id) ? 'ln--tripped' : loadClass(base[i])))
      if (result?.affected) affected = new Map(Object.entries(result.affected).map(([k, v]) => [Number(k), v]))
    }
    // the towns the blast reached so far: their lights burn as embers (the people there are counted).
    // Not while the replay plays: its effects layer draws the embers then, and restyling ~100 lights
    // at every step's end would make the browser repaint the map under them.
    for (let j = 0; cascade && !playing && j < step; j++) (steps[j].hits || []).forEach((h) => h.subs.forEach((id) => (subClasses[id] = 'sub--hit')))
    // a substation that lost most of its load reads as dark; some of it, as dimmed
    affected.forEach((lost, id) => {
      const s = subById.get(id)
      if (!s) return
      if (dark.has(id) || lost >= 0.6 * Math.max(s.load_mw * loadFactor, 0.1)) dark.add(id)
      else subClasses[id] = 'sub--dim'
    })
    dark.forEach((id) => (subClasses[id] = 'sub--dark'))
    if (headroomOn && headroom) {
      grid.subs.forEach((s) => {
        const v = headroom[s.id]
        if (v !== undefined) subClasses[s.id] = headroomClass(v, mw)
      })
    }
    const cur = cascade && step > 0 ? steps[step - 1] : null
    // the counter never counts down mid-replay: the most people dark at any step shown so far
    let peopleMax = cur ? 0 : result?.people || 0
    for (let j = 0; cur && j < step; j++) peopleMax = Math.max(peopleMax, steps[j].people ?? 0)
    return {
      lineClasses,
      subClasses,
      flow: flowNow, // signed MW per branch for the flow animation
      affected, // Map sub id -> MW lost (for the towns feed)
      homes: cur ? cur.homes : result ? Math.round((result.lost_mw || 0) * HOMES_PER_MW) : 0,
      lostMw: cur ? cur.lost_mw : result?.lost_mw || 0,
      people: cur ? (cur.people ?? 0) : result?.people || 0, // people without power at the step on screen (estimate)
      peopleMax, // the counter's number: the peak so far (estimate)
      peopleFinal: cascade ? (cascade.people ?? null) : null, // where the cascade ends — show it in the result
      // the blast counter at the step on screen (estimates): everyone hit so far, each person once;
      // everyone in the areas that lost power so far; those people in homes (2.5 per home)
      // (an older engine without people_hit falls back to people_zone, then people; the last step
      // never shows less than the cascade's own total)
      peopleHit: cur
        ? Math.max(cur.people_hit ?? cur.people_zone ?? cur.people ?? 0, step >= steps.length ? (cascade.people_hit ?? cascade.people_zone ?? cascade.people ?? 0) : 0)
        : 0,
      peopleZone: cur ? (cur.people_zone ?? cur.people ?? 0) : 0,
      homesZone: cur ? (cur.homes_zone ?? 0) : 0,
      action: cur?.action || null, // the step on screen: 'storm' | 'trip' | 'shed' (firm: customers cut to hold a line)
      siteCutOff: !!(cascade && step >= steps.length && cascade.site_cut_off), // the campus itself lost power
    }
  }, [grid, result, cascade, step, playing, trip, headroomOn, headroom, mw, subById, loadFactor])

  const value = {
    user,
    // grid: /api/grid for the region — {meta: {region, region_name, bbox, center, load_mw, population,
    //   people_per_mw, population_source, synthetic, ...}, subs: [{id, name, area, lat, lon, load_mw, kv_max}],
    //   branches: [{id, from_sub, to_sub, kv, rate_mva, base_pct, base_flow}]}; 'US' → no subs/branches
    grid,
    gridError,
    loadGrid, // (code = region) → Promise<grid>
    // regions
    region, // 'FL' by default; any code from /api/regions; 'US' = the national map
    regions, // /api/regions → [{code, name, bbox, center, load_mw, population, people_per_mw, valid, ...}], null while loading
    regionsError,
    loadRegions,
    regionLoading, // true while a new state's grid loads (the old map stays up)
    setRegion, // (code, {place: [lat, lon], mw}?) → clears the case, loads the grid, the map flies there
    peoplePerMw, // this region's people per MW of lost load (estimate, from /api/grid meta)
    population, // this region's population (Census Vintage 2024, from /api/grid meta)
    // the case
    site,
    mw,
    extraSites,
    loadFactor,
    trip,
    upgrades,
    firm, // bool: every campus on firm service (kept on; the operator cuts other customers)
    place, // (lat, lon): drop the campus; on the national map, opens that state and drops it there
    clearSite,
    setMw,
    setExtraSites,
    setLoadFactor,
    setTrip,
    setUpgrades,
    setFirm, // (bool) → clears the cascade, re-solves
    caseBody, // what the backend solves: {region, firm, load_factor, trip, upgrades, sites, lat?, lon?, mw?}
    // results: result = /api/grid/whatif (…, people, people_per_mw, population, sub_area, firm);
    // cascade = /api/grid/cascade (steps[{…, action, people, shed_mw}], people, site_cut_off, firm, firm_held, shed_mw)
    result,
    solving,
    whatifError,
    cascade,
    cascading,
    cascadeError,
    step,
    playing,
    setStep,
    setPlaying,
    startCascade,
    clearCascade,
    // the replay while it plays: {schedule (shell/cascadeSchedule.js buildSchedule), startedAt
    // (performance.now() at play), from (the step it started from)}; null when paused or done
    fx,
    view, // {lineClasses, subClasses, flow, affected, homes, lostMw, people, peopleMax, peopleFinal, peopleHit, peopleZone, homesZone, action, siteCutOff}
    // headroom
    headroomOn,
    headroom,
    headroomError,
    toggleHeadroom,
    fetchHeadroom,
    getHeadroom, // (loadFactor?, region?) → Promise<{sub id: MW}>; cached for the heatmap too
    // scenarios
    scenarios,
    scenarioError,
    loadScenarios,
    saving,
    canSave,
    saveSite,
    removeScenario,
    pickScenario,
    // UI + map
    mode,
    setMode,
    mapTool,
    setMapTool,
    resetAll,
    resetCount,
    mapRef,
    focus,
    // lookups
    subById,
    branchById,
    branchIndex,
    subName,
    areaOf, // (sub id) → the town it's named after, e.g. "Naples"
    subPos,
  }
  // dev only: browser checks (scratch/*.py) can drive the store, e.g. window.__overload.setRegion('TX')
  useEffect(() => {
    if (import.meta.env.DEV) window.__overload = value
  })
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}
