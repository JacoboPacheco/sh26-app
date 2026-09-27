// #/preview/hospitals — hospitals on backup power over the live map, before they're mounted.
// The list renders in the right-hand column (where it belongs: who is affected), the crosses are
// portaled into the map's camera (they can't be GridMap children from here), and this panel
// compares the list with the engine's own answer (POST /api/hospitals/backup) once a cascade ends.
// Mounted for real: <HospitalsLayer /> is a GridMap child and <HospitalsList /> sits in the panel.
// Below them: the hospital beds agent (HospitalAgentStarter starts it when the cascade lands; HospitalsFound is the
// panel the presentation's hospitals beat shows).
import { useEffect, useMemo, useState } from 'react'
import { createPortal } from 'react-dom'
import { project } from '../../geo'
import { useOverload } from '../../store'
import { Badge, Button, ErrorBanner } from '../../ui'
import { backupFor, useHospitalStatus } from './hospitalsApi'
import HospitalsLayer from './HospitalsLayer'
import HospitalsList from './HospitalsList'
import HospitalAgentStarter from './HospitalAgentStarter'
import HospitalsFound from './HospitalsFound'

export default function Preview() {
  const right = useElement('.mc-right')
  const [es, setEs] = useState(false)
  return (
    <div className="stack">
      <p className="muted">
        Crosses on the map are hospitals (OpenStreetMap). Drop a data center and run the cascade: hospitals whose area goes dark turn
        red. The list is in the right-hand column.
      </p>
      <EngineCheck />
      {right ? createPortal(<div className="panel-body"><HospitalsList /></div>, right) : <HospitalsList />}
      <MapPortal />
      <HospitalAgentStarter />
      <label className="row">
        <input type="checkbox" checked={es} onChange={(e) => setEs(e.target.checked)} /> The agent's panel in Spanish
      </label>
      <HospitalsFound lang={es ? 'es' : 'en'} animate />
    </div>
  )
}

// The list's counts next to the engine's for the same case, once a replay has ended.
function EngineCheck() {
  const o = useOverload()
  const st = useHospitalStatus()
  const [res, setRes] = useState({ key: null, data: null, error: null })
  const done = !!o?.cascade && o.step >= o.cascade.steps.length
  const key = done ? JSON.stringify(o.caseBody) : null
  const check = () =>
    backupFor(o.caseBody).then(
      (data) => setRes({ key, data, error: null }),
      (error) => setRes({ key, data: null, error }),
    )
  if (!done) return <p className="muted">Engine check: run a cascade to compare the list with the engine.</p>
  const mine = res.key === key ? res : { data: null, error: null }
  const same = mine.data && st.ready && mine.data.counts.backup === st.counts.backup && mine.data.counts.strained === st.counts.strained
  return (
    <div className="row">
      <Button variant="secondary" onClick={check}>
        Check with the engine
      </Button>
      {mine.data && (
        <span>
          Engine: {mine.data.counts.backup} on backup, {mine.data.counts.strained} partial.{' '}
          <Badge tone={same ? 'neutral' : 'warn'}>{same ? 'Matches the list' : 'Differs from the list'}</Badge>
        </span>
      )}
      <ErrorBanner error={mine.error} />
    </div>
  )
}

function useElement(selector) {
  const [el, setEl] = useState(null)
  useEffect(() => {
    const check = () => {
      const found = document.querySelector(selector)
      setEl((cur) => (cur === found ? cur : found))
    }
    check()
    const t = setInterval(check, 500)
    return () => clearInterval(t)
  }, [selector])
  return el
}

// The map's camera group and its current zoom (read off the camera's transform).
function MapPortal() {
  const [cam, setCam] = useState(null)
  const [k, setK] = useState(1)
  useEffect(() => {
    let obs = null
    let el = null
    const check = () => {
      if (el?.isConnected) return
      obs?.disconnect()
      el = document.querySelector('.map-cam')
      setCam(el)
      if (!el) return
      const cur = el
      const read = () => setK(Number(/scale\(([\d.]+)\)/.exec(cur.style.transform || '')?.[1]) || 1)
      read()
      obs = new MutationObserver(read)
      obs.observe(cur, { attributes: true, attributeFilter: ['style'] })
    }
    check()
    const t = setInterval(check, 300)
    return () => {
      clearInterval(t)
      obs?.disconnect()
    }
  }, [])
  const view = useMemo(() => ({ k, project }), [k])
  return cam ? createPortal(<HospitalsLayer view={view} />, cam) : null
}
