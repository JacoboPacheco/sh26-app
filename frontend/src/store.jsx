import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react'
import { deleteScenario, getGrid, getHeadroom, listScenarios, runCascade, saveScenario, whatIf } from './api'
import { fmt, headroomClass, loadClass } from './geo'

// The app's shared state and actions. Every feature reads and changes the scenario through
// useOverload() — it never keeps its own copy of the grid, the case, or the results.
//
// A *case* is what the backend solves (backend/grid.py → CaseIn):
//   site + mw        the main data center (Campus mode)
//   extraSites       more data centers [{id, lat, lon, mw}] (AI-boom mode)
//   loadFactor       1.0 = the dataset's snapshot (a summer afternoon); the heat-wave clock scales it
//   trip             branch ids knocked out first (hurricane mode)
//   upgrades         {branch id: new rating MVA} (Fix it)
// Any change re-runs the what-if (debounced) and clears the cascade.

const Ctx = createContext(null)
export const useOverload = () => useContext(Ctx)

export const STEP_MS = 600 // one cascade step on screen
export const HOMES_PER_MW = 700 // matches backend/powerflow.py; an estimate (~1.4 kW per home)
export const SEED_MARK = '(demo scenario)' // matches backend/seed.py

// "NAPLES 12" -> "Naples": the town a synthetic substation is named after
export function townOf(name) {
  const base = String(name || '')
    .replace(/\s+\d+$/, '')
    .trim()
    .toLowerCase()
  return base.replace(/\b\w/g, (c) => c.toUpperCase())
}

