// #/preview/component3d — the 3D panel's four kinds side by side (a line, a transformer, a storm, a
// held line), with controls to scrub the loading and the time around the trip, and a replay of the
// recorded hero cascade (Fort Myers, 1,500 MW) through the real panel logic on a fake store clock,
// so the panel's sync can be checked with no backend. Mounted for real: <ComponentPanel /> in
// App.jsx's MissionControl (it reads the store's cascade, fx and step).
// #/preview/component3d?live — instead mounts the real, store-connected panel in the page (as the
// mount line will), so a cascade run on the live map shows it where it will live.
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { buildSchedule } from '../../shell/cascadeSchedule'
import ComponentPanel, { ComponentPanelView, PanelFrame } from './ComponentPanel'
import { drawScene, fitCanvas, readPalette, readout } from './render3d'
import { RELAY_PCT, elementForStep } from './shots'
import fixture from './heroFixture.json'
import './component3d.css'

const KVS = [100, 138, 230, 345, 500, 765]
const T_MIN = -1500
const T_MAX = 2600

function useFixture() {
  return useMemo(() => {
    const subById = new Map(fixture.grid.subs.map((s) => [s.id, s]))
    const branchById = new Map(fixture.grid.branches.map((b) => [b.id, b]))
    return { cascade: fixture.cascade, subById, branchById, lookups: { subById, branchById, upgrades: {}, result: null, branchIndex: null } }
  }, [])
}

export default function Preview() {
  return /[?&]live(?:$|&)/.test(window.location.hash) ? <LiveMount /> : <Gallery />
}

// the real panel, portaled into the demo page (the preview box itself is only a note)
function LiveMount() {
  const host = document.querySelector('.mc')
  return (
    <>
      <p className="muted">The 3D panel is mounted in the page: run a cascade.</p>
      {host && createPortal(<ComponentPanel />, host)}
    </>
  )
}

function Gallery() {
  const fx = useFixture()
  const [at, setAt] = useState(156)
  const [kv, setKv] = useState('auto')
  const [u, setU] = useState(-700)
  const [still, setStill] = useState(false)
  const [playingTrip, setPlayingTrip] = useState(false)

  // the four kinds, from the recorded case's own elements (names, kV, lengths); the loading is scrubbed
  const cards = useMemo(() => {
    const steps = fx.cascade.steps
    const find = (id) => steps.findIndex((s) => s.tripped.includes(id))
    const base = (id) => elementForStep(fx.cascade, find(id), fx.lookups)
    const line = base(35006)
    const tf = base(36001)
    const storm = { ...base(35065), key: 'storm', kind: 'storm', at: 64, before: 64, mode: 'thermal', more: 23 }
    const shed = { ...base(34753), key: 'shed', kind: 'shed', before: 92, at: Math.max(101, at), after: 99.5, mode: 'thermal', more: 0 }
    const k = (el) => (kv === 'auto' ? el : { ...el, kv: Number(kv), key: `${el.key}-${kv}` })
    const mode = at > RELAY_PCT ? 'relay' : 'thermal'
    return [
      { title: 'Line: overload', el: k({ ...line, key: `line-${at}`, before: 70, at, mode }) },
      { title: 'Transformer: overload', el: { ...tf, key: `tf-${at}`, before: 77, at, mode } },
      { title: 'Storm: knocked down', el: k(storm) },
      { title: 'Held line: customers cut', el: k(shed) },
    ]
  }, [fx, at, kv])

  // "Play the trip": the time runs from the approach through the trip in real time
  useEffect(() => {
    if (!playingTrip) return undefined
    let raf = 0
    const t0 = performance.now() - (u - T_MIN)
    const loop = (now) => {
      const v = T_MIN + (now - t0)
      if (v >= T_MAX) {
        setU(T_MAX)
        setPlayingTrip(false)
        return
      }
      setU(v)
      raf = requestAnimationFrame(loop)
    }
    raf = requestAnimationFrame(loop)
    return () => cancelAnimationFrame(raf)
  }, [playingTrip]) // eslint-disable-line react-hooks/exhaustive-deps -- starts from the current time once

  const body = (
    <div className="c3dp" data-testid="c3d-preview">
      <div className="c3dp__bar">
        <strong>3D failing element: preview</strong>
        <a href="#/">Close</a>
      </div>
      <div className="c3dp__controls">
        <label>
          Loading at the trip: <b>{Math.round(at)} %</b>
          <input type="range" min={101} max={600} value={at} onChange={(e) => setAt(Number(e.target.value))} aria-label="Loading at the trip" />
        </label>
        <label>
          Time from the trip: <b>{Math.round(u)} ms</b>
          <input type="range" min={T_MIN} max={T_MAX} step={10} value={u} onChange={(e) => (setPlayingTrip(false), setU(Number(e.target.value)))} aria-label="Time from the trip" />
        </label>
        <label>
          Tower (kV)
          <select value={kv} onChange={(e) => setKv(e.target.value)} aria-label="Tower (kV)">
            <option value="auto">From the element</option>
            {KVS.map((v) => (
              <option key={v} value={v}>
                {v} kV
              </option>
            ))}
          </select>
        </label>
        <label className="c3dp__check">
          <input type="checkbox" checked={still} onChange={(e) => setStill(e.target.checked)} /> Reduced motion (still)
        </label>
        <button type="button" className="btn" onClick={() => (setU(T_MIN), setPlayingTrip(true))} data-testid="c3d-trip">
          Play the trip
        </button>
        <span className="muted">Past {RELAY_PCT} % a line trips on its relay (no tree contact).</span>
      </div>
      <div className="c3dp__cards">
        {cards.map((c) => (
          <div key={c.title} className="c3dp__card">
            <p className="c3dp__title">{c.title}</p>
            <Card el={c.el} u={still ? Math.max(u, 1600) : u} A={1400} t={still ? 0 : u / 1000 + 3} />
          </div>
        ))}
      </div>
      <HeroReplay fx={fx} />
    </div>
  )
  return createPortal(body, document.body)
}

