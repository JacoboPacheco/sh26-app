import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { deleteScenario, getGrid, getHeadroom, listScenarios, runCascade, saveScenario, whatIf } from './api'
import { fmt, headroomClass, loadClass } from './geo'
import GridMap from './GridMap'
import Layout from './Layout'
import { DataCenterCard, HeadroomCard, ScenarioCard, SiteCard } from './Panels'
import { Badge, ErrorBanner, Loading } from './ui'
import useAuth from './useAuth'

const STEP_MS = 600 // one cascade step on screen

function App() {
  // Keep this call: with VITE_DEMO_EMAIL/VITE_DEMO_PASSWORD set (frontend/.env) it signs
  // in as the seeded demo account on load, so per-user features need no login screen.
  const { user } = useAuth()

  const [grid, setGrid] = useState(null)
  const [gridError, setGridError] = useState(null)
  const [mw, setMw] = useState(500)
  const [site, setSite] = useState(null) // where the data center was dropped {lat, lon}
  const [result, setResult] = useState(null) // the what-if at `site`
  const [solving, setSolving] = useState(false)
  const [whatifError, setWhatifError] = useState(null)
  const [cascade, setCascade] = useState(null)
  const [cascading, setCascading] = useState(false)
  const [cascadeError, setCascadeError] = useState(null)
  const [step, setStep] = useState(0)
  const [playing, setPlaying] = useState(false)
  const [headroomOn, setHeadroomOn] = useState(false)
  const [headroom, setHeadroom] = useState(null)
  const [headroomError, setHeadroomError] = useState(null)
  const [scenarios, setScenarios] = useState(undefined)
  const [scenarioError, setScenarioError] = useState(null)
  const [saving, setSaving] = useState(false)

  const subById = useMemo(() => new Map((grid?.subs || []).map((s) => [s.id, s])), [grid])
  const branchById = useMemo(() => new Map((grid?.branches || []).map((b) => [b.id, b])), [grid])
  const subName = useCallback((id) => subById.get(id)?.name || `#${id}`, [subById])
  const subPos = useCallback((id) => {
    const s = subById.get(id)
    return s ? [s.lon, s.lat] : null
  }, [subById])
  const latest = useRef(0) // newest what-if request
  const cascadeReq = useRef(0) // newest cascade request
  const mapRef = useRef(null)
  const focusedSite = useRef(null) // the site the camera last flew to (the slider doesn't move it)

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

  // Re-solve whenever the site or the size changes (debounced for the slider); only the newest answer lands.
  useEffect(() => {
    if (!site) return undefined
    const id = ++latest.current
    const t = setTimeout(() => {
      setSolving(true)
      whatIf({ ...site, mw })
        .then((r) => {
          if (id !== latest.current) return
          setResult(r)
          setWhatifError(null)
          if (focusedSite.current !== site) {
            focusedSite.current = site
            // fly to the site and the lines it pushes over their limit
            const pts = [[r.sub_lon, r.sub_lat], ...r.overloaded.flatMap((o) => [subPos(o.from), subPos(o.to)])]
            mapRef.current?.focus(pts.filter(Boolean))
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
  }, [site, mw, subPos])

  // Play the cascade one step at a time.
  useEffect(() => {
    if (!playing || !cascade) return undefined
    const t = setTimeout(() => {
      const next = step + 1
      setStep(next)
      if (next >= cascade.steps.length) setPlaying(false)
    }, STEP_MS)
    return () => clearTimeout(t)
  }, [playing, step, cascade])

  function clearCascade() {
    cascadeReq.current++ // a cascade still in flight belongs to the old site; drop it when it lands
    setCascading(false)
    setCascade(null)
    setCascadeError(null)
    setPlaying(false)
    setStep(0)
  }

  function place(lat, lon) {
    setSite({ lat, lon })
    clearCascade()
  }

  function changeMw(value) {
    setMw(value)
    clearCascade()
  }

  async function startCascade() {
    const id = ++cascadeReq.current
    setCascading(true)
    setCascadeError(null)
    try {
      const c = await runCascade({ ...site, mw })
      if (id !== cascadeReq.current) return
      setCascade(c)
      setStep(0)
      setPlaying(c.steps.length > 0)
      // pull the camera out to everything the cascade will touch
      const pts = [[c.sub_lon, c.sub_lat]]
      c.steps.forEach((st) => {
        st.tripped.forEach((bid) => {
          const b = branchById.get(bid)
          if (b) pts.push(subPos(b.from_sub), subPos(b.to_sub))
        })
        st.dark_subs.forEach((sid) => pts.push(subPos(sid)))
      })
      mapRef.current?.focus(pts.filter(Boolean))
    } catch (err) {
      if (id === cascadeReq.current) setCascadeError(err)
    } finally {
      if (id === cascadeReq.current) setCascading(false)
    }
  }

  function toggleHeadroom() {
    const on = !headroomOn
    setHeadroomOn(on)
    if (on && !headroom) fetchHeadroom()
  }

  function fetchHeadroom() {
    setHeadroomError(null)
    getHeadroom()
      .then((h) => setHeadroom(h.by_sub))
      .catch(setHeadroomError)
  }

  const saveName = result ? `${result.sub_name} · ${fmt(mw)} MW`.slice(0, 80) : ''
  const alreadySaved = !!scenarios?.some((sc) => sc.name === saveName)

  async function save() {
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
  }

  async function remove(id) {
    try {
      await deleteScenario(id)
    } catch (err) {
      if (!/not found/i.test(err.message)) {
        setScenarioError(err)
        return
      }
    }
    setScenarios((list) => list.filter((sc) => sc.id !== id))
  }

  function pick(sc) {
    setMw(sc.mw)
    place(sc.lat, sc.lon)
  }


  // What the map shows: every branch's loading class, and per-substation classes (dark / headroom).
  const mapView = useMemo(() => {
    if (!grid) return null
    const base = result ? result.loading_pct : grid.branches.map((b) => b.base_pct)
    const lineClasses = new Array(grid.branches.length)
    const subClasses = {}
    const steps = cascade?.steps || []
    if (cascade && step > 0) {
      const tripped = new Set()
      for (let j = 0; j < step; j++) {
        steps[j].tripped.forEach((id) => tripped.add(id))
        steps[j].dark_subs.forEach((id) => (subClasses[id] = 'sub--dark'))
      }
      const last = step >= steps.length
      const hot = new Map(steps[step - 1].hot.map((h) => [h.id, h.pct]))
      grid.branches.forEach((b, i) => {
        if (tripped.has(b.id)) lineClasses[i] = 'ln--tripped'
        else if (last) lineClasses[i] = loadClass(cascade.final_loading_pct[i])
        else if (hot.has(b.id)) lineClasses[i] = loadClass(hot.get(b.id))
        else lineClasses[i] = loadClass(Math.min(base[i], 79.9)) // not hot now: under 80 %
      })
    } else {
      grid.branches.forEach((_, i) => (lineClasses[i] = loadClass(base[i])))
    }
    if (headroomOn && headroom) {
      grid.subs.forEach((s) => {
        const v = headroom[s.id]
        if (v !== undefined) subClasses[s.id] = headroomClass(v, mw)
      })
    }
    return { lineClasses, subClasses }
  }, [grid, result, cascade, step, headroomOn, headroom, mw])

  const shownSite = result ? { lat: result.sub_lat, lon: result.sub_lon } : site

  // how many substations fall in each headroom bucket at the current size (for the legend)
  const headroomCounts = useMemo(() => {
    if (!headroom) return null
    const counts = { 'sub--hr-ok': 0, 'sub--hr-mid': 0, 'sub--hr-low': 0 }
    Object.values(headroom).forEach((v) => counts[headroomClass(v, mw)]++)
    return { ok: counts['sub--hr-ok'], mid: counts['sub--hr-mid'], low: counts['sub--hr-low'] }
  }, [headroom, mw])

  return (
    <Layout
      title="Overload"
      tagline="When the next AI data center plugs in, whose lights go out?"
      user={user}
      footer={
        <p>
          Grid model: Breakthrough Energy Sciences U.S. Test System, derived from Texas A&amp;M University&apos;s ACTIVSg
          synthetic grids (CC-BY 4.0). It is synthetic and does not represent any utility&apos;s real network. DC power flow;
          homes are an estimate (~1.4 kW average household load). Coastline simplified.
        </p>
      }
    >
      <div className="app-grid">
        <section className="stack map-col" aria-label="Grid map">
          <div className="row">
            <Badge>Synthetic grid model (Breakthrough Energy / Texas A&amp;M), not any utility&apos;s network</Badge>
          </div>
          {grid ? (
            <GridMap
              ref={mapRef}
              grid={grid}
              lineClasses={mapView.lineClasses}
              subClasses={mapView.subClasses}
              site={shownSite}
              headroomMode={headroomOn && !!headroom}
              onPlace={place}
            />
          ) : gridError ? (
            <ErrorBanner error={gridError} onRetry={loadGrid} />
          ) : (
            <Loading label="Loading the grid…" />
          )}
        </section>
        <aside className="stack side">
          <DataCenterCard mw={mw} onMw={changeMw} />
          <ScenarioCard
            user={user}
            scenarios={scenarios}
            error={scenarioError}
            onRetry={loadScenarios}
            onPick={pick}
            onDelete={remove}
            canSave={!!(user && result && !alreadySaved)}
            onSave={save}
            saving={saving}
          />
          <SiteCard
            site={site}
            result={result}
            solving={solving}
            error={whatifError}
            subName={subName}
            cascade={cascade}
            cascading={cascading}
            cascadeError={cascadeError}
            step={step}
            onStep={(s) => {
              setPlaying(false)
              setStep(s)
            }}
            onCascade={startCascade}
          />
          <HeadroomCard
            on={headroomOn}
            onToggle={toggleHeadroom}
            mw={mw}
            counts={headroomCounts}
            loading={headroomOn && !headroom && !headroomError}
            error={headroomError}
            onRetry={fetchHeadroom}
          />
        </aside>
      </div>
    </Layout>
  )
}

export default App
