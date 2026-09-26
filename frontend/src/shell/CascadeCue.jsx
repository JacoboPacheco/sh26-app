import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from 'react'
import { createPortal } from 'react-dom'
import { useMapView } from '../GridMap'
import { useOverload } from '../store'

// "Run the cascade" beside the data center just dropped (user, Sat 19:05: a first-time viewer reads the
// summary and never finds the button in the bottom bar). It sits next to the campus dot while the site is
// over its limit and nothing has run yet; while it is on screen the bottom bar hides its own button, so
// there is only ever one. Panned out of view, the bottom bar's comes back.
//
// The map is one SVG marked role="img" whose pointer-up drops a campus, so the button can't live inside
// it: an invisible anchor rides the camera inside the SVG and the button is an HTML layer over the map
// that follows the anchor's screen position (one rect read per frame, only while it is showing).

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
const FLIP_AT = 230 // px: nearer the right edge than this, the button goes on the dot's left

export default function CascadeCue() {
  const { site, result, cascade, cascading, mapTool, fx, startCascade } = useOverload()
  const { project } = useMapView()
  const want = !!(site && result?.overloaded?.length && !cascade && !cascading && !mapTool && !fx)
  const anchor = useRef(null)
  const [host, setHost] = useState(null)
  const [pos, setPos] = useState(null)
  const attach = useCallback((el) => {
    anchor.current = el
    setHost(el?.ownerSVGElement?.parentElement || null)
  }, [])

  // follow the dot through pans, zooms and camera eases
  useEffect(() => {
    if (!want || !host) return undefined
    let raf = 0
    let last = ''
    const tick = () => {
      const a = anchor.current?.getBoundingClientRect()
      const h = host.getBoundingClientRect()
      if (a && h.width) {
        const x = a.left + a.width / 2 - h.left
        const y = a.top + a.height / 2 - h.top
        const inside = x > GAP && x < h.width - GAP && y > GAP && y < h.height - GAP
        const flip = x > h.width - FLIP_AT
        const key = inside ? `${Math.round(x)},${Math.round(y)},${flip}` : 'out'
        if (key !== last) {
          last = key
          setPos(inside ? { x, y, flip } : null)
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
            className={`cascade-cue${pos.flip ? ' cascade-cue--flip' : ''}`}
            style={{ left: pos.x, top: pos.y }}
            onPointerDown={stop}
            onPointerMove={stop}
            onPointerUp={stop}
            onClick={stop}
            onDoubleClick={stop}
          >
            <button
              type="button"
              className="btn cascade-cue__btn"
              onClick={(e) => {
                e.stopPropagation()
                startCascade()
              }}
            >
              Run the cascade
            </button>
          </div>,
          host,
        )}
    </>
  )
}
