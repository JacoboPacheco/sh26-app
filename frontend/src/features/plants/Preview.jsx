// #/preview/plants — Plant Down over the live map, before it has a mode of its own. The panel
// renders here; the map layer is portaled into the map's camera (it can't be a GridMap child from
// here), with the zoom read off the camera's transform. Mounted for real, PlantsLayer is a GridMap
// child and PlantsPanel the "Plants" mode's panel.
import { useEffect, useMemo, useState } from 'react'
import { createPortal } from 'react-dom'
import { project } from '../../geo'
import PlantsLayer from './PlantsLayer'
import PlantsPanel from './PlantsPanel'

export default function Preview() {
  return (
    <div className="stack">
      <PlantsPanel />
      <MapPortal />
    </div>
  )
}

// The map's camera group and its current zoom, found once the grid has loaded (and found again
// if the map remounts, e.g. on a region change).
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
  return cam ? createPortal(<PlantsLayer view={view} always />, cam) : null
}
