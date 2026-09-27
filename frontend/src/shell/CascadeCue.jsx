import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from 'react'
import { createPortal } from 'react-dom'
import { useMapView } from '../GridMap'
import { useOverload } from '../store'
import RunCascadeButton from './RunCascadeButton'

// "Run the cascade" beside the data center just dropped (user, Sat 19:05: a first-time viewer reads the
// summary and never finds the button in the bottom bar). It sits next to the campus dot while the site is
// over its limit and nothing has run yet; while it is on screen the bottom bar hides its own button, so
// there is only ever one. Panned out of view, the bottom bar's comes back.
//
// The map is one SVG marked role="img" whose pointer-up drops a campus, so the button can't live inside
// it: an invisible anchor rides the camera inside the SVG and the button is an HTML layer over the map
// that follows the anchor's screen position (one rect read per frame, only while it is showing).
//
// It sits on whichever side of the dot is clear: right, left, below or above, never over a town's name
// (user, Sat 23:38) or a panel, and it keeps its side while that side stays clear so it doesn't hop while
// the map pans. Pressing it throws the breaker's lever for at least HOLD_MS before the bottom bar takes over.

let shown = false
const subs = new Set()
function setShown(v) {
  if (v === shown) return
  shown = v
  subs.forEach((f) => f())
}
const subscribe = (f) => {
  subs.add(f)
  return () => subs.delete(f)
}
/** true while the cue is on screen (the bottom bar hides its own "Run the cascade" then) */
export function useCascadeCueShown() {
  return useSyncExternalStore(subscribe, () => shown)
}

const stop = (e) => e.stopPropagation()
const GAP = 12 // px of map kept clear around the edges
const HOLD_MS = 320 // the lever's throw (200 ms) stays visible at least this long after a press
const SIDES = ['right', 'left', 'below', 'above'] // the order tried
const LABEL_WEIGHT = 8 // covering a town's name is worse than covering empty sea
const PANELS = '.appbar, .mc-left, .mc-right, .mc-bottom, .mc-top > *, .map-controls, .mc-credit'
const NARROW = 640 // px: a map narrower than this gets the plain button (no sentence, no key hint)

const rectIn = (el, host, pad = 0) => {
  const r = el.getBoundingClientRect()
  return { l: r.left - host.left - pad, t: r.top - host.top - pad, r: r.right - host.left + pad, b: r.bottom - host.top + pad }
}
const overlap = (a, b) => Math.max(0, Math.min(a.r, b.r) - Math.max(a.l, b.l)) * Math.max(0, Math.min(a.b, b.b) - Math.max(a.t, b.t))

// where each side would put the button (top-left corner, in host pixels) for a dot at (x, y), the button W x H,
// and `d` from the dot's centre to the button's near edge
function boxes(x, y, W, H, d) {
  return {
    right: { l: x + d, t: y - H / 2 },
    left: { l: x - d - W, t: y - H / 2 },
    below: { l: x - W / 2, t: y + d },
    above: { l: x - W / 2, t: y - d - H },
  }
}

