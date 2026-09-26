import { useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import { project } from '../../geo'
import { useOverload } from '../../store'

// What the briefing draws on the live map while the stage is open: the line being talked about
// (bright), the fix (upgraded lines, or the ghost of the moved campus, in green), the lines each
// restoration wave rebuilds (green), and a ring on the area being named. Drawn inside the map's
// camera group (portaled into `.map-cam`), so it pans and zooms with the map.
//   lines: [{id, tone: 'hl' | 'fix'}], ghost: {lat, lon} | null, rings: [{center: [lon, lat], key}]
export default function MapOverlay({ lines = [], ghost = null, rings = [] }) {
  const { branchById, subPos } = useOverload()
  const { cam, k } = useCamera()
  if (!cam) return null
  const w = 1 / Math.max(k, 0.5)
  return createPortal(
    <g className="rs-map" aria-hidden="true">
      {lines.map(({ id, tone }) => {
        const b = branchById.get(Number(id))
        const a = b && subPos(b.from_sub)
        const z = b && subPos(b.to_sub)
        if (!a || !z) return null
        const [x1, y1] = project(a[0], a[1])
        const [x2, y2] = project(z[0], z[1])
        if (x1 === x2 && y1 === y2) {
          // a transformer: both ends at one substation
          return <circle key={`${tone}${id}`} className={`rs-map__xf rs-map__xf--${tone}`} cx={x1} cy={y1} r={9 * w} strokeWidth={2.5 * w} />
        }
        return <line key={`${tone}${id}`} className={`rs-map__ln rs-map__ln--${tone}`} x1={x1} y1={y1} x2={x2} y2={y2} strokeWidth={(tone === 'hl' ? 4 : 3.2) * w} />
      })}
      {ghost &&
        (() => {
          const [x, y] = project(ghost.lon, ghost.lat)
          return <circle className="rs-map__ghost" cx={x} cy={y} r={12 * w} strokeWidth={2 * w} strokeDasharray={`${4 * w} ${3 * w}`} />
        })()}
      {rings.map((r) => {
        const [x, y] = project(r.center[0], r.center[1])
        return <circle key={r.key} className="rs-map__ring" cx={x} cy={y} r={22 * w} strokeWidth={2 * w} />
      })}
    </g>,
    cam,
  )
}

// The map's camera group and its current zoom (read off its transform), found once the grid has
// loaded and found again if the map remounts.
function useCamera() {
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
      const read = () => setK(Number(/scale\(([\d.]+)\)/.exec(cur.style.transform || cur.getAttribute('transform') || '')?.[1]) || 1)
      read()
      obs = new MutationObserver(read)
      obs.observe(cur, { attributes: true, attributeFilter: ['style', 'transform'] })
    }
    check()
    const t = setInterval(check, 400)
    return () => {
      clearInterval(t)
      obs?.disconnect()
    }
  }, [])
  return { cam, k }
}
