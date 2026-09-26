// #/preview/town — "Is my area at risk?" over the live map, before the workspace mounts it. The
// panel renders here; the map layers are portaled into the map's camera (they can't be GridMap
// children from here), with the zoom read off the camera's transform. Mounted for real:
//   <GridMap …><AreaLayer /><AreaHover /></GridMap>   and   <AreaFinder /> in a panel.
import { useEffect, useMemo, useState } from 'react'
import { createPortal } from 'react-dom'
import { project } from '../../geo'
import AreaFinder from './AreaFinder'
import AreaHover from './AreaHover'
import AreaLayer from './AreaLayer'

export default function Preview() {
  return (
    <div className="stack">
      <AreaFinder />
      <MapPortal />
    </div>
  )
}

// The map's camera group and its current zoom, found once the grid has loaded (and found again if
// the map remounts).
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
  return cam
    ? createPortal(
        <>
          <AreaLayer view={view} />
          <AreaHover />
        </>,
        cam,
      )
    : null
}
