// #/preview/planner — the siting planner over the live map, before it has a place in the app. The
// panel renders here; the map layer is portaled into the map's camera (it can't be a GridMap child
// from here), with the zoom read off the camera's transform. Mounted for real, PlannerLayer is a
// GridMap child and PlannerPanel a panel of its own.
import { useEffect, useMemo, useState } from 'react'
import { createPortal } from 'react-dom'
import { project } from '../../geo'
import PlannerLayer from './PlannerLayer'
import PlannerPanel from './PlannerPanel'

export default function Preview() {
  return (
    <div className="planner-preview">
      <PlannerPanel />
      <MapPortal />
    </div>
  )
}

// The map's camera group and its current zoom, found once the grid has loaded (and again if the
// map remounts, e.g. on a region change).
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
  return cam ? createPortal(<PlannerLayer view={view} />, cam) : null
}
