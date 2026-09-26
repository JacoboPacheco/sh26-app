import BoomLayer from './features/boom/BoomLayer'
import BoomPanel from './features/boom/BoomPanel'
import BestSitesLayer from './features/fix/BestSitesLayer'
import FixPanel from './features/fix/FixPanel'
import FlowCanvas from './features/flow/FlowCanvas'
import Intro from './features/flow/Intro'
import HeatClock from './features/heat/HeatClock'
import HurricaneLayer from './features/hurricane/HurricaneLayer'
import HurricanePanel from './features/hurricane/HurricanePanel'
import ImpactLayer from './features/impact/ImpactLayer'
import GridMap from './GridMap'
import CampusPanel from './shell/CampusPanel'
import ImpactPanel from './shell/ImpactPanel'
import Timeline from './shell/Timeline'
import { OverloadProvider, useOverload } from './store'
import { ErrorBanner, Loading } from './ui'
import useAuth from './useAuth'

const MODES = [
  { id: 'campus', label: 'Data center', Panel: CampusPanel },
  { id: 'hurricane', label: 'Hurricane', Panel: HurricanePanel },
  { id: 'boom', label: 'AI boom', Panel: BoomPanel },
  { id: 'fix', label: 'Fix it', Panel: FixPanel },
]

function App() {
  // Keep this call: with VITE_DEMO_EMAIL/VITE_DEMO_PASSWORD set (frontend/.env) it signs
  // in as the seeded demo account on load, so per-user features need no login screen.
  const { user } = useAuth()
  return (
    <OverloadProvider user={user}>
      <MissionControl user={user} />
    </OverloadProvider>
  )
}

// The whole screen is the map; everything else floats over it.
function MissionControl({ user }) {
  const o = useOverload()
  const { grid, gridError, loadGrid, view, site, result, extraSites, mode, setMode, mapTool, mapRef, place, headroomOn, headroom } = o
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

  return (
    <div className="mc">
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
          <ImpactLayer />
          <HurricaneLayer />
          <BoomLayer />
          <BestSitesLayer />
        </GridMap>
      ) : (
        <div className="mc-loading">{gridError ? <ErrorBanner error={gridError} onRetry={loadGrid} /> : <Loading label="Loading Florida's grid…" />}</div>
      )}

      <header className="mc-top">
        <div className="mc-title">
          <h1 className="wordmark">Overload</h1>
          <p className="hook">When the next AI data center plugs in, whose lights go out?</p>
        </div>
        <div className="mc-top__right">
          <HeatClock />
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
          Homes are estimates. Outline: U.S. Census Bureau.
        </span>
      </p>
      <Intro />
    </div>
  )
}

export default App