export function OverloadProvider({ user, children }) {
  const [grid, setGrid] = useState(null)
  const [gridError, setGridError] = useState(null)

  // the case
  const [site, setSite] = useState(null)
  const [mw, setMwState] = useState(500)
  const [extraSites, setExtraSitesState] = useState([])
  const [loadFactor, setLoadFactorState] = useState(1.0)
  const [trip, setTripState] = useState([])
  const [upgrades, setUpgradesState] = useState({})

  // results
  const [result, setResult] = useState(null)
  const [solving, setSolving] = useState(false)
  const [whatifError, setWhatifError] = useState(null)
  const [cascade, setCascade] = useState(null)
  const [cascading, setCascading] = useState(false)
  const [cascadeError, setCascadeError] = useState(null)
  const [step, setStepState] = useState(0)
  const [playing, setPlaying] = useState(false)

  // headroom heatmap (per load level)
  const [headroomOn, setHeadroomOn] = useState(false)
  const [headroomByLevel, setHeadroomByLevel] = useState({})
  const [headroomError, setHeadroomError] = useState(null)

  // UI
  const [mode, setMode] = useState('campus') // campus | hurricane | boom | fix
  const [mapTool, setMapTool] = useState(null) // a feature's pointer handlers for the map, or null (see GridMap)
  const [resetCount, setResetCount] = useState(0) // bumps on "Start over": features clear their own local state on it

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
  const subPos = useCallback((id) => {
    const s = subById.get(id)
    return s ? [s.lon, s.lat] : null
  }, [subById])
  const focus = useCallback((points, center) => mapRef.current?.focus(points.filter(Boolean), center), [])

  // ------------------------------------------------------------------ loading
  const loadGrid = useCallback(
    () =>
      getGrid()
        .then((g) => {
          setGridError(null)
          setGrid(g)
        })
        .catch(setGridError),
    [],
  )
  useEffect(() => {
    loadGrid()
  }, [loadGrid])

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
    const body = { load_factor: loadFactor, trip, upgrades, sites: extraSites.map(({ lat, lon, mw: m }) => ({ lat, lon, mw: m })) }
    if (site) Object.assign(body, { lat: site.lat, lon: site.lon, mw })
    return body
  }, [site, mw, extraSites, loadFactor, trip, upgrades])
  const hasCase = !!(site || extraSites.length || trip.length || loadFactor !== 1.0)

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

  // Play the cascade one step at a time.
  useEffect(() => {
    if (!playing || !cascade) return undefined
    const t = setTimeout(() => {
      const next = step + 1
      setStepState(next)
      if (next >= cascade.steps.length) setPlaying(false)
    }, STEP_MS)
    return () => clearTimeout(t)
  }, [playing, step, cascade])

  // setters that also clear a cascade that no longer matches the case
  const place = useCallback((lat, lon) => {
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
  const clearSite = useCallback(() => {
    setSite(null)
    focusedSite.current = null
    if (!extraSites.length && !trip.length && loadFactor === 1.0) setResult(null)
    clearCascade()
  }, [clearCascade, extraSites.length, trip.length, loadFactor])
  // "Start over": every ingredient of the case, the results, any map tool, the camera
  const resetAll = useCallback(() => {
    latest.current++ // a what-if still in flight is dropped when it lands
    setSite(null)
    focusedSite.current = null
    setExtraSitesState([])
    setLoadFactorState(1.0)
    setTripState([])
    setUpgradesState({})
    setResult(null)
    setWhatifError(null)
    setSolving(false)
    clearCascade()
    setHeadroomOn(false)
    setMapTool(null)
    setMode('campus')
    setResetCount((n) => n + 1)
    mapRef.current?.reset()
  }, [clearCascade])

  const setStep = useCallback((s) => {
    setPlaying(false)
    setStepState(s)
  }, [])

  // Run the cascade for the current case; `extra` overrides case fields for this run (e.g. a hurricane's trip list).
  const startCascade = useCallback(
    async (extra = {}) => {
      const id = ++cascadeReq.current
      setCascading(true)
      setCascadeError(null)
      try {
        const c = await runCascade({ ...caseBody, ...extra })
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
  const levelKey = loadFactor.toFixed(2)
  const headroom = headroomByLevel[levelKey] || null
  // fetched when the heatmap is on and this level isn't cached; Retry clears the error, which refetches
  useEffect(() => {
    if (!headroomOn || headroom || headroomError) return
    const key = loadFactor.toFixed(2)
    getHeadroom(loadFactor)
      .then((h) => setHeadroomByLevel((m) => ({ ...m, [key]: h.by_sub })))
      .catch(setHeadroomError)
  }, [headroomOn, headroom, headroomError, loadFactor])
  const fetchHeadroom = useCallback(() => setHeadroomError(null), [])
  const toggleHeadroom = useCallback(() => setHeadroomOn((on) => !on), [])

  // ------------------------------------------------------------------ scenarios
  const saveName = result?.sub_name ? `${result.sub_name} · ${fmt(mw)} MW`.slice(0, 80) : ''
  const canSave = !!(user && site && result && !scenarios?.some((sc) => sc.name === saveName))
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
  const pickScenario = useCallback((sc) => {
    setMwState(sc.mw)
    setMode('campus')
    place(sc.lat, sc.lon)
  }, [place])

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
    return {
      lineClasses,
      subClasses,
      flow: flowNow, // signed MW per branch for the flow animation
      affected, // Map sub id -> MW lost (for the towns feed)
      homes: cur ? cur.homes : result ? Math.round((result.lost_mw || 0) * HOMES_PER_MW) : 0,
      lostMw: cur ? cur.lost_mw : result?.lost_mw || 0,
    }
  }, [grid, result, cascade, step, trip, headroomOn, headroom, mw, subById, loadFactor])

  const value = {
    user,
    grid,
    gridError,
    loadGrid,
    // the case
    site,
    mw,
    extraSites,
    loadFactor,
    trip,
    upgrades,
    place,
    clearSite,
    setMw,
    setExtraSites,
    setLoadFactor,
    setTrip,
    setUpgrades,
    caseBody,
    // results
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
    view,
    // headroom
    headroomOn,
    headroom,
    headroomError,
    toggleHeadroom,
    fetchHeadroom,
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
    subPos,
  }
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}