export default function CascadeCue() {
  const { site, result, cascade, cascading, mapTool, fx, startCascade } = useOverload()
  const { project } = useMapView()
  const [thrown, setThrown] = useState(false) // pressed: the lever stays over until the run has started
  const heldUntil = useRef(0)
  const idle = !cascade && !cascading && !fx
  const want = !!(site && result?.overloaded?.length && !mapTool && (idle || thrown))
  const anchor = useRef(null)
  const button = useRef(null)
  const [host, setHost] = useState(null)
  const [pos, setPos] = useState(null)
  const attach = useCallback((el) => {
    anchor.current = el
    setHost(el?.ownerSVGElement?.parentElement || null)
  }, [])

  const run = useCallback(() => {
    heldUntil.current = performance.now() + HOLD_MS
    setThrown(true)
    startCascade()
  }, [startCascade])

  // after the run has started (the request answered), give the lever its full throw before the cue leaves
  useEffect(() => {
    if (!thrown || cascading) return undefined
    const left = heldUntil.current - performance.now()
    if (left <= 0) {
      // oxlint-disable-next-line react/set-state-in-effect
      setThrown(false)
      return undefined
    }
    const t = setTimeout(() => setThrown(false), left)
    return () => clearTimeout(t)
  }, [thrown, cascading])

  // follow the dot through pans, zooms and camera eases, and pick the side that is clear
  useEffect(() => {
    if (!want || !host) return undefined
    let raf = 0
    let last = ''
    let side = 'right'
    const tick = () => {
      const a = anchor.current?.getBoundingClientRect()
      const h = host.getBoundingClientRect()
      if (a && h.width) {
        const x = a.left + a.width / 2 - h.left
        const y = a.top + a.height / 2 - h.top
        const inside = x > GAP && x < h.width - GAP && y > GAP && y < h.height - GAP
        let key = 'out'
        let next = null
        if (inside) {
          const narrow = h.width < NARROW
          // the pulsing ring is 16 map units around the dot's 6
          const dot = host.querySelector('.site--primary .site-dot')?.getBoundingClientRect()
          const ringR = dot && dot.width ? Math.min(40, Math.max(8, (dot.width / 2) * (16 / 6))) : 20
          const off = ringR + 4 // the leader starts just outside the ring
          const b = button.current?.getBoundingClientRect()
          const W = b?.width || (narrow ? 200 : 330)
          const H = b?.height || (narrow ? 46 : 58)
          const at = boxes(x, y, W, H, off + 16)
          const labels = [...host.querySelectorAll('.map-city')].map((el) => rectIn(el, h, 4))
          const panels = [...document.querySelectorAll(PANELS)].map((el) => rectIn(el, h))
          const cost = (s) => {
            const r = { l: at[s].l, t: at[s].t, r: at[s].l + W, b: at[s].t + H }
            let c = 0
            if (r.l < GAP || r.t < GAP || r.r > h.width - GAP || r.b > h.height - GAP) c += 1e7
            for (const l of labels) c += LABEL_WEIGHT * overlap(r, l)
            for (const p of panels) c += overlap(r, p)
            return c
          }
          // keep the side while it stays clear (no hopping while panning); otherwise the first clear one, else the least bad
          if (cost(side) > 0) {
            let best = SIDES[0]
            let bestCost = Infinity
            for (const s of SIDES) {
              const c = cost(s)
              if (c < bestCost) {
                best = s
                bestCost = c
              }
            }
            side = best
          }
          next = { x, y, side, off, narrow }
          key = `${Math.round(x)},${Math.round(y)},${side},${Math.round(off)},${narrow}`
        }
        if (key !== last) {
          last = key
          setPos(next)
        }
      }
      raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [want, host])

  const visible = want && !!host && !!pos
  useEffect(() => {
    setShown(visible)
    return () => setShown(false)
  }, [visible])

  // Enter runs it when nothing else has the keyboard (the hint on the button says so)
  const ready = visible && !thrown
  useEffect(() => {
    if (!ready) return undefined
    const onKey = (e) => {
      if (e.key !== 'Enter' || e.repeat || e.defaultPrevented || e.ctrlKey || e.metaKey || e.altKey || e.shiftKey) return
      const active = document.activeElement
      if (active && active !== document.body) return // a control has focus: Enter belongs to it
      if (document.querySelector('[role="dialog"], [aria-modal="true"], dialog[open]')) return
      e.preventDefault()
      run()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [ready, run])

  if (!site) return null
  // the dot is drawn at the substation it connects to once the case is solved (App.jsx `sites`)
  const [x, y] = project(result?.sub_lon ?? site.lon, result?.sub_lat ?? site.lat)
  return (
    <>
      <g ref={attach} transform={`translate(${x} ${y})`} aria-hidden="true">
        <circle r={0.01} fill="none" />
      </g>
      {visible &&
        createPortal(
          // React bubbles a portal's events through its React parents, and one of them is the map's SVG, whose
          // pointer-down captures the pointer (so the button would never get its click) and whose pointer-up
          // drops a campus: stop them here
          <div
            className={`run-cue run-cue--${pos.side}`}
            style={{ left: pos.x, top: pos.y, '--off': `${pos.off}px` }}
            onPointerDown={stop}
            onPointerMove={stop}
            onPointerUp={stop}
            onClick={stop}
            onDoubleClick={stop}
          >
            <span ref={button} className="run-cue__box">
              <RunCascadeButton
                state={thrown ? 'running' : 'idle'}
                detail={!pos.narrow}
                className="cascade-cue__btn"
                onClick={(e) => {
                  e.stopPropagation()
                  run()
                }}
              />
            </span>
          </div>,
          host,
        )}
    </>
  )
}