// one element drawn at a given time (redrawn whenever the inputs change)
function Card({ el, u, A, t }) {
  const canvasRef = useRef(null)
  const figRef = useRef(null)
  const tagRef = useRef(null)
  const pal = useMemo(() => readPalette(), [])
  useLayoutEffect(() => {
    const cv = canvasRef.current
    if (!cv) return
    const { W, H, dpr } = fitCanvas(cv)
    const st = drawScene(cv.getContext('2d'), W, H, dpr, el, u, A, t, { pal })
    readout(figRef.current, tagRef.current, st)
  })
  return <PanelFrame el={el} canvasRef={canvasRef} figRef={figRef} tagRef={tagRef} />
}

// The recorded hero cascade replayed through the real panel, on a clock that mimics the store's:
// the schedule from shell/cascadeSchedule.js, `step` advancing at each tier's end, pause and scrub.
function HeroReplay({ fx }) {
  const { cascade, subById, branchById, lookups } = fx
  const n = cascade.steps.length
  const [step, setStep] = useState(0)
  const [clock, setClock] = useState(null) // the store's fx while playing
  const playing = !!clock
  const play = () => {
    const from = step >= n ? 0 : step
    if (from !== step) setStep(0)
    window.__c3dFrames = []
    setClock({ schedule: buildSchedule(cascade, subById, branchById, from), startedAt: performance.now(), from })
  }
  // like the store: `step` moves to each tier's step at that tier's end; the last one stops the replay
  useEffect(() => {
    if (!clock) return undefined
    const tiers = clock.schedule.tiers
    const timers = tiers.map((tier, i) =>
      setTimeout(
        () => {
          setStep(tier.step)
          if (i === tiers.length - 1) setClock(null)
        },
        Math.max(0, tier.t1 - (performance.now() - clock.startedAt)),
      ),
    )
    return () => timers.forEach(clearTimeout)
  }, [clock])
  const frames = () => {
    const f = window.__c3dFrames || []
    if (!f.length) return 'no frames yet'
    const s = [...f].sort((a, b) => a - b)
    const mean = f.reduce((a, b) => a + b, 0) / f.length
    return `${f.length} frames · mean ${mean.toFixed(1)} ms · p95 ${s[Math.floor(s.length * 0.95)].toFixed(1)} ms`
  }
  const [stats, setStats] = useState('')
  return (
    <section className="c3dp__replay" aria-label="Hero replay">
      <p className="c3dp__title">Recorded hero cascade (Fort Myers, 1,500 MW, 9 steps): the panel on the store&apos;s clock</p>
      <div className="c3dp__controls">
        <button type="button" className="btn" data-testid="c3d-hero-play" onClick={play}>
          {step >= n ? 'Replay' : 'Play'}
        </button>
        <button type="button" className="btn btn--secondary" data-testid="c3d-hero-pause" onClick={() => setClock(null)}>
          Pause
        </button>
        <label>
          Step {step} of {n}
          <input type="range" min={0} max={n} value={step} onChange={(e) => (setClock(null), setStep(Number(e.target.value)))} aria-label="Step" />
        </label>
        <button type="button" className="btn btn--secondary" data-testid="c3d-hero-stats" onClick={() => setStats(frames())}>
          Frame times
        </button>
        <span className="muted" data-testid="c3d-hero-stats-out">
          {stats}
        </span>
      </div>
      <div className="c3dp__heroStage">
        <ComponentPanelView cascade={cascade} fx={clock} playing={playing} step={step} lookups={lookups} dock="inline" />
      </div>
    </section>
  )
}
