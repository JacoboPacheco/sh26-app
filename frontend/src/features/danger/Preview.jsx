// #/preview/danger — danger zones over the live map, before they are mounted. The panel renders here;
// the map layer is portaled into the map's camera (it can't be a GridMap child from a preview), with
// the zoom read off the camera's transform. Mounted for real: <DangerLayer /> is a GridMap child in
// App.jsx and <DangerPanel /> a section of the Data center panel (shell/CampusPanel.jsx).
import { useEffect, useMemo, useState } from 'react'
import { createPortal } from 'react-dom'
import { project } from '../../geo'
import DangerLayer from './DangerLayer'
import DangerPanel from './DangerPanel'

export default function Preview() {
  return (
    <div className="stack dz-preview">
      <DangerPanel />
      <p className="muted dz-note">Preview: in the app the layer mounts as a GridMap child and the panel inside Data center mode.</p>
      <MapPortal />
    </div>
  )
}

// The map's camera group and its current zoom, found once the grid has loaded (and again if the map
// remounts, e.g. on a region change).
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
  return cam ? createPortal(<DangerLayer view={view} />, cam) : null
}
