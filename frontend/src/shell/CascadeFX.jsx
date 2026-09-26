import { useMemo } from 'react'
import { useMapView } from '../GridMap'
import { stepMsFor, useOverload } from '../store'

// Smooth destruction. While a cascade plays, every failure is scheduled ONCE as a CSS animation
// timed inside its step's interval: tripped lines flash white-hot and die, and the lights around
// each failure flicker out in a wave that spreads outward from it (nearest first), with darkness
// rolling in behind. The browser animates it all, so the blackout sweeps across the map instead of
// landing in 600 ms chunks. Paused or scrubbed, this layer is off and the map shows the exact step.
export default function CascadeFX() {
  const { cascade, playing, step, subById, branchById } = useOverload()
  const { k, project } = useMapView()
  // the step playback started from (captured when play begins; resuming mid-way schedules only what's ahead)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const start = useMemo(() => step, [playing, cascade])

  const fx = useMemo(() => {
    if (!cascade?.steps?.length) return null
    const STEP_MS = stepMsFor(cascade.steps.length)
    const lines = []
    const subs = []
    cascade.steps.forEach((st, j) => {
      const stepNo = j + 1 // the store shows steps[j] when step === j + 1
      if (stepNo <= start) return
      const t0 = (stepNo - 1 - start) * STEP_MS // this step's interval starts here…
      // the failure's center: the midpoint of the lines that tripped this step
      const mids = []
      st.tripped.forEach((bid, i) => {
        const b = branchById.get(bid)
        const a = b && subById.get(b.from_sub)
        const z = b && subById.get(b.to_sub)
        if (!a || !z) return
        const [x1, y1] = project(a.lon, a.lat)
        const [x2, y2] = project(z.lon, z.lat)
        mids.push([(x1 + x2) / 2, (y1 + y2) / 2])
        // storms knock out many lines at once: stagger them across the interval
        const lag = st.tripped.length > 1 ? (i / st.tripped.length) * STEP_MS * 0.8 : 0
        lines.push({ key: `${stepNo}-${bid}`, x1, y1, x2, y2, t: t0 + lag })
      })
      const cx = mids.length ? mids.reduce((s, m) => s + m[0], 0) / mids.length : null
      const cy = mids.length ? mids.reduce((s, m) => s + m[1], 0) / mids.length : null
      const ids = new Set([...(st.dark_subs || []), ...(st.newly_affected || []).map(([id]) => id)])
      const pts = []
      ids.forEach((id) => {
        const s = subById.get(id)
        if (!s) return
        const [x, y] = project(s.lon, s.lat)
        pts.push({ id, x, y, d: cx === null ? 0 : Math.hypot(x - cx, y - cy) })
      })
      pts.sort((p, q) => p.d - q.d)
      // …and the lights go out across it, nearest to the failure first
      pts.forEach((p, i) => subs.push({ key: `${stepNo}-${p.id}`, x: p.x, y: p.y, t: t0 + STEP_MS * 0.15 + (i / Math.max(pts.length, 1)) * STEP_MS * 0.8 }))
    })
    return { lines, subs }
  }, [cascade, start, subById, branchById, project])

  if (!playing || !fx) return null
  const r = 8 / Math.sqrt(Math.max(k, 1))
  return (
    <g className="fx" pointerEvents="none" aria-hidden="true">
      {fx.subs.map((s) => (
        <g key={s.key} transform={`translate(${s.x} ${s.y})`}>
          <circle className="fx-dark" r={r} fill="url(#blackout)" style={{ animationDelay: `${s.t}ms` }} />
          <circle className="fx-flicker" r={r / 3.2} style={{ animationDelay: `${Math.max(0, s.t - 160)}ms` }} />
        </g>
      ))}
      {fx.lines.map((l) => (
        <line key={l.key} className="fx-trip" x1={l.x1} y1={l.y1} x2={l.x2} y2={l.y2} style={{ animationDelay: `${l.t}ms` }} />
      ))}
    </g>
  )
}
