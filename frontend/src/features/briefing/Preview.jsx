// #/preview/briefing — the review stage on real cases before it is mounted in the app. On load it
// places the hero (Fort Myers, 1,500 MW), runs the cascade and opens the stage. The picker switches
// case: the heat wave, a Gulf storm over the campus, and the hypothetical catastrophes. While the
// engine/writer routes aren't live the stage falls back to a contract-shaped fixture, labeled on screen.
import { useCallback, useEffect, useRef, useState } from 'react'
import { useOverload } from '../../store'
import { Button, ErrorBanner } from '../../ui'
import { getPresets as getStorms, trackHits } from '../hurricane/hurricaneApi'
import ReviewStage from './ReviewStage'
import { getPresets } from './briefingApi'
import { HERO } from './stage'

const CASES = [
  { id: 'hero', label: 'Fort Myers · 1,500 MW', body: HERO },
  { id: 'heat', label: 'Heat wave (x1.04) · 500 MW', body: { ...HERO, mw: 500, load_factor: 1.04 } },
  { id: 'gulf', label: 'Gulf storm + the campus', storm: 'gulf-fort-myers' },
  { id: 'fl-cat5-statewide', label: 'A Category 5 crosses all of Florida', preset: true },
  { id: 'fl-season-20', label: 'Twenty Category 5 storms in one season', preset: true },
  { id: 'fl-heatdome-gulf', label: 'Heat dome + Gulf storm', preset: true },
]

export default function Preview() {
  const o = useOverload()
  const [stage, setStage] = useState(null)
  const [last, setLast] = useState(null) // the stage to reopen
  const [busy, setBusy] = useState(null)
  const [error, setError] = useState(null)
  const [names, setNames] = useState({})
  const oRef = useRef(o)
  useEffect(() => {
    oRef.current = o
  })

  useEffect(() => {
    getPresets('FL')
      .then((r) => setNames(Object.fromEntries((r.presets || []).map((p) => [p.id, p.name]))))
      .catch(() => {})
  }, [])

  const pick = useCallback(async (c, opts = {}) => {
    setError(null)
    setStage(null)
    if (c.preset) {
      const st = { body: { region: 'FL', preset: c.id }, loadReplay: true }
      setLast(st)
      setStage({ ...st, ...opts })
      return
    }
    setBusy(c.id)
    try {
      const O = oRef.current
      let body = c.body
      if (c.storm) {
        const storms = await getStorms()
        const s = storms.presets.find((p) => p.id === c.storm)
        const hits = await trackHits(s.points, s.radius_km)
        body = { ...HERO, trip: hits.trip }
      }
      // the map's case becomes this one, and its cascade runs
      O.setMw(body.mw)
      O.place(body.lat, body.lon)
      O.setLoadFactor(body.load_factor ?? 1)
      O.setTrip(body.trip || [])
      O.setUpgrades({})
      O.setExtraSites([])
      O.setFirm(false)
      await O.startCascade(body)
      setLast({ body })
      setStage({ body, ...opts })
    } catch (err) {
      setError(err)
    } finally {
      setBusy(null)
    }
  }, [])

  // the hero, once the grid is in
  const started = useRef(false)
  const ready = !!o.grid && o.grid.meta?.region === 'FL'
  useEffect(() => {
    if (!ready || started.current) return
    started.current = true
    pick(CASES[0])
  }, [ready, pick])

  return (
    <div className="stack">
      <p className="muted">Pick a case: the review stage opens over the map. Esc closes it.</p>
      <div className="row">
        {CASES.map((c) => (
          <Button key={c.id} variant="secondary" busy={busy === c.id} disabled={!ready} onClick={() => pick(c)}>
            {names[c.id] || c.label}
          </Button>
        ))}
      </div>
      {stage === null && (
        <div className="row">
          <Button onClick={() => (last ? setStage(last) : pick(CASES[0]))} disabled={!ready}>
            Open the review stage
          </Button>
          <Button variant="secondary" onClick={() => (last ? setStage({ ...last, autoPlay: true }) : pick(CASES[0], { autoPlay: true }))} disabled={!ready}>
            Play briefing
          </Button>
        </div>
      )}
      <ErrorBanner error={error} />
      {stage && <ReviewStage {...stage} allowFixture onClose={() => setStage(null)} />}
    </div>
  )
}
