import { useEffect, useMemo, useRef } from 'react'
import GridMap from '../../GridMap'
import { STATES, regionAt } from '../../geo'
import CascadeFX from '../../shell/CascadeFX'
import { useOverload } from '../../store'
import { ErrorBanner, Loading } from '../../ui'
import BoomLayer from '../boom/BoomLayer'
import BestSitesLayer from '../fix/BestSitesLayer'
import FlowCanvas from '../flow/FlowCanvas'
import HurricaneLayer from '../hurricane/HurricaneLayer'
import ImpactLayer from '../impact/ImpactLayer'
import AppBar from './AppBar'
import './app.css'
import { DataCenterLeft, DataCenterRight } from './DataCenterPage'
import { BriefPage, ComparePage, LibraryPage, MissingPage } from './DocPages'
import { DataCentersLayer, HomeLeft, HomeRight, useHomeState } from './HomePage'
import { Optional, hasModule } from './optional'
import { go, href, useRoute } from './router'
import { Inspector, NoModel, RecipeSidebar, ReplayBar, ReviewHost, Toolbar, isKnownState, moodOf, useWorkspaceEntry } from './Workspace'

// The Overload application (#/next): a national home map of announced AI data centers, a workspace
// per state, a page per data center, and the library / compare / brief pages. One map stays mounted
// across the map pages, so moving between America and a state is a camera flight, not a reload.
//
// Layout = application chrome: an app bar (brand, breadcrumbs, navigation) on top; on map pages a
// left sidebar, the map canvas, a right inspector, and in a state a toolbar above and the replay
// bar below the canvas. Document pages (library, compare, brief) use the full width.
export default function NextApp() {
  const route = useRoute()
  const o = useOverload()
  const home = useHomeState()
  // a brief plays as the briefing track's review stage over the live map (a saved scenario, the hero,
  // a preset); a share link, ?doc, or no stage yet reads as our one-page brief
  const stageBrief = route.page === 'brief' && !('doc' in route.query) && hasModule('briefing/BriefRoute') && /^(\d+|hero|preset:.+)$/.test(route.id)
  // a state with no grid model (a typo, Alaska, D.C.) reads as a page of its own, not a workspace
  const noModel = route.page === 'state' && !isKnownState(route.code, o.regions)
  const page = noModel ? 'nomodel' : route.page
  const mapPage = page === 'home' || page === 'state' || page === 'dc' || stageBrief
  const national = route.page === 'home' || route.page === 'dc'

  // Map pages set the store's region: America for home and a data center's page, the state for its workspace.
  useEffect(() => {
    if (national && o.region !== 'US') o.setRegion('US')
  }, [national, o.region]) // eslint-disable-line react-hooks/exhaustive-deps -- setRegion's identity follows the region
  useWorkspaceEntry(route)
  useFollowCase(route)
  usePageTitle(route, o)

  // a click on the national map opens the state under it with a campus dropped there
  const onPlace = (lat, lon) => {
    if (!national) return o.place(lat, lon)
    const code = regionAt(lat, lon)
    if (!code) return
    if (o.regions && !o.regions.some((r) => r.code === code)) {
      home.setNotice(`${STATES[code]?.name || code} has no grid model yet. Try a neighboring state.`)
      return
    }
    go(`/state/${code}`, { lat: lat.toFixed(4), lon: lon.toFixed(4), mw: home.dropMw })
  }

  const layout = mapPage ? `nx--map nx--${page}` : 'nx--doc'
  const sides = page === 'home' || page === 'dc' || page === 'state'
  const mood = page === 'state' ? moodOf(o.loadFactor) : 'peak'

  return (
    <div className={`nx ${layout} mc--${mood}${o.region === 'FL' ? ' nx--fl' : ''}`}>
      <AppBar route={route} />
      {page === 'state' && <Toolbar route={route} />}
      {sides && (
        <aside className="nx-left" aria-label={page === 'state' ? 'Scenario' : page === 'dc' ? 'Data center' : 'Explore'}>
          {page === 'home' && <HomeLeft home={home} />}
          {page === 'dc' && <DataCenterLeft id={route.id} />}
          {page === 'state' && <RecipeSidebar route={route} />}
        </aside>
      )}
      {mapPage && <MapCanvas key="canvas" route={route} national={national} onPlace={onPlace} home={home} />}
      {sides && (
        <aside className="nx-right" aria-label={page === 'state' ? 'Result' : 'Details'}>
          {page === 'home' && <HomeRight home={home} />}
          {page === 'dc' && <DataCenterRight id={route.id} />}
          {page === 'state' && <Inspector route={route} />}
        </aside>
      )}
      {page === 'state' && <ReplayBar />}
      {page === 'state' && <ReviewHost />}
      {stageBrief && (
        <div className="nx-briefhost">
          {/^\d+$/.test(route.id) && o.user === undefined ? (
            // a saved scenario is the demo account's: wait for the sign-in (a 401 would sign the app out)
            <div className="nx-briefhost__msg" role="status">
              <Loading label="Signing in…" />
            </div>
          ) : (
          <Optional
            from="briefing/BriefRoute"
            id={route.id}
            onClose={() => go(o.region && o.region !== 'US' ? `/state/${o.region}` : '/library')}
            fallback={<BriefUnavailable id={route.id} />}
          />
          )}
        </div>
      )}
      {!mapPage && (
        <main className="nx-doc" id="main">
          {page === 'library' && <LibraryPage />}
          {page === 'compare' && <ComparePage a={route.a} b={route.b} />}
          {page === 'brief' && !stageBrief && <BriefPage id={route.id} />}
          {page === 'missing' && <MissingPage />}
          {page === 'nomodel' && (
            <div className="nx-docwrap">
              <NoModel code={route.code} />
            </div>
          )}
        </main>
      )}
    </div>
  )
}

