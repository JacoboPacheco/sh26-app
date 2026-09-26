// #/preview/catalog — the data-center catalog over the live map: national totals, the list, a campus's
// card, and every campus as a dot on the map (portaled into the map's camera; mounted for real,
// CatalogLayer is a GridMap child). "Test it in the workspace" drives the real store.
import { useEffect, useMemo, useState } from 'react'
import { createPortal } from 'react-dom'
import { project } from '../../geo'
import { useOverload } from '../../store'
import { Button } from '../../ui'
import CatalogLayer from './CatalogLayer'
import CatalogView from './CatalogView'

export default function Preview() {
  const o = useOverload()
  const national = o?.region === 'US'
  return (
    <div className="stack">
      <div className="cat-preview__bar">
        <Button variant="secondary" aria-pressed={national} onClick={() => o.setRegion(national ? 'FL' : 'US')}>
          {national ? 'Back to Florida' : 'Show every campus on the U.S. map'}
        </Button>
      </div>
      <CatalogView />
      <MapPortal region={o?.region} />
    </div>
  )
}

// The map's camera group and its zoom, found once the grid has loaded (and again after a remount).
function MapPortal({ region }) {
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
  const view = useMemo(() => ({ k, project, region }), [k, region])
  return cam ? createPortal(<CatalogLayer view={view} />, cam) : null
}
