import { useCallback, useSyncExternalStore } from 'react'
import { fmt } from '../../geo'
import { useOverload } from '../../store'

// One way to test a planned data center, shared by the "Planned data centers" dropdown (shell/CampusPanel)
// and the rings on the map (ProposalRings): data-center mode, a campus of the REPORTED size (capped at the
// engine's 50 GW), dropped at the reported place. Which proposal was picked last is kept here too, so the
// dropdown shows (with its source) the proposal a ring click dropped, and the ring the dropdown picked.

export const MW_MAX = 50000 // the backend takes 1–50,000 MW (engine MW_MAX)

let picked = '' // the id of the proposal picked last ('' = none)
const subs = new Set()
const subscribe = (f) => {
  subs.add(f)
  return () => subs.delete(f)
}
export const setPickedProposal = (id) => {
  const next = id || ''
  if (next === picked) return
  picked = next
  subs.forEach((f) => f())
}
export const usePickedProposal = () => useSyncExternalStore(subscribe, () => picked, () => '')

// The site on the map is this proposal's reported place (the store keeps the exact lat/lon it was given).
export const isDroppedAt = (site, e) => !!site && !!e && Math.abs(site.lat - e.lat) < 1e-6 && Math.abs(site.lon - e.lon) < 1e-6

// (entry {id, lat, lon, mw}) => drops a campus of its reported size at its reported place.
export function useDropProposal() {
  const { setMode, setMw, place } = useOverload()
  return useCallback(
    (e) => {
      if (!e || !Number.isFinite(e.lat) || !Number.isFinite(e.lon) || !(e.mw > 0)) return
      setPickedProposal(e.id)
      setMode('campus')
      setMw(Math.min(Math.round(e.mw), MW_MAX))
      place(e.lat, e.lon)
    },
    [setMode, setMw, place],
  )
}

// What the page says about a proposal, in the sources' own words (NO DEFAMATION): the status as reported
// ("paused", "rejected by the county"), never the catalog's bucket ("paused/canceled"), and the size as
// reported ("up to 1,000 MW") where it differs from the plain number the test uses.
export const statusOf = (e) => e?.status_reported || e?.status || 'status not reported'
export const sizeOf = (e) => e?.mw_reported || `${fmt(e?.mw)} MW`

// "https://www.fox13news.com/news/…" -> "fox13news.com" (who reported it, as a reader would name the site)
export function sourceHost(url) {
  try {
    return new URL(url).hostname.replace(/^www\./, '')
  } catch {
    return 'source'
  }
}