// The one map. National pages draw the data centers; a state draws its synthetic grid and every
// feature's layer (the same set mission control mounts).
function MapCanvas({ route, national, onPlace, home }) {
  const o = useOverload()
  const { grid, gridError, loadGrid, view, site, result, extraSites, mapTool, mapRef, headroomOn, headroom, regionLoading } = o
  const sites = [
    ...(site ? [{ lat: result?.sub_lat ?? site.lat, lon: result?.sub_lon ?? site.lon, primary: true }] : []),
    ...extraSites.map((s) => ({ lat: s.lat, lon: s.lon })),
  ]
  const tap =
    site && result?.forSite === site && result.sub_lat != null
      ? { from: [site.lon, site.lat], to: [result.sub_lon, result.sub_lat], key: `${site.lat},${site.lon}` }
      : null
  const stateMap = !national && grid?.meta?.region && grid.meta.region !== 'US'
  const waiting = route.page === 'state' && grid?.meta?.region !== route.code

  return (
    <main className="nx-canvas" id="main" aria-label="Map">
      {grid ? (
        <GridMap
          ref={mapRef}
          grid={grid}
          lineClasses={stateMap ? view.lineClasses : []}
          subClasses={stateMap ? view.subClasses : {}}
          sites={stateMap ? sites : []}
          tap={stateMap ? tap : null}
          headroomMode={stateMap && headroomOn && !!headroom}
          onPlace={onPlace}
          tool={stateMap ? mapTool : null}
          overlay={stateMap ? (refs) => <FlowCanvas {...refs} /> : undefined}
        >
          {national ? (
            <>
              <DataCentersLayer home={home} focusId={route.page === 'dc' ? route.id : null} />
              <Optional from="town/AreaHover" loading={null} />
            </>
          ) : stateMap ? (
            <>
              <CascadeFX />
              <ImpactLayer />
              <HurricaneLayer />
              <BoomLayer />
              <BestSitesLayer />
              <Optional from="hospitals/HospitalsLayer" loading={null} />
              <Optional from="plants/PlantsLayer" loading={null} />
              <Optional from="town/AreaLayer" loading={null} />
              <Optional from="town/AreaHover" loading={null} />
            </>
          ) : null}
        </GridMap>
      ) : (
        <div className="nx-canvas__msg">{gridError ? <ErrorBanner error={gridError} onRetry={() => loadGrid()} /> : <Loading label="Loading the map…" />}</div>
      )}
      {(regionLoading || (waiting && !gridError)) && grid && (
        <p className="nx-canvas__loading" role="status">
          Loading {STATES[route.code]?.name || 'the state'}&apos;s grid model…
        </p>
      )}
      {!national && gridError && grid && (
        <div className="nx-canvas__error">
          <ErrorBanner error={gridError} onRetry={() => loadGrid()} />
        </div>
      )}
      <p className="nx-attrib">
        <span className="nx-attrib__pill">Synthetic grid model, not any utility&apos;s network</span>
        <span className="nx-attrib__text">
          Grid: Breakthrough Energy Sciences U.S. Test System from Texas A&amp;M ACTIVSg synthetic grids (CC-BY 4.0), DC power flow. Outlines: U.S. Census
          Bureau. People and costs are estimates.
        </span>
      </p>
    </main>
  )
}

function BriefUnavailable({ id }) {
  return (
    <div className="nx-briefhost__msg" role="status">
      <p>The briefing can&apos;t open right now.</p>
      <a className="btn" href={href(`/brief/${encodeURIComponent(id)}`, { doc: 1 })}>
        Read the one-page brief
      </a>
    </div>
  )
}

// On a page other than a workspace, something else can open a case (a data center card's "Test it",
// the library's "Open", the planner's "Load this plan"): the store's case changes, and the app
// follows it into that state's workspace.
function useFollowCase(route) {
  const { region, site, extraSites, trip, loadFactor } = useOverload()
  const sig = `${region}|${site ? `${site.lat},${site.lon}` : ''}|${extraSites.length}|${trip.length}|${loadFactor}`
  const seen = useRef({ page: null, sig })
  useEffect(() => {
    const s = seen.current
    if (s.page !== route.page) {
      seen.current = { page: route.page, sig } // a new page starts from the case it found
      return
    }
    if (sig === s.sig) return
    s.sig = sig
    if (route.page === 'state' || region === 'US' || !region) return
    // whoever opened the case may already be navigating there (a card's onPlaced, with its query)
    if (window.location.hash.startsWith(`#/next/state/${region}`)) return
    go(`/state/${region}`)
  }, [route.page, sig, region])
}

function usePageTitle(route, o) {
  const name = o.grid?.meta?.region_name
  const title = useMemo(() => {
    if (route.page === 'home') return 'Overload · AI data centers and the grid'
    if (route.page === 'state') return `Overload · ${STATES[route.code]?.name || name || route.code} workspace`
    if (route.page === 'dc') return 'Overload · Data center'
    if (route.page === 'library') return 'Overload · Library'
    if (route.page === 'compare') return 'Overload · Compare'
    if (route.page === 'brief') return 'Overload · Brief'
    return 'Overload'
  }, [route, name])
  useEffect(() => {
    document.title = title
  }, [title])
}
