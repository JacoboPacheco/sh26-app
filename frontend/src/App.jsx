import BoomLayer from './features/boom/BoomLayer'
import BoomPanel from './features/boom/BoomPanel'
import DangerLayer from './features/danger/DangerLayer'
import BestSitesLayer from './features/fix/BestSitesLayer'
import FixPanel from './features/fix/FixPanel'
import FlowCanvas from './features/flow/FlowCanvas'
import Intro from './features/flow/Intro'
import HeatClock from './features/heat/HeatClock'
import HurricaneLayer from './features/hurricane/HurricaneLayer'
import HurricanePanel from './features/hurricane/HurricanePanel'
import ImpactLayer from './features/impact/ImpactLayer'
import PlantsLayer from './features/plants/PlantsLayer'
import PlantsPanel from './features/plants/PlantsPanel'
import GridMap from './GridMap'
import CampusPanel from './shell/CampusPanel'
import RegionPicker from './shell/RegionPicker'
import CascadeFX from './shell/CascadeFX'
import ImpactPanel from './shell/ImpactPanel'
import ScenarioBar from './shell/ScenarioBar'
import Timeline from './shell/Timeline'
import { OverloadProvider, useOverload } from './store'
import { lazy, Suspense, useEffect, useRef, useState } from 'react'
import { ErrorBanner, Loading } from './ui'
import useAuth from './useAuth'

// Feature previews: each feature folder may have a Preview.jsx; #/preview/<folder> shows it over the
// live map inside the real app state. How a feature is built and checked before it is mounted.
// Loaded lazily, so a half-written feature only breaks its own preview, never the main app.
const PREVIEWS = Object.fromEntries(Object.entries(import.meta.glob('./features/*/Preview.jsx')).map(([path, load]) => [path, lazy(load)]))
// The next application shell, built in features/app/ alongside this one: #/next… renders it instead.
const loadNext = import.meta.glob('./features/app/NextApp.jsx')['./features/app/NextApp.jsx']
const NEXT = loadNext ? lazy(loadNext) : null
// Build plans (Sperry GridLock): a full page with its own map at #/plans
const PLANS = lazy(() => import('./features/gridlock/BuildPlansPage'))
// Before the vote: look up a proposed data center, what to ask, where to speak (#/vote, #/vote/<id>)
const VOTE = lazy(() => import('./features/vote/VotePage'))

function useHashPrefix(prefix) {
  const [on, setOn] = useState(() => window.location.hash.startsWith(prefix))
  useEffect(() => {
    const f = () => setOn(window.location.hash.startsWith(prefix))
    window.addEventListener('hashchange', f)
    return () => window.removeEventListener('hashchange', f)
  }, [prefix])
  return on
}
const useIsNext = () => useHashPrefix('#/next')

