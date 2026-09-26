import BoomLayer from './features/boom/BoomLayer'
import BoomPanel from './features/boom/BoomPanel'
import DangerLayer from './features/danger/DangerLayer'
import DarkFirstLayer from './features/darkfirst/DarkFirstLayer'
import BestSitesLayer from './features/fix/BestSitesLayer'
import FixPanel from './features/fix/FixPanel'
import GeminiDuelLayer from './features/fix/GeminiDuelLayer'
import FlowCanvas from './features/flow/FlowCanvas'
import Intro from './features/flow/Intro'
import HardenLayer from './features/harden/HardenLayer'
import HurricaneLayer from './features/hurricane/HurricaneLayer'
import HurricanePanel from './features/hurricane/HurricanePanel'
import ImpactLayer from './features/impact/ImpactLayer'
import PlantsLayer from './features/plants/PlantsLayer'
import ProposalRings from './features/proposals/ProposalRings'
import UnlockLayer from './features/unlock/UnlockLayer'
import StrengthenPage from './features/unlock/StrengthenPage'
import { NO_SUBS, strengthenClick, useStayOnStrengthen, useStrengthenLines } from './features/unlock/strengthenMap'
import PlantsPanel from './features/plants/PlantsPanel'
import GridMap from './GridMap'
import CampusPanel from './shell/CampusPanel'
import TopBar from './shell/TopBar'
import CascadeFX from './shell/CascadeFX'
import CascadeCue from './shell/CascadeCue'
import ComponentPanel from './features/component3d'
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
// Views: the data center locator with a company filter, population and energy graphs (#/views)
const VIEWS = lazy(() => import('./features/views/ViewsPage'))

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
  const views = useHashPrefix('#/views')
  if (plans)
    return (
      <>
        <TopBar active="agreement" />
        <Suspense fallback={<Loading />}>
          <div className="plans-page below-bar">
            <PLANS />
          </div>
        </Suspense>
      </>
    )
  if (views)
    return (
      <OverloadProvider user={user}>
        <TopBar active="views" />
        <div className="below-bar">
          <Suspense fallback={<Loading />}>
            <VIEWS />
          </Suspense>
        </div>
      </OverloadProvider>
    )
  if (vote)
    return (
      <OverloadProvider user={user}>
        <TopBar active="proposals" />
        <div className="below-bar">
          <Suspense fallback={<Loading />}>
            <VOTE />
          </Suspense>
        </div>
      </OverloadProvider>
    )
  return (
    <OverloadProvider user={user}>
      {next && NEXT ? (
        <Suspense fallback={<Loading />}>
          <NEXT user={user} />
        </Suspense>
      ) : (
        <MissionControl />
      )}
    </OverloadProvider>
  )
}

