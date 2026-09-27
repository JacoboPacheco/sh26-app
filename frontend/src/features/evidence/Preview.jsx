// #/preview/evidence (dev only): "How we know" over the live map, open, for the case on the map, with the headline
// numbers' formulas ("The case has its fix in it" judges it as the results panel does after the flip: applied); a
// catastrophe preset as the incident solution stage mounts it; and the capacity formula from the Strengthen study when
// the server has one (a peek: it never starts a study).
import { useEffect, useState } from 'react'
import { useOverload } from '../../store'
import { Button } from '../../ui'
import { getUnlockJob, peekUnlock } from '../unlock/unlockApi'
import HowWeKnow, { Formula } from './HowWeKnow'

const CASES = [
  { id: 'hero', label: 'Fort Myers · 1,500 MW', lat: 26.6406, lon: -81.8723, mw: 1500 },
  { id: 'calm', label: 'Fort Myers · 500 MW', lat: 26.6406, lon: -81.8723, mw: 500 },
  { id: 'orl', label: 'Orlando · 500 MW', lat: 28.5384, lon: -81.3789, mw: 500 },
]

function useCapacity(region) {
  const [cap, setCap] = useState(null)
  useEffect(() => {
    let live = true
    peekUnlock({ region, mw: 1000 })
      .then((p) => (p.state === 'done' && p.id ? getUnlockJob(p.id) : null))
      .then((j) => live && j?.result?.capacity && setCap(j.result.capacity))
      .catch(() => {})
    return () => {
      live = false
    }
  }, [region])
  return cap
}

export default function Preview() {
  const { region, setRegion, setMw, place, caseBody, cascade, startCascade, cascading, resetAll } = useOverload()
  const cap = useCapacity(region === 'US' ? 'FL' : region)
  const [applied, setApplied] = useState(false)
  const pick = (c) => {
    if (region !== 'FL') {
      setRegion('FL', { place: [c.lat, c.lon], mw: c.mw })
      return
    }
    setMw(c.mw)
    place(c.lat, c.lon)
  }
  return (
    <div className="stack">
      <div className="row" role="group" aria-label="Try a case">
        {CASES.map((c) => (
          <Button key={c.id} variant="secondary" onClick={() => pick(c)}>
            {c.label}
          </Button>
        ))}
        <Button variant="secondary" onClick={() => startCascade()} busy={cascading}>
          Run the cascade
        </Button>
        <Button variant="secondary" onClick={resetAll}>
          Start over
        </Button>
        <label className="hwk-preview__check">
          <input type="checkbox" checked={applied} onChange={(e) => setApplied(e.target.checked)} /> The case has its fix in it
        </label>
      </div>
      <div className="hwk-preview">
        {/* as the results column mounts it: the cost comes from the map case's shared estimate */}
        <HowWeKnow body={caseBody} applied={applied} figures={cascade ? ['people_hit', 'cost', 'outage_hours'] : ['cost', 'outage_hours']} cascade={cascade} defaultOpen />
      </div>
      <div className="hwk-preview">
        {/* as the incident solution stage mounts it: a catastrophe preset instead of the storm's line ids */}
        <HowWeKnow body={{ region: 'FL', preset: 'fl-gulf-fort-myers' }} label="How we know (catastrophe preset: Gulf landfall)" />
      </div>
      {cap && (
        <div className="hwk-preview">
          <Formula kind="capacity" capacity={cap} />
        </div>
      )}
    </div>
  )
}
