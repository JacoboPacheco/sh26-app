import { useEffect, useMemo, useState } from 'react'
import { useOverload } from '../../store'
import { ErrorBanner, Loading } from '../../ui'
import ReviewStage from './ReviewStage'
import { presetRegion } from './briefingApi'
import { HERO } from './stage'

// #/next/brief/<id>: the review stage as a page of the app. id = hero | preset:<catastrophe id>.
// The case's cascade is loaded into the map, paused, and the stage opens on it.
function readId() {
  const m = window.location.hash.match(/^#\/next\/brief\/([^/?#]+)/)
  return m ? decodeURIComponent(m[1]) : 'hero'
}

export default function BriefRoute({ id: idProp, onClose }) {
  const o = useOverload()
  const [hashId, setHashId] = useState(readId)
  useEffect(() => {
    const on = () => setHashId(readId())
    window.addEventListener('hashchange', on)
    return () => window.removeEventListener('hashchange', on)
  }, [])
  const id = idProp ?? hashId
  // saved scenarios were removed (Audit: dead code and leftovers); a numeric id never resolves now
  const body = useMemo(() => {
    if (id === 'hero') return HERO
    if (id.startsWith('preset:')) return { region: presetRegion(id.slice(7)), preset: id.slice(7) }
    return null
  }, [id])

  // the case's state first (a saved scenario can be in another state)
  const wrongRegion = !!body?.region && o.region !== body.region
  const { setRegion } = o
  useEffect(() => {
    if (wrongRegion) setRegion(body.region)
  }, [wrongRegion, body, setRegion])

  if (!body) return <ErrorBanner error={new Error(`No briefing called "${id}"`)} />
  if (!o.grid || wrongRegion || o.grid.meta?.region !== body.region) return <Loading label="Loading the grid…" />
  return <ReviewStage key={id} body={body} loadReplay onClose={onClose || (() => (window.location.hash = '#/next'))} />
}