// The whole screen is the map; everything else floats over it.
function MissionControl() {
  const o = useOverload()
  const { grid, gridError, loadGrid, view, site, result, extraSites, mode, setMode, mapTool, mapRef, place, headroomOn, headroom, loadFactor } = o
  // the grid's mood follows the time of day: dim and calm at night, hot in a heat wave
  const level = loadFactor < 0.7 ? 'night' : loadFactor < 0.9 ? 'morning' : loadFactor > 1.001 ? 'heat' : 'peak'
  const Panel = MODES.find((m) => m.id === mode)?.Panel || CampusPanel
  // Strengthen the grid is a full page around the same map: the grid alone (no demo campus, no cascade layers)
  const strengthen = mode === 'unlock'
  const strengthenLines = useStrengthenLines(grid)
  // the living flow draws the demo's case (its overloads stream red): on Strengthen it runs only while that case
  // is empty, when what it draws is the grid alone
  const flowOn = !strengthen || (!result && !o.cascade)
  useStayOnStrengthen(o) // a state change on that page keeps you on it
  const sites = [
    ...(site ? [{ lat: result?.sub_lat ?? site.lat, lon: result?.sub_lon ?? site.lon, primary: true }] : []),
    ...extraSites.map((s) => ({ lat: s.lat, lon: s.lon })),
  ]
  // the connect pulse: from where you clicked to the substation the campus plugs into
  const tap =
    site && result?.forSite === site && result.sub_lat != null
      ? { from: [site.lon, site.lat], to: [result.sub_lon, result.sub_lat], key: `${site.lat},${site.lon}` }
      : null
  // the top bar's two map tabs switch the mode here; #/strengthen (from another page) opens Strengthen
  const pickTab = (id, e) => {
    if (id !== 'demo' && id !== 'strengthen') return
    e?.preventDefault()
    setMode(id === 'strengthen' ? 'unlock' : mode === 'unlock' ? 'campus' : mode)
    if (window.location.hash !== '#/' && window.location.hash !== '') window.history.replaceState(null, '', '#/')
  }
  useEffect(() => {
    if (window.location.hash.startsWith('#/strengthen')) {
      setMode('unlock')
      window.history.replaceState(null, '', '#/')
    }
  }, [setMode])
  // a big leap of the people-hit counter shakes the map (shell/ImpactPanel dispatches it; bomb.css)
  const rootRef = useRef(null)
  useEffect(() => {
    const quake = (e) => {
      const el = rootRef.current
      if (!el) return
      // by the people in the leap (100,000+: 12-16 px) or, for a smaller incident, its weight in the incident (up to 12.9 px)
      const d = e.detail || {}
      const byPeople = (d.big ?? d.delta >= 100000) ? 3 + Math.log10(Math.max(d.delta || 1, 1)) * 1.8 : 0
      el.style.setProperty('--quake', `${Math.min(16, Math.max(byPeople, 12.9 * (d.intensity || 0), 3)).toFixed(1)}px`)
      el.classList.remove('mc--quake')
      void el.offsetWidth // restart the animation
      el.classList.add('mc--quake')
    }
    window.addEventListener('overload:leap', quake)
    return () => window.removeEventListener('overload:leap', quake)
  }, [])

  return (
    <div className={`mc mc--${level}${strengthen ? ' mc--unlock' : ''}`} ref={rootRef}>
      {grid ? (
        <GridMap
          ref={mapRef}
          grid={grid}
          lineClasses={strengthen ? strengthenLines : view.lineClasses}
          subClasses={strengthen ? NO_SUBS : view.subClasses}
          sites={strengthen ? [] : sites}
          tap={strengthen ? null : tap}
          headroomMode={!strengthen && headroomOn && !!headroom}
          onPlace={strengthen ? strengthenClick(o) : place}
          tool={strengthen ? null : mapTool}
          overlay={flowOn ? (refs) => <FlowCanvas {...refs} /> : undefined}
        >
          {/* the demo's layers stay mounted (their state survives a visit to Strengthen) but hidden there */}
          <g display={strengthen ? 'none' : undefined}>
            {/* first, so the danger zones, best sites and every other layer draw (and take clicks) above them */}
            <ProposalRings />
            <DangerLayer />
            <CascadeFX />
            {/* "Run the cascade" beside the dropped data center (the bottom bar hides its own meanwhile) */}
            <CascadeCue />
            <ImpactLayer />
            {/* "Who goes dark first?": the rule picked in the results column, crossfaded over the end state */}
            <DarkFirstLayer />
            <HurricaneLayer />
            {/* "Harden before the storm": the plan's lines in green and the storm replayed with them */}
            <HardenLayer />
            <BoomLayer />
            <BestSitesLayer />
            <PlantsLayer />
            {/* last: Gemini's recorded plans replayed over everything ("Let Gemini fix it") */}
            <GeminiDuelLayer />
          </g>
          <UnlockLayer />
        </GridMap>
      ) : (
        <div className="mc-loading">{gridError ? <ErrorBanner error={gridError} onRetry={loadGrid} /> : <Loading label={`Loading ${o.regions?.find((r) => r.code === o.region)?.name || 'the'} grid…`} />}</div>
      )}

      <TopBar active={strengthen ? 'strengthen' : 'demo'} onPick={pickTab} withState />
      {strengthen ? (
        <StrengthenPage />
      ) : (
        <>
          <header className="mc-top">
            <div className="mc-title">
              <h1 className="hook">When the next AI data center plugs in, whose lights go out?</h1>
              <p className="pitch">
                …and how many more can the grid safely take? AI finds where they strain it and tests the fixes; the physics engine checks every one.{' '}
                <a className="pitch__link" href="#/strengthen" onClick={(e) => pickTab('strengthen', e)}>
                  Strengthen the grid
                </a>
              </p>
            </div>
            <ScenarioBar />
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

          {/* the failing line or transformer in 3D while the cascade replays (features/component3d) */}
          <ComponentPanel />
          <footer className="mc-bottom glass">
            <Timeline />
          </footer>
        </>
      )}

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