function usePreviewName() {
  const read = () => window.location.hash.match(/^#\/preview\/([\w-]+)/)?.[1] || null
  const [name, setName] = useState(read)
  useEffect(() => {
    const on = () => setName(read())
    window.addEventListener('hashchange', on)
    return () => window.removeEventListener('hashchange', on)
  }, [])
  return name
}

const MODES = [
  { id: 'campus', label: 'Data center', Panel: CampusPanel },
  { id: 'hurricane', label: 'Hurricane', Panel: HurricanePanel },
  { id: 'boom', label: 'AI boom', Panel: BoomPanel },
  { id: 'fix', label: 'Fix it', Panel: FixPanel },
  { id: 'plants', label: 'Plants', Panel: PlantsPanel },
]

function App() {
  // Keep this call: with VITE_DEMO_EMAIL/VITE_DEMO_PASSWORD set (frontend/.env) it signs
  // in as the seeded demo account on load, so per-user features need no login screen.
  const { user } = useAuth()
  const next = useIsNext()
  const plans = useHashPrefix('#/plans')
  const vote = useHashPrefix('#/vote')
  if (plans)
    return (
      <Suspense fallback={<Loading />}>
        <div className="plans-page">
          <PLANS extra={<a href="#/">Back to Overload</a>} />
        </div>
      </Suspense>
    )
  if (vote)
    return (
      <OverloadProvider user={user}>
        <Suspense fallback={<Loading />}>
          <VOTE />
        </Suspense>
      </OverloadProvider>
    )
  return (
    <OverloadProvider user={user}>
      {next && NEXT ? (
        <Suspense fallback={<Loading />}>
          <NEXT user={user} />
        </Suspense>
      ) : (
        <MissionControl user={user} />
      )}
    </OverloadProvider>
  )
}

// The whole screen is the map; everything else floats over it.
function MissionControl({ user }) {
  const o = useOverload()
  const { grid, gridError, loadGrid, view, site, result, extraSites, mode, setMode, mapTool, mapRef, place, headroomOn, headroom, loadFactor } = o
  // the grid's mood follows the time of day: dim and calm at night, hot in a heat wave
  const level = loadFactor < 0.7 ? 'night' : loadFactor < 0.9 ? 'morning' : loadFactor > 1.001 ? 'heat' : 'peak'
  const Panel = MODES.find((m) => m.id === mode)?.Panel || CampusPanel
  const sites = [
    ...(site ? [{ lat: result?.sub_lat ?? site.lat, lon: result?.sub_lon ?? site.lon, primary: true }] : []),
    ...extraSites.map((s) => ({ lat: s.lat, lon: s.lon })),
  ]
  // the connect pulse: from where you clicked to the substation the campus plugs into
  const tap =
    site && result?.forSite === site && result.sub_lat != null
      ? { from: [site.lon, site.lat], to: [result.sub_lon, result.sub_lat], key: `${site.lat},${site.lon}` }
      : null
  // a big leap of the people-hit counter shakes the map (shell/ImpactPanel dispatches it; bomb.css)
  const rootRef = useRef(null)
  useEffect(() => {
    const quake = (e) => {
      const el = rootRef.current
      if (!el) return
      el.style.setProperty('--quake', `${Math.min(16, 3 + Math.log10(Math.max(e.detail?.delta || 1, 1)) * 1.8).toFixed(1)}px`)
      el.classList.remove('mc--quake')
      void el.offsetWidth // restart the animation
      el.classList.add('mc--quake')
    }
    window.addEventListener('overload:leap', quake)
    return () => window.removeEventListener('overload:leap', quake)
  }, [])

  return (
    <div className={`mc mc--${level}`} ref={rootRef}>
      {grid ? (
        <GridMap
          ref={mapRef}
          grid={grid}
          lineClasses={view.lineClasses}
          subClasses={view.subClasses}
          sites={sites}
          tap={tap}
          headroomMode={headroomOn && !!headroom}
          onPlace={place}
          tool={mapTool}
          overlay={(refs) => <FlowCanvas {...refs} />}
        >
          <DangerLayer />
          <CascadeFX />
          <ImpactLayer />
          <HurricaneLayer />
          <BoomLayer />
          <BestSitesLayer />
          <PlantsLayer />
        </GridMap>
      ) : (
        <div className="mc-loading">{gridError ? <ErrorBanner error={gridError} onRetry={loadGrid} /> : <Loading label={`Loading ${o.regions?.find((r) => r.code === o.region)?.name || 'the'} grid…`} />}</div>
      )}

      <header className="mc-top">
        <div className="mc-title">
          <div className="mc-title__row">
            <h1 className="wordmark">Overload</h1>
            <RegionPicker />
          </div>
          <p className="hook">When the next AI data center plugs in, whose lights go out?</p>
        </div>
        <ScenarioBar />
        <div className="mc-top__right">
          <HeatClock />
          <a className="plans-link" href="#/vote">
            Before the vote: look up a proposed data center
          </a>
          <a className="plans-link" href="#/plans">
            Build plans: are neighboring utilities building together?
          </a>
          {user && <span className="signed-in">Signed in as {user.email}</span>}
        </div>
      </header>

      <aside className="mc-left glass" aria-label="Scenario">
        <nav className="modes" aria-label="Mode">
          {MODES.map((m) => (
            <button key={m.id} type="button" className={`mode${m.id === mode ? ' mode--on' : ''}`} aria-pressed={m.id === mode} onClick={() => setMode(m.id)}>
              {m.label}
            </button>
          ))}
        </nav>
        <Panel />
      </aside>

      <aside className="mc-right glass" aria-label="Who is affected">
        <ImpactPanel />
      </aside>

      <footer className="mc-bottom glass">
        <Timeline />
      </footer>

      <p className="mc-credit">
        <span className="pill">Synthetic grid model (Breakthrough Energy / Texas A&amp;M), not any utility&apos;s network</span>
        <span className="credit">
          Grid: Breakthrough Energy Sciences U.S. Test System, from Texas A&amp;M ACTIVSg synthetic grids (CC-BY 4.0). DC power flow.
          People counts are estimates. Outline: U.S. Census Bureau.
        </span>
      </p>
      <Intro />
      <PreviewHost />
    </div>
  )
}

function PreviewHost() {
  const name = usePreviewName()
  if (!name) return null
  const Preview = PREVIEWS[`./features/${name}/Preview.jsx`]
  return (
    <section className="preview-host glass" aria-label={`Preview of ${name}`}>
      <div className="preview-host__bar">
        <strong>Preview: {name}</strong>
        <a href="#/">Close</a>
      </div>
      {Preview ? (
        <Suspense fallback={<Loading />}>
          <Preview />
        </Suspense>
      ) : (
        <p className="muted">No features/{name}/Preview.jsx yet.</p>
      )}
    </section>
  )
}

export default App
