import { useOverload } from '../../store'
import { stateHref, testHref } from './viewsKit'

// A link from the Data page into the main map (Watch it fail): the state's model, or a campus of a site's reported
// size placed at its reported point. It drives the same store the map reads (the page is rendered inside
// OverloadProvider, like Proposed data centers' "Watch what could happen"), so the viewer lands in the app they
// came from, top bar and all. A click that opens a new tab follows the plain deep link instead.

const MODEL_MAX_MW = 50000 // the engine's cap (backend MW_MAX)

function openOnMap(o, { state, lat, lon, mw }) {
  if (!o || !state) return false
  if (lat != null && lon != null && mw) {
    const size = Math.min(Math.round(mw), MODEL_MAX_MW)
    if (o.region === state) {
      o.setMode?.('campus')
      o.setMw(size)
      o.place(lat, lon)
    } else if (!o.setRegion(state, { place: [lat, lon], mw: size })) return false
  } else if (o.region !== state && !o.setRegion(state)) return false
  window.location.hash = '#/'
  return true
}

export default function MapLink({ state, site, className, title, children }) {
  const o = useOverload()
  const href = (site ? testHref(site) : stateHref(state)) || stateHref(state || site?.state)
  const onClick = (e) => {
    if (!o || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button !== 0) return
    e.preventDefault()
    const ok = site ? openOnMap(o, { state: site.state, lat: site.lat, lon: site.lon, mw: site.mw }) : openOnMap(o, { state })
    if (!ok) window.location.hash = href
  }
  return (
    <a className={className} href={href} title={title} onClick={onClick}>
      {children}
    </a>
  )
}
