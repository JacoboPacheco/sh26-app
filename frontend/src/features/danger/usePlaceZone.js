import { useCallback } from 'react'
import { useOverload } from '../../store'

// Put the campus on a danger zone: the Data center mode, the size the zones were found for (the
// backend computes them at the nearest 50 MW, 5,000 MW at most), and the zone's own substation, so
// the what-if and the cascade are exactly the case the zone was ranked on. The store flies the
// camera to the site once the what-if lands. `size` null (a stale answer on screen): keep the size.
export function usePlaceZone() {
  const { place, mw, setMw, mode, setMode } = useOverload()
  return useCallback(
    (z, size) => {
      if (mode !== 'campus') setMode('campus')
      if (size && size !== mw) setMw(size)
      place(z.lat, z.lon)
    },
    [place, mw, setMw, mode, setMode],
  )
}
